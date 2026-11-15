"""The Silent Payments tab: payments a separate spscand found for this wallet."""

from __future__ import annotations

import dataclasses
import threading

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from electrum import constants
from electrum.i18n import _
from electrum.gui.qt.util import (
    Buttons,
    CancelButton,
    ColorScheme,
    OkButton,
    WindowModalDialog,
)

from .receive_keys import (
    ReceiveSetupError,
    create_settings,
    load_settings,
    save_settings,
)
from .receive_state import balance, display_order, status_text
from .spscand_client import (
    SpscandClient,
    SpscandError,
    check_url,
    electrum_fetch,
    proxy_enabled,
)

REFRESH_MS = 15_000

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

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.wallet = window.wallet
        self.outputs = []
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
        self.buttons = QHBoxLayout()
        self.buttons.addWidget(self.setup_button)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(address_row)
        layout.addWidget(self.balance_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(self.buttons)

        self._loaded.connect(self._show)
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
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

    def close(self) -> None:
        self.timer.stop()


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
