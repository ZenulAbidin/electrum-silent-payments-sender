"""Qt entry point for unified Electrum Send-tab Silent Payments support."""

from PyQt6.QtCore import QTimer

from electrum import constants
from electrum.i18n import _
from electrum.plugin import BasePlugin, hook
from electrum.transaction import PartialTxOutput

from .core import (
    PLACEHOLDER_SCRIPT,
    SilentPaymentError,
    expected_hrp,
    is_silent_payment_request_text,
    parse_silent_payment_request,
)
from .txflow import (
    annotate_silent_payment_output,
    annotate_silent_payment_output_addresses,
    confirm_transaction_compat,
    finalize_transaction,
    get_silent_payment_coins,
    make_unsigned_silent_transaction,
    normalize_silent_payment_records,
    seal_after_confirmation,
    silent_payment_history_label,
    validate_wallet,
    verify_transaction,
)

SILENT_PAYMENT_RECORDS_KEY = "silent_payments_sender_records"


def _payto_text(send_tab) -> str:
    """Read Electrum's single-recipient Pay-to field across supported versions."""
    return send_tab.payto_e.line_edit.text().strip()


def _store_silent_payment_record(
    wallet,
    *,
    txid: str,
    recipient_address: str,
    derived_address: str,
) -> None:
    records = normalize_silent_payment_records(
        wallet.db.get(SILENT_PAYMENT_RECORDS_KEY, {})
    )
    records[str(txid)] = {
        "recipient_address": str(recipient_address),
        "derived_address": str(derived_address),
    }
    wallet.db.put(SILENT_PAYMENT_RECORDS_KEY, records)
    wallet.save_db()


def _get_silent_payment_record(wallet, txid: str):
    records = wallet.db.get(SILENT_PAYMENT_RECORDS_KEY, {})
    if not isinstance(records, dict):
        return None
    record = records.get(txid)
    if not isinstance(record, dict):
        return None
    recipient = record.get("recipient_address")
    derived = record.get("derived_address")
    if not isinstance(recipient, str) or not isinstance(derived, str):
        return None
    return recipient, derived


class Plugin(BasePlugin):

    @hook
    def init_menubar(self, window):
        """Install into the existing Send tab after Electrum has constructed it."""
        self._integrate_send_tab(window)

    @hook
    def transaction_dialog_update(self, dialog):
        """Restore full multiline Silent Payment output details after a restart."""
        txid = dialog.tx.txid()
        if not txid:
            return
        record = _get_silent_payment_record(dialog.wallet, txid)
        if record is None:
            return
        recipient_address, derived_address = record
        for output_index, output in enumerate(dialog.tx.outputs()):
            if output.address == derived_address:
                annotate_silent_payment_output_addresses(
                    dialog.tx,
                    output_index=output_index,
                    recipient_address=recipient_address,
                    full=True,
                )
                dialog.io_widget.update(dialog.tx)
                return

    def _integrate_send_tab(self, window) -> None:
        send_tab = window.send_tab
        if getattr(send_tab, "_silent_payments_sender_integrated", False):
            return
        send_tab._silent_payments_sender_integrated = True

        original_pay = send_tab.send_button.func
        original_try_payment_identifier = send_tab.payto_e.try_payment_identifier

        def schedule_refresh(*_args):
            QTimer.singleShot(0, lambda: self._refresh_send_tab(send_tab))

        def try_payment_identifier(text):
            if is_silent_payment_request_text(text):
                send_tab.payto_e.setText(text.strip())
                schedule_refresh()
                return
            return original_try_payment_identifier(text)

        def pay_dispatch(*_args):
            text = _payto_text(send_tab)
            if is_silent_payment_request_text(text):
                self._send_from_send_tab(send_tab, text)
                return
            original_pay()

        # Electrum's Paste/QR entry paths call this method dynamically. Accepting
        # Silent Payment text here avoids Electrum's native address parser rejecting it.
        send_tab.payto_e.try_payment_identifier = try_payment_identifier

        # EnterButton stores the callback separately for keyboard activation.
        try:
            send_tab.send_button.clicked.disconnect(original_pay)
        except (TypeError, RuntimeError):
            pass
        send_tab.send_button.clicked.connect(pay_dispatch)
        send_tab.send_button.func = pay_dispatch

        send_tab.payto_e.textChanged.connect(schedule_refresh)
        send_tab.payto_e.paymentIdentifierChanged.connect(schedule_refresh)
        send_tab.amount_e.textChanged.connect(schedule_refresh)
        schedule_refresh()

    def _refresh_send_tab(self, send_tab) -> None:
        text = _payto_text(send_tab)
        active = is_silent_payment_request_text(text)
        was_active = getattr(send_tab, "_silent_payments_sender_active", False)
        send_tab._silent_payments_sender_active = active

        if not active:
            if was_active:
                send_tab.send_button.setToolTip("")
                send_tab.payto_e.setToolTip("")
                # Match the native unfrozen field without calling update_fields()
                # while payment_identifier can still be None. Explicitly restore
                # the palette base color: clearing a Qt stylesheet can leave the
                # previous validation color painted until another style change.
                send_tab.payto_e.setStyleSheet(
                    "QWidget { background-color: palette(base); }"
                )
                # Do not call update_fields() here. Electrum's do_clear()
                # clears the text before setting payment_identifier to None,
                # so this queued refresh can run in that intermediate state.
                # Electrum's own text/payment-identifier handlers restore the
                # native field state for ordinary recipients.
            return

        send_tab.save_button.setEnabled(False)
        send_tab.max_button.setChecked(False)
        send_tab.max_button.setEnabled(False)
        send_tab.send_button.setToolTip(
            _("Create a BIP352 Silent Payment using the entered amount.")
        )

        hrp = expected_hrp(is_testnet=constants.net.TESTNET)
        try:
            request = parse_silent_payment_request(text, expected_hrp=hrp)
            if request.amount_sat is not None and send_tab.amount_e.get_amount() is None:
                send_tab.amount_e.setAmount(request.amount_sat)
            amount_sat = send_tab.amount_e.get_amount()
            if amount_sat is None:
                amount_sat = request.amount_sat
            amount_valid = isinstance(amount_sat, int) and amount_sat > 0
            send_tab.set_field_validated(send_tab.payto_e, validated=True)
            send_tab.payto_e.setToolTip(
                _("Recognized BIP352 Silent Payment address.")
            )
            send_tab.invoice_error.setText(
                "" if amount_valid else _("Enter an amount")
            )
            send_tab.send_button.setEnabled(amount_valid)
        except SilentPaymentError as exc:
            send_tab.set_field_validated(send_tab.payto_e, validated=False)
            send_tab.payto_e.setToolTip(str(exc))
            send_tab.invoice_error.setText(str(exc))
            send_tab.send_button.setEnabled(False)

    def _send_from_send_tab(self, send_tab, text: str) -> None:
        window = send_tab.window
        try:
            validate_wallet(window.wallet)
            request = parse_silent_payment_request(
                text,
                expected_hrp=expected_hrp(is_testnet=constants.net.TESTNET),
            )
            amount_sat = send_tab.amount_e.get_amount()
            if amount_sat is None:
                amount_sat = request.amount_sat
            if not isinstance(amount_sat, int) or amount_sat <= 0:
                raise SilentPaymentError(
                    "Enter a positive amount or include one in the BIP21 URI."
                )
        except SilentPaymentError as exc:
            window.show_error(str(exc))
            return

        if not constants.net.TESTNET:
            proceed = window.question(
                _(
                    "MAINNET WARNING\n\n"
                    "This independent Silent Payments plugin has not received "
                    "a professional security audit. A defect could make the "
                    "recipient unable to detect or spend the payment.\n\n"
                    "Verify the sp1 address independently and use a small "
                    "amount first. Continue with a real-bitcoin transaction?"
                ),
                title=_("Confirm experimental mainnet payment"),
            )
            if not proceed:
                return

        password = None
        if window.wallet.has_password():
            password = window.get_password(
                message=_(
                    "Password needed to derive and sign the silent payment."
                )
            )
            if password is None:
                return

        finalized_by_tx_id = {}

        def make_tx(fee_policy, *, confirmed_only=False, base_tx=None):
            if base_tx is not None:
                raise SilentPaymentError(
                    "Transaction batching is disabled for silent payments."
                )
            spend_confirmed_only = bool(
                confirmed_only
                or window.config.WALLET_SPEND_CONFIRMED_ONLY
            )
            coins = get_silent_payment_coins(
                window=window,
                confirmed_only=spend_confirmed_only,
            )
            tx = make_unsigned_silent_transaction(
                wallet=window.wallet,
                fee_policy=fee_policy,
                coins=coins,
                outputs=[
                    PartialTxOutput(
                        scriptpubkey=PLACEHOLDER_SCRIPT,
                        value=amount_sat,
                    )
                ],
                spend_confirmed_only=spend_confirmed_only,
            )
            finalized = finalize_transaction(
                wallet=window.wallet,
                tx=tx,
                password=password,
                recipient=request.recipient,
            )
            annotate_silent_payment_output(
                tx,
                finalized,
                request.recipient,
            )
            finalized_by_tx_id[id(tx)] = finalized
            return tx

        try:
            tx, is_preview, _paid_with_swap = confirm_transaction_compat(
                window=window,
                make_tx=make_tx,
                amount_sat=amount_sat,
            )
        except Exception as exc:
            self.logger.exception("silent payment transaction construction failed")
            window.show_error(
                _("Could not create silent payment:\n{}").format(str(exc))
            )
            return

        if tx is None:
            return
        if is_preview:
            window.show_warning(
                _(
                    "Unsigned preview/export is disabled for silent payments. "
                    "The output depends on the exact inputs; return and use Send "
                    "to sign immediately."
                )
            )
            return

        finalized = finalized_by_tx_id.get(id(tx))
        try:
            if finalized is None:
                raise SilentPaymentError(
                    "Could not match the confirmed transaction to its BIP352 derivation."
                )
            finalized = seal_after_confirmation(tx, finalized)
        except SilentPaymentError as exc:
            window.show_error(str(exc))
            return
        if not verify_transaction(tx, finalized):
            window.show_error(
                _(
                    "The transaction changed after silent-payment derivation. "
                    "Nothing was signed."
                )
            )
            return

        description = send_tab.get_message().strip()

        def sign_done(success):
            if not success:
                return
            output = tx.outputs()[finalized.output_index]
            _store_silent_payment_record(
                window.wallet,
                txid=tx.txid(),
                recipient_address=request.recipient.encoded,
                derived_address=output.address,
            )
            window.wallet.set_label(
                tx.txid(),
                silent_payment_history_label(
                    recipient_address=request.recipient.encoded,
                    derived_address=output.address,
                    description=description,
                ),
            )
            send_tab.do_clear()
            window.broadcast_or_show(tx)

        window.sign_tx_with_password(
            tx,
            callback=sign_done,
            password=password,
        )
