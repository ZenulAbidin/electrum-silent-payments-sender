"""The Silent Payments tab: payments a separate spscand found for this wallet."""

from __future__ import annotations

import dataclasses
import threading

from electrum import constants
from electrum.gui.qt.util import (
    Buttons,
    CancelButton,
    ColorScheme,
    OkButton,
    WindowModalDialog,
)
from electrum.i18n import _
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import SilentPaymentError
from .receive_keys import (
    ReceiveSetupError,
    create_settings,
    load_settings,
    save_settings,
)
from .receive_spend import SpendError, build_signed_spend, silent_payment_recipient
from .receive_state import balance, display_order, spendable, status_text
from .records import store_silent_payment_record
from .spscand_client import (
    SpscandClient,
    SpscandError,
    check_url,
    electrum_fetch,
    proxy_enabled,
)
from .txflow import silent_payment_history_label

REFRESH_MS = 15_000
EVENT_WAIT = 25  # seconds a request for news is held open by spscand

UNCONFIRMED_WARNING = _(
    "Spending a payment that is not confirmed yet is risky: if the sender replaces "
    "their transaction, yours becomes invalid and is dropped."
)


def can_receive(wallet) -> bool:
    keystore = wallet.get_keystore() if hasattr(wallet, "get_keystore") else None
    return (
        getattr(wallet, "wallet_type", None) == "standard"
        and getattr(keystore, "type", None) == "bip32"
        and not wallet.is_watching_only()
    )


class ReceiveTab(QWidget):
    _loaded = pyqtSignal(object, object, object)  # status, outputs, error
    _in_gui = pyqtSignal(object)  # a function to run on the GUI thread

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.wallet = window.wallet
        self.outputs = []
        self.labels = {}
        self._busy = False

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.balance_label = QLabel()
        self.address_e = QLineEdit()
        self.address_e.setReadOnly(True)
        copy_button = QPushButton(_("Copy"))
        copy_button.clicked.connect(
            lambda: self.window.do_copy(self.address_e.text(), title=_("Address"))
        )
        address_row = QHBoxLayout()
        address_row.addWidget(QLabel(_("Silent payment address:")))
        address_row.addWidget(self.address_e, 1)
        address_row.addWidget(copy_button)

        self.table = QTreeWidget()
        self.table.setHeaderLabels(
            [_("Status"), _("Amount"), _("Label"), _("Block"), _("Output")]
        )
        self.table.setRootIsDecorated(False)

        self.setup_button = QPushButton()
        self.setup_button.clicked.connect(self.open_settings)
        self.refresh_button = QPushButton(_("Refresh"))
        self.refresh_button.clicked.connect(self.refresh)
        self.rescan_button = QPushButton(_("Rescan…"))
        self.rescan_button.clicked.connect(self.rescan)
        self.label_button = QPushButton(_("New labelled address…"))
        self.label_button.clicked.connect(self.new_label)
        self.spend_button = QPushButton(_("Spend…"))
        self.spend_button.clicked.connect(self.open_spend)
        self.buttons = QHBoxLayout()
        self.buttons.addWidget(self.setup_button)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.refresh_button)
        self.buttons.addWidget(self.rescan_button)
        self.buttons.addWidget(self.label_button)
        self.buttons.addWidget(self.spend_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(address_row)
        layout.addWidget(self.balance_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(self.buttons)

        self._loaded.connect(self._show)
        self._in_gui.connect(lambda function: function())
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self._closed = threading.Event()
        threading.Thread(
            target=self._follow_events, name="spscand-events", daemon=True
        ).start()
        self.update_view()
        self.refresh()

    def settings(self):
        try:
            return load_settings(self.wallet)
        except ReceiveSetupError as exc:
            self._set_status(str(exc), error=True)
            return None

    def client(self, settings) -> SpscandClient:
        network = self.window.network
        return SpscandClient(
            settings.url,
            settings.token,
            electrum_fetch(network),
            proxy_enabled=proxy_enabled(network),
        )

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            ColorScheme.RED.as_stylesheet() if error else ""
        )

    def update_view(self) -> None:
        settings = self.settings()
        configured = settings is not None and bool(settings.url)
        self.setup_button.setText(
            _("Settings…") if settings else _("Set up receiving…")
        )
        self.address_e.setText(settings.address if settings else "")
        self.refresh_button.setEnabled(configured)
        self.label_button.setEnabled(configured)
        self.rescan_button.setEnabled(configured)
        self.spend_button.setEnabled(configured)
        if settings is None:
            self._set_status(
                _(
                    "Receive silent payments with spscand, running on your own computer "
                    "or server. It does the scanning; this wallet shows the payments and "
                    "spends them."
                )
            )
        elif not settings.url:
            self._set_status(_("Enter your spscand's address in Settings."))
        self._fill_table()

    def refresh(self) -> None:
        settings = self.settings()
        if settings is None or not settings.url or self._busy:
            return
        self._busy = True

        def work():
            try:
                client = self.client(settings)
                status = client.check_pairing(settings.address)
                self.labels = client.labels()
                self._loaded.emit(status, client.outputs(), None)
            except Exception as exc:
                self._loaded.emit(None, None, exc)

        threading.Thread(target=work, name="spscand", daemon=True).start()

    def _show(self, status, outputs, error) -> None:
        self._busy = False
        if error is not None:
            message = str(error) if isinstance(error, SpscandError) else repr(error)
            self._set_status(_("spscand: {}").format(message), error=True)
            return
        self.outputs = outputs
        scanned, tip = status.get("scanned_height"), status.get("tip")
        if status.get("error"):
            text = _("spscand reports an error: {}").format(status["error"])
        elif status.get("synced"):
            text = _("spscand {}: scanned to block {}.").format(
                status["version"], scanned
            )
        else:
            text = _("spscand {}: scanning, at block {} of {}.").format(
                status["version"], scanned, tip
            )
        mempool = status.get("mempool")
        if mempool is None:
            text += " " + _("It does not watch the mempool: payments show once mined.")
        elif mempool.get("error"):
            text += " " + _("Mempool: {}").format(mempool["error"])
        self._set_status(text, error=bool(status.get("error")))
        self._fill_table()

    def _fill_table(self) -> None:
        fmt = self.window.format_amount_and_units
        totals = balance(self.outputs)
        text = _("Balance: {}").format(fmt(totals.confirmed))
        if totals.incoming:
            text += "   " + _("Incoming, unconfirmed: {}").format(fmt(totals.incoming))
        if totals.outgoing:
            text += "   " + _("Being spent: {}").format(fmt(totals.outgoing))
        self.balance_label.setText(text)
        self.table.clear()
        for output in display_order(self.outputs):
            label = "" if output.label is None else str(output.label)
            comment = self.labels.get(output.label, {}).get("comment")
            if comment:
                label += f" ({comment})"
            block = "" if output.height is None else str(output.height)
            item = QTreeWidgetItem(
                [status_text(output), fmt(output.amount), label, block, output.outpoint]
            )
            if not output.confirmed:
                item.setToolTip(0, _("Not mined yet. The sender can still replace it."))
            self.table.addTopLevelItem(item)
        for column in range(4):
            self.table.resizeColumnToContents(column)

    def open_settings(self) -> None:
        dialog = ReceiveSettingsDialog(self)
        if dialog.exec():
            self.update_view()
            self.refresh()

    def _background(self, work, done) -> None:
        """Run work() off the GUI thread, then done(result, error) on it."""

        def run():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            self._in_gui.emit(lambda: done(result, error))

        threading.Thread(target=run, name="spscand", daemon=True).start()

    def new_label(self) -> None:
        settings = self.settings()
        if settings is None or not settings.url:
            return
        comment, ok = QInputDialog.getText(
            self,
            _("New labelled address"),
            _("What is this address for? (only you and spscand see this)"),
        )
        if not ok:
            return

        def done(label, error):
            if error is not None:
                self.window.show_error(_("spscand: {}").format(error))
                return
            self.window.do_copy(label["address"], title=_("Labelled address"))
            self.window.show_message(
                _(
                    "Labelled address {} ({}):\n\n{}\n\nIt was copied to the clipboard."
                ).format(
                    label["label"],
                    label["comment"] or _("no comment"),
                    label["address"],
                )
            )
            self.refresh()

        self._background(lambda: self.client(settings).add_label(comment.strip()), done)

    def rescan(self) -> None:
        settings = self.settings()
        if settings is None or not settings.url:
            return
        height, ok = QInputDialog.getInt(
            self,
            _("Rescan"),
            _(
                "spscand forgets what it found from this block on and scans again. "
                "Start at block:"
            ),
            value=settings.birthday,
            min=0,
        )
        if not ok:
            return

        def done(started, error):
            if error is not None:
                self.window.show_error(_("spscand: {}").format(error))
                return
            self._set_status(
                _("spscand is scanning again from block {}.").format(started)
            )
            self.refresh()

        self._background(lambda: self.client(settings).rescan(height), done)

    def open_spend(self) -> None:
        settings = self.settings()
        if settings is None:
            return
        candidates = spendable(
            self.outputs, allow_unconfirmed=settings.allow_unconfirmed
        )
        if not candidates:
            self.window.show_message(_("There are no payments to spend yet."))
            return
        if SpendDialog(self, settings, candidates).exec():
            self.refresh()

    def _follow_events(self) -> None:
        """Refresh as soon as spscand reports something, instead of at the next poll."""
        since, boot = 0, None
        while not self._closed.is_set():
            try:
                settings = load_settings(self.wallet)
            except ReceiveSetupError:
                settings = None
            if settings is None or not settings.url:
                self._closed.wait(REFRESH_MS / 1000)
                continue
            try:
                news = self.client(settings).events(since, boot, EVENT_WAIT)
            except SpscandError as exc:
                if "HTTP 404" in str(exc):
                    return  # an spscand without events: the timer keeps polling
                self._closed.wait(REFRESH_MS / 1000)
                continue
            except Exception:
                self._closed.wait(REFRESH_MS / 1000)
                continue
            if self._closed.is_set():
                return  # the tab is gone
            if news["events"] or news.get("reset"):
                self._in_gui.emit(self.refresh)
            since, boot = news["last"], news["boot"]

    def close(self) -> None:
        self.timer.stop()
        self._closed.set()


class ReceiveSettingsDialog(WindowModalDialog):
    def __init__(self, tab: ReceiveTab):
        super().__init__(tab.window, _("Silent payments: receiving"))
        self.tab = tab
        self.window = tab.window
        self.wallet = tab.wallet
        self.settings = tab.settings()
        keystore = self.wallet.get_keystore()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_e = QLineEdit(self.settings.url if self.settings else "")
        self.url_e.setPlaceholderText("http://127.0.0.1:7352  or  http://….onion:7352")
        self.token_e = QLineEdit(self.settings.token if self.settings else "")
        self.token_e.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("spscand address"), self.url_e)
        form.addRow(_("API token"), self.token_e)

        self.birthday_e = self.words_e = self.passphrase_e = None
        if self.settings is None:
            network = self.window.network
            height = network.get_local_height() if network else 0
            self.birthday_e = QLineEdit(str(max(0, height)))
            form.addRow(_("Start scanning at block"), self.birthday_e)
            if not keystore.has_seed():
                self.words_e = QLineEdit()
                self.passphrase_e = QLineEdit()
                self.passphrase_e.setEchoMode(QLineEdit.EchoMode.Password)
                form.addRow(_("BIP39 seed words"), self.words_e)
                form.addRow(_("Seed passphrase (if any)"), self.passphrase_e)
        layout.addLayout(form)
        if self.settings is None and self.words_e is not None:
            note = QLabel(
                _(
                    "Electrum does not keep this wallet's seed, and silent payment keys "
                    "come from it. The words are checked against this wallet and are not "
                    "stored."
                )
            )
            note.setWordWrap(True)
            layout.addWidget(note)

        self.unconfirmed_cb = QCheckBox(_("Allow spending unconfirmed payments"))
        self.unconfirmed_cb.setChecked(
            bool(self.settings and self.settings.allow_unconfirmed)
        )
        self.unconfirmed_cb.setToolTip(UNCONFIRMED_WARNING)
        layout.addWidget(self.unconfirmed_cb)

        if self.settings is not None:
            layout.addWidget(self._spscand_setup_box())
        layout.addLayout(Buttons(CancelButton(self), OkButton(self)))

    def _spscand_setup_box(self) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        intro = QLabel(
            _(
                "To set up spscand, run this where it lives, and paste the scan key "
                "when it asks. The scan key lets spscand see your payments, never spend "
                "them."
            )
        )
        intro.setWordWrap(True)
        grid.addWidget(intro, 0, 0, 1, 3)
        rows = [
            (_("Command"), self.settings.spscand_init_command()),
            (_("Scan key"), f"{self.settings.scan_secret:064x}"),
        ]
        for row, (name, value) in enumerate(rows, start=1):
            field = QLineEdit(value)
            field.setReadOnly(True)
            copy = QPushButton(_("Copy"))
            copy.clicked.connect(
                lambda _checked, v=value, n=name: self.window.do_copy(v, title=n)
            )
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(field, row, 1)
            grid.addWidget(copy, row, 2)
        return box

    def accept(self) -> None:
        if self.unconfirmed_cb.isChecked() and not (
            self.settings and self.settings.allow_unconfirmed
        ):
            if not self.question(UNCONFIRMED_WARNING + "\n\n" + _("Allow it anyway?")):
                return
        url, token = self.url_e.text().strip(), self.token_e.text().strip()
        try:
            if url:
                # Checked now, so a bad address fails here rather than on every refresh.
                check_url(url, proxy_enabled=proxy_enabled(self.window.network))
            settings = self.settings or self._create()
            if settings is None:
                return
        except (ReceiveSetupError, SpscandError) as exc:
            self.show_error(str(exc))
            return
        settings = dataclasses.replace(
            settings,
            url=url,
            token=token,
            allow_unconfirmed=self.unconfirmed_cb.isChecked(),
        )
        save_settings(self.wallet, settings)
        created = self.settings is None
        super().accept()
        if created:
            ReceiveSettingsDialog(self.tab).exec()  # now with the spscand command

    def _create(self):
        try:
            birthday = int(self.birthday_e.text())
        except ValueError:
            raise ReceiveSetupError(_("The start block must be a number.")) from None
        password = None
        if self.wallet.has_password():
            password = self.window.get_password(
                message=_("Password needed to derive your silent payment keys.")
            )
            if password is None:
                return None
        return create_settings(
            self.wallet.get_keystore(),
            password,
            network=constants.net.NET_NAME,
            birthday=birthday,
            bip39_words=self.words_e.text() if self.words_e else "",
            bip39_passphrase=self.passphrase_e.text() if self.passphrase_e else "",
        )


class SpendDialog(WindowModalDialog):
    """Send received payments to an address, signing in this wallet."""

    def __init__(self, tab: ReceiveTab, settings, candidates):
        super().__init__(tab.window, _("Spend silent payments"))
        self.tab, self.window, self.wallet = tab, tab.window, tab.wallet
        self.settings = settings
        self.candidates = candidates
        fmt = self.window.format_amount_and_units

        layout = QVBoxLayout(self)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([_("Payment"), _("Amount"), _("Status")])
        self.list.setRootIsDecorated(False)
        for output in candidates:
            item = QTreeWidgetItem(
                [output.outpoint, fmt(output.amount), status_text(output)]
            )
            item.setCheckState(0, Qt.CheckState.Checked)
            self.list.addTopLevelItem(item)
        layout.addWidget(self.list)

        form = QFormLayout()
        self.destination_e = QLineEdit(self.wallet.get_receiving_address())
        self.amount_e = QLineEdit()
        self.amount_e.setPlaceholderText(_("everything selected, minus the fee"))
        self.fee_rate_e = QLineEdit(f"{self._default_fee_rate():g}")
        self.destination_e.setToolTip(
            _("An address, or a silent payment address (sp1…)")
        )
        form.addRow(_("Pay to"), self.destination_e)
        form.addRow(_("Amount (sat)"), self.amount_e)
        form.addRow(_("Fee rate (sat/vB)"), self.fee_rate_e)
        layout.addLayout(form)
        if any(not o.confirmed for o in candidates):
            warning = QLabel(UNCONFIRMED_WARNING)
            warning.setWordWrap(True)
            warning.setStyleSheet(ColorScheme.RED.as_stylesheet())
            layout.addWidget(warning)
        layout.addLayout(
            Buttons(CancelButton(self), OkButton(self, _("Sign and send")))
        )

    def _default_fee_rate(self) -> float:
        from electrum.fee_policy import FeePolicy

        try:
            per_kb = FeePolicy(self.window.config.FEE_POLICY).fee_per_kb(
                self.window.network
            )
        except Exception:
            per_kb = None
        return max(1.0, per_kb / 1000) if per_kb else 2.0

    def selected(self):
        return [
            output
            for index, output in enumerate(self.candidates)
            if self.list.topLevelItem(index).checkState(0) == Qt.CheckState.Checked
        ]

    def accept(self) -> None:
        try:
            amount_text = self.amount_e.text().strip()
            amount = int(amount_text) if amount_text else None
            fee_rate = float(self.fee_rate_e.text())
        except ValueError:
            self.show_error(
                _("Enter the amount in satoshis and the fee rate as a number.")
            )
            return
        password = None
        if self.wallet.has_password():
            password = self.window.get_password(
                message=_("Password needed to sign with your silent payment key.")
            )
            if password is None:
                return
        change_address = (
            self.wallet.get_single_change_address_for_new_transaction()
            or self.wallet.get_receiving_address()
        )
        try:
            spend_secret = self.settings.spend_secret(
                self.wallet.get_keystore(), password
            )
            tx = build_signed_spend(
                self.selected(),
                spend_secret=spend_secret,
                destination=self.destination_e.text().strip(),
                change_address=change_address,
                amount=amount,
                fee_rate=fee_rate,
                locktime=self.wallet.adb.get_local_height(),
            )
        except (ReceiveSetupError, SpendError, SilentPaymentError) as exc:
            self.show_error(str(exc))
            return
        except Exception as exc:
            self.show_error(_("Could not build the transaction: {}").format(exc))
            return
        destination = self.destination_e.text().strip()
        recipient = silent_payment_recipient(destination)
        fee = sum(o.amount for o in self.selected()) - sum(
            o.value for o in tx.outputs()
        )
        fmt = self.window.format_amount_and_units
        sent = tx.outputs()[0].value if len(tx.outputs()) == 1 else (amount or 0)
        if not self.question(
            _("Send {} to {}?\nFee: {}").format(fmt(sent), destination, fmt(fee))
        ):
            return
        if recipient:
            # As for payments from the Send tab: show the recipient in the history.
            derived = next(
                o.address for o in tx.outputs() if o.address != change_address
            )
            store_silent_payment_record(
                self.wallet,
                txid=tx.txid(),
                recipient_address=recipient.encoded,
                derived_address=derived,
            )
            self.wallet.set_label(
                tx.txid(),
                silent_payment_history_label(
                    recipient_address=recipient.encoded,
                    derived_address=derived,
                    description="",
                ),
            )
        super().accept()
        self.window.broadcast_or_show(tx)
