"""QML entry point: Silent Payments in Electrum's mobile (QML) Send flow.

Electrum's QML GUI exposes few plugin hooks, so this module:

* recognizes ``sp1``/``tsp1`` addresses and BIP21 ``sp`` instructions where the
  Send dialog hands a resolved payment identifier to the invoice parser;
* loads its QML file from the authorized plugin archive (in memory, never
  from a writable directory) into the running application's context;
* reuses Electrum's own fee/confirmation dialog with a finalizer that derives
  the BIP352 output for the exact selected inputs, seals it, and signs through
  Electrum's normal authenticated sign-and-broadcast path.
"""

import os
import threading
from functools import partial

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot, qInstallMessageHandler
from PyQt6.QtQml import QQmlComponent, QQmlEngine

from electrum import constants
from electrum.i18n import _
from electrum.plugin import BasePlugin, hook
from electrum.transaction import PartialTxOutput

import electrum.gui.qml as electrum_qml
from electrum.gui.qml import qeinvoice
from electrum.gui.qml.qetxfinalizer import QETxFinalizer
from electrum.gui.qml.qetypes import QEAmount
from electrum.gui.qml.qewallet import QEWallet

from .core import (
    PLACEHOLDER_SCRIPT,
    DerivationFailure,
    SilentPaymentError,
    expected_hrp,
    is_silent_payment_request_text,
    parse_silent_payment_request,
)
from .records import store_silent_payment_record
from .txflow import (
    annotate_silent_payment_output,
    finalize_transaction,
    make_unsigned_silent_transaction,
    seal_after_confirmation,
    silent_payment_history_label,
    validate_wallet,
    verify_transaction,
)

QML_FILE = "qml/SilentPaymentsSender.qml"
# Virtual location of the in-memory QML document. It is placed beside
# Electrum's own components so that "controls" and ConfirmTxDialog resolve
# exactly as they do for Electrum's built-in pages. No file is written there.
QML_VIRTUAL_NAME = "SilentPaymentsSenderPlugin.qml"

_hook_lock = threading.Lock()
_active_plugin = None  # type: Plugin | None


def _install_send_hook() -> None:
    """Route Silent Payment text away from the on-chain address validator."""
    with _hook_lock:
        parser_cls = qeinvoice.QEInvoiceParser
        if getattr(parser_cls.validateRecipient, "_silent_payments_sender", False):
            return
        original = parser_cls.validateRecipient

        def validateRecipient(self, pi):
            plugin = _active_plugin
            text = getattr(pi, "text", "") if pi is not None else ""
            if (
                plugin is not None
                and isinstance(text, str)
                and is_silent_payment_request_text(text)
            ):
                self.clear()
                plugin.so.handle_request(self._wallet, text)
                return None
            return original(self, pi)

        validateRecipient._silent_payments_sender = True
        validateRecipient._silent_payments_sender_original = original
        parser_cls.validateRecipient = validateRecipient


def _uninstall_send_hook() -> None:
    with _hook_lock:
        parser_cls = qeinvoice.QEInvoiceParser
        current = parser_cls.validateRecipient
        original = getattr(current, "_silent_payments_sender_original", None)
        if original is not None:
            parser_cls.validateRecipient = original


class SilentPaymentTxFinalizer(QETxFinalizer):
    """Electrum's QML finalizer, constrained to one derived BIP352 output."""

    def __init__(self, parent=None, *, recipient=None, description=""):
        super().__init__(parent)
        self._sp_recipient = recipient
        self._sp_description = description
        self._sp_finalized = {}
        self._sp_error = None
        self.canRbf = False

    def make_tx(self, amount):
        try:
            return self._make_silent_tx(amount)
        except SilentPaymentError as exc:
            # Electrum's update() reports repr(exc); keep the plain message.
            self._sp_error = str(exc)
            raise

    def _make_silent_tx(self, amount):
        if amount == "!" or not isinstance(amount, int) or amount <= 0:
            raise SilentPaymentError(
                _("Enter a positive amount. Sending the maximum is not supported.")
            )
        qewallet = self._wallet
        wallet = qewallet.wallet
        validate_wallet(wallet)
        password = qewallet.password if wallet.has_password() else None
        if wallet.has_password() and not password:
            raise SilentPaymentError(_("Unlock the wallet before sending."))
        confirmed_only = bool(wallet.config.WALLET_SPEND_CONFIRMED_ONLY)
        coins = wallet.get_spendable_coins(
            None,
            nonlocal_only=False,
            confirmed_only=confirmed_only,
        )
        tx = make_unsigned_silent_transaction(
            wallet=wallet,
            fee_policy=self._fee_policy,
            coins=coins,
            outputs=[PartialTxOutput(scriptpubkey=PLACEHOLDER_SCRIPT, value=amount)],
            spend_confirmed_only=confirmed_only,
        )
        finalized = finalize_transaction(
            wallet=wallet,
            tx=tx,
            password=password,
            recipient=self._sp_recipient,
        )
        annotate_silent_payment_output(tx, finalized, self._sp_recipient)
        # Only the most recent candidate may be signed.
        self._sp_finalized = {id(tx): finalized}
        return tx

    def update(self):
        self._sp_error = None
        self._sp_finalized = {}
        super().update()
        if self._sp_error:
            self._tx = None
            self.warning = self._sp_error

    @pyqtSlot()
    def signAndSend(self):
        tx = self._tx
        if not self._valid or tx is None:
            return
        finalized = self._sp_finalized.get(id(tx))
        try:
            if finalized is None:
                raise DerivationFailure(
                    _("Could not match the transaction to its BIP352 derivation.")
                )
            finalized = seal_after_confirmation(tx, finalized)
            if not verify_transaction(tx, finalized):
                raise DerivationFailure(
                    _(
                        "The transaction changed after silent-payment derivation. "
                        "Nothing was signed."
                    )
                )
        except SilentPaymentError as exc:
            self.signError.emit(str(exc))
            return
        # A signed candidate is never reused, even if signing is cancelled.
        self._sp_finalized = {}
        self._valid = False
        self.validChanged.emit()
        self._wallet.sign_and_broadcast(
            tx,
            on_success=partial(self._on_sp_signed, finalized),
            on_failure=self.on_sign_failed,
        )

    def _on_sp_signed(self, finalized, tx):
        wallet = self._wallet.wallet
        output = tx.outputs()[finalized.output_index]
        try:
            store_silent_payment_record(
                wallet,
                txid=tx.txid(),
                recipient_address=self._sp_recipient.encoded,
                derived_address=output.address,
            )
            wallet.set_label(
                tx.txid(),
                silent_payment_history_label(
                    recipient_address=self._sp_recipient.encoded,
                    derived_address=output.address,
                    description=self._sp_description,
                ),
            )
        except Exception:
            self._logger.exception("could not store silent payment record")
        self.finished.emit(True, False, tx.is_complete())

    def on_sign_failed(self, msg=None):
        # Signing was cancelled or failed: derive again from fresh inputs.
        self._valid = False
        self.validChanged.emit()
        super().on_sign_failed(msg)

    @pyqtSlot()
    def sign(self):
        self.signError.emit(_preview_disabled_message())

    @pyqtSlot()
    def saveOrShow(self):
        self.signError.emit(_preview_disabled_message())


def _preview_disabled_message() -> str:
    return _(
        "Unsigned preview/export is disabled for silent payments. "
        "The output depends on the exact inputs; use Pay to sign immediately."
    )


class SilentPaymentsQObject(QObject):
    """Bridge between the plugin and its QML document."""

    paymentRequested = pyqtSignal(
        str, QEAmount, bool, arguments=["recipient", "amount", "isMainnet"]
    )
    requestError = pyqtSignal(str, arguments=["message"])

    def __init__(self, plugin):
        super().__init__()
        self._plugin = plugin
        self._pending = None
        self._pending_amount = None
        self._finalizers = []

    def handle_request(self, qewallet, text: str) -> None:
        self._pending = None
        try:
            if qewallet is None:
                raise SilentPaymentError(_("Open a wallet first."))
            validate_wallet(qewallet.wallet)
            request = parse_silent_payment_request(
                text,
                expected_hrp=expected_hrp(is_testnet=constants.net.TESTNET),
            )
        except SilentPaymentError as exc:
            self.requestError.emit(str(exc))
            return
        self._pending = (qewallet, request)
        # Keep a reference: the signal argument has no QObject parent.
        self._pending_amount = amount = QEAmount(amount_sat=request.amount_sat or 0)
        self.paymentRequested.emit(
            request.recipient.encoded,
            amount,
            not constants.net.TESTNET,
        )

    @pyqtSlot(QEWallet, QEAmount, str, result=QObject)
    def createFinalizer(self, qewallet, amount, description):
        pending = self._pending
        if pending is None or pending[0] is not qewallet:
            self.requestError.emit(
                _("The wallet changed. Scan or paste the silent payment address again.")
            )
            return None
        sats = amount.satsInt if amount is not None else 0
        if amount is None or amount.isMax or not isinstance(sats, int) or sats <= 0:
            self.requestError.emit(_("Enter a positive amount."))
            return None
        _qewallet, request = pending
        finalizer = SilentPaymentTxFinalizer(
            self,
            recipient=request.recipient,
            description=description.strip(),
        )
        # Set the amount first: assigning the wallet runs the first update().
        finalizer.amount = QEAmount(amount_sat=sats)
        finalizer.wallet = qewallet
        # Keep a Python reference: QML must not garbage-collect it mid-flow.
        self._finalizers.append(finalizer)
        finalizer.finished.connect(partial(self._release, finalizer))
        return finalizer

    @pyqtSlot(QObject)
    def releaseFinalizer(self, finalizer):
        self._release(finalizer)

    def _release(self, finalizer, *_args):
        if finalizer in self._finalizers:
            self._finalizers.remove(finalizer)
            finalizer.deleteLater()


class Plugin(BasePlugin):

    def __init__(self, parent, config, name):
        super().__init__(parent, config, name)
        self.so = SilentPaymentsQObject(self)
        self._qml_object = None
        self._app = None

    @hook
    def init_qml(self, app):
        global _active_plugin
        if self._qml_object is not None:
            return
        self._app = app
        self.so.setParent(app)
        _active_plugin = self
        _install_send_hook()
        if app.engine.rootObjects():
            # Plugin enabled at runtime: the window already exists.
            self._create_qml_object(app)
        else:
            # Startup: Electrum runs init_qml before loading main.qml.
            app.engine.objectCreated.connect(self._on_engine_object_created)

    def _on_engine_object_created(self, obj, _url):
        if obj is None or self._qml_object is not None or self._app is None:
            return
        try:
            self._app.engine.objectCreated.disconnect(self._on_engine_object_created)
        except (TypeError, RuntimeError):
            pass
        # Compiling another component inside engine.load() re-enters the
        # QML type loader and blocks; create it on the next event-loop turn.
        QTimer.singleShot(0, lambda: self._create_qml_object(self._app))

    def _create_qml_object(self, app):
        if self._qml_object is not None or app is None:
            return
        try:
            self._qml_object = self._load_qml(app)
        except Exception:
            self.logger.exception("could not load Silent Payments QML")

    def _load_qml(self, app):
        engine = app.engine
        roots = engine.rootObjects()
        if not roots:
            raise RuntimeError("QML application window is not available")
        root = roots[0]
        components_dir = os.path.join(
            os.path.dirname(electrum_qml.__file__), "components"
        )
        qml_bytes = self.read_file(QML_FILE)
        component = QQmlComponent(engine)
        # Electrum installs a Python Qt message handler. setData() compiles
        # synchronously without releasing the GIL, so a message logged by
        # Qt's QML loader thread would wait for the GIL forever. Use Qt's
        # default handler while compiling, then restore Electrum's.
        previous_handler = qInstallMessageHandler(None)
        try:
            component.setData(
                qml_bytes,
                QUrl.fromLocalFile(os.path.join(components_dir, QML_VIRTUAL_NAME)),
            )
        finally:
            qInstallMessageHandler(previous_handler)
        if component.isError():
            raise RuntimeError(
                "; ".join(error.toString() for error in component.errors())
            )
        context = QQmlEngine.contextForObject(root)
        obj = component.createWithInitialProperties(
            {"plugin": self.so},
            context,
        )
        if obj is None:
            raise RuntimeError(
                "; ".join(error.toString() for error in component.errors())
            )
        obj.setParent(root)
        self._qml_component = component
        return obj

    def on_close(self):
        global _active_plugin
        if self._app is not None:
            try:
                self._app.engine.objectCreated.disconnect(self._on_engine_object_created)
            except (TypeError, RuntimeError):
                pass
        if _active_plugin is self:
            _active_plugin = None
            _uninstall_send_hook()
        if self._qml_object is not None:
            self._qml_object.deleteLater()
            self._qml_object = None
