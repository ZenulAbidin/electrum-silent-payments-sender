#!/usr/bin/env python3
"""Drive Electrum's real GUI (QML or Qt) through a Silent Payment on regtest.

This is an end-to-end test, not a unit test. It starts Electrum's QML (mobile)
or Qt (desktop) application offscreen, installs and authorizes the release ZIP
exactly as an external plugin, opens a funded regtest wallet, enters a
``tsp1`` address or BIP21 ``sp`` URI, steps through the plugin's and
Electrum's own confirmation and password dialogs, and broadcasts to a local
regtest node. The receiving side is checked independently with
``silent_payment_test_receiver.py``.

Requirements: Electrum (4.8.3+) source or install with PyQt6 incl. QtQuick on
``PYTHONPATH``; a regtest ``bitcoind`` and an Electrum server; ``bitcoin-cli``.

Test-only shortcut: the plugin-password key file must normally be root-owned
(``/etc/electrum/plugins_key``). The harness points Electrum at a key inside
the work directory and accepts its ownership. Everything else (ZIP hash
signature, in-memory import, manifest checks) is Electrum's normal code path.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Ubuntu's apport hook trips an import-time assertion in exceptiongroup.
sys.excepthook = sys.__excepthook__

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "silent_payments_sender"
PLUGIN_PASSWORD = "regtest-harness-plugin-password"
WALLET_PASSWORD = "regtest-harness-wallet-password"


class HarnessFailure(Exception):
    pass


def bcli(args, *cmd):
    out = subprocess.run(
        [args.bitcoin_cli, *args.bitcoin_cli_arg, *cmd],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return out


def receiver(args, *cmd):
    out = subprocess.run(
        [sys.executable, "-s", str(ROOT / "scripts" / "silent_payment_test_receiver.py"), *cmd],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def make_wallet(args, config, path: Path, script_type: str):
    from electrum import keystore
    from electrum.mnemonic import Mnemonic
    from electrum.wallet import restore_wallet_from_text

    if script_type == "p2wpkh":
        text = Mnemonic("en").make_seed(seed_type="segwit")
    elif script_type == "p2pkh":
        text = Mnemonic("en").make_seed(seed_type="standard")
    elif script_type == "p2wpkh-p2sh":
        words = Mnemonic("en").make_seed(seed_type="standard")
        root_seed = keystore.bip39_to_seed(words, passphrase="")
        ks = keystore.from_bip43_rootseed(
            root_seed, derivation="m/49h/1h/0h", xtype="p2wpkh-p2sh"
        )
        text = ks.xprv
    else:
        raise ValueError(script_type)
    result = restore_wallet_from_text(
        text,
        path=str(path),
        config=config,
        password=WALLET_PASSWORD,
        encrypt_file=True,
        gap_limit=20,
    )
    wallet = result["wallet"]
    if wallet.get_txin_type() != script_type:
        raise HarnessFailure(f"wallet has {wallet.get_txin_type()}, wanted {script_type}")
    addresses = wallet.get_receiving_addresses()[:3]
    return addresses


def negative_inputs(sp_address):
    """Inputs the plugin must reject: bad checksum, and a mainnet address on regtest."""
    from electrum import segwit_addr

    last = sp_address[-1]
    corrupted = sp_address[:-1] + ("q" if last != "q" else "p")
    decoded = segwit_addr.bech32_decode(sp_address, ignore_long_length=True)
    mainnet = segwit_addr.bech32_encode(
        segwit_addr.Encoding.BECH32M, "sp", decoded.data
    )
    return [
        ("bad_checksum", corrupted, "Invalid BIP352 address"),
        ("wrong_network", mainnet, "expected an tsp address"),
    ]


class QmlDriver:
    """A small polling state machine run from the Qt event loop (QML GUI)."""

    def __init__(self, args, gui, wallet_path, sp_text, expected_amount, results):
        from PyQt6.QtCore import QTimer
        self.args = args
        self.gui = gui
        self.app = gui.app
        self.wallet_path = wallet_path
        self.sp_text = sp_text
        self.expected_amount = expected_amount
        self.results = results
        self.deadline = time.monotonic() + args.timeout
        self.step = self.step_load_wallet
        self.timer = QTimer(self.app)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.mempool_before = None

    # helpers -------------------------------------------------------------
    @property
    def root(self):
        return self.app.engine.rootObjects()[0]

    def js(self, code, scope=None):
        from PyQt6.QtQml import QQmlEngine, QQmlExpression
        scope = scope or self.root
        expr = QQmlExpression(QQmlEngine.contextForObject(self.root), scope, code)
        value, undefined = expr.evaluate()
        if expr.hasError():
            raise HarnessFailure(f"JS error in {code!r}: {expr.error().toString()}")
        return value

    def find(self, name):
        from PyQt6.QtCore import QObject
        return self.root.findChild(QObject, name)

    def top_dialog_title(self):
        return self.js(
            "app.activeDialogs.length ? String(app.activeDialogs[app.activeDialogs.length-1].title) : ''"
        )

    def finish(self, error=None):
        self.timer.stop()
        if error:
            self.results["error"] = str(error)
        # As Electrum's own SIGINT handler: skip the close confirmation.
        self.app.appController.wantClose = True
        self.app.quit()

    def tick(self):
        if time.monotonic() > self.deadline:
            self.finish(f"timeout in {self.step.__name__}")
            return
        try:
            next_step = self.step()
        except Exception as exc:  # report and stop
            import traceback
            traceback.print_exc()
            self.finish(exc)
            return
        if next_step is not None:
            print(f"[qml-harness] {self.step.__name__} -> {next_step.__name__}", flush=True)
            self.step = next_step

    # steps ---------------------------------------------------------------
    def step_load_wallet(self):
        if not self.app.engine.rootObjects():
            return None
        early = self.find("silentPaymentsAmountDialog")
        if early is not None and early.property("opened"):
            raise HarnessFailure("dialog visible before any input")
        plugin_obj = [o for o in self.root.children() if o.property("plugin") is not None]
        self.results["plugin_qml_loaded"] = bool(plugin_obj)
        if not plugin_obj:
            raise HarnessFailure("plugin QML object was not created")
        self.app.daemon.loadWallet(str(self.wallet_path), WALLET_PASSWORD)
        return self.step_wait_synced

    def step_wait_synced(self):
        qewallet = self.app.daemon.currentWallet
        if qewallet is None:
            return None
        wallet = qewallet.wallet
        if not wallet.is_up_to_date() or not wallet.network or not wallet.network.is_connected():
            return None
        confirmed, unconfirmed, _x = wallet.get_balance()[:3]
        if confirmed <= 0:
            return None
        self.results["balance_before"] = confirmed
        self.mempool_before = set(json.loads(bcli(self.args, "getrawmempool")))
        # Hand the text to Electrum the way an opened bitcoin:/payment link
        # does (WalletMainView -> PIResolver -> InvoiceParser). The Send
        # dialog's paste/scan dispatch() ends in the same PIResolver call; it
        # is not opened here because its camera scanner needs a real device.
        self.negatives = negative_inputs(self.results["silent_payment_address"])
        return self.step_negative

    def step_negative(self):
        if not self.negatives:
            self.js(f"app.pendingIntent = {json.dumps(self.sp_text)}")
            return self.step_amount_dialog
        name, text, expected = self.negatives[0]
        if not getattr(self, "_negative_sent", False):
            self._negative_sent = True
            self.js(f"app.pendingIntent = {json.dumps(text)}")
            return None
        if self.top_dialog_title() != "Silent Payment":
            return None
        message = self.js("String(app.activeDialogs[app.activeDialogs.length-1].text)")
        if expected not in message:
            raise HarnessFailure(f"{name}: unexpected message {message!r}")
        if self.find("silentPaymentsAmountDialog").property("opened"):
            raise HarnessFailure(f"{name}: amount dialog opened for a rejected input")
        self.results.setdefault("rejected_inputs", {})[name] = message
        self.js("app.activeDialogs[app.activeDialogs.length-1].doAccept()")
        self.negatives.pop(0)
        self._negative_sent = False
        return None

    def step_amount_dialog(self):
        dialog = self.find("silentPaymentsAmountDialog")
        if dialog is None or not dialog.property("opened"):
            title = self.top_dialog_title()
            if title == "Confirm experimental mainnet payment":
                raise HarnessFailure("mainnet warning shown on regtest")
            if title and title not in ("", "Silent Payment"):
                pass
            return None
        field = self.find("silentPaymentsAmountField")
        prefilled = field.property("text")
        self.results["amount_prefilled"] = prefilled
        if self.args.amount_in_uri:
            expected = self.js(f"Config.formatSatsForEditing(Config.unitsToSats('{prefilled}'))")
            sats = self.js(f"Config.unitsToSats('{prefilled}').satsInt")
            if int(sats) != self.expected_amount:
                raise HarnessFailure(f"BIP21 amount not prefilled: {prefilled!r} ({sats})")
        else:
            if prefilled:
                raise HarnessFailure(f"unexpected prefilled amount {prefilled!r}")
            text = self.js(f"Config.formatSatsForEditing({self.expected_amount})")
            field.setProperty("text", text)
        self.find("silentPaymentsDescriptionField").setProperty("text", "regtest harness")
        self.js("clicked()", scope=self.find("silentPaymentsNextButton"))
        return self.step_confirm_dialog

    def step_confirm_dialog(self):
        dialog = self.find("silentPaymentsConfirmDialog")
        if dialog is None:
            return None
        finalizer = dialog.property("finalizer")
        if finalizer is None:
            raise HarnessFailure("confirm dialog has no finalizer")
        if not finalizer.property("valid"):
            warning = finalizer.property("warning")
            if warning:
                raise HarnessFailure(f"finalizer invalid: {warning}")
            return None
        outputs = finalizer.property("outputs")
        self.results["confirm_outputs"] = [
            {"address": o["address"], "value": o["value"]} for o in outputs
        ]
        sp_outputs = [o for o in outputs if str(o["address"]).startswith("tsp1")]
        if len(sp_outputs) != 1 or sp_outputs[0]["value"] != self.expected_amount:
            raise HarnessFailure(f"unexpected outputs {outputs}")
        self.results["finalizer_class"] = type(finalizer).__name__
        # The unsigned export paths must refuse.
        errors = []
        finalizer.signError.connect(errors.append)
        finalizer.saveOrShow()
        finalizer.sign()
        if len(errors) != 2 or "disabled" not in errors[0]:
            raise HarnessFailure(f"unsigned export was not refused: {errors}")
        finalizer.signError.disconnect(errors.append)
        self.results["export_refused"] = True
        # Fee change re-derives from the (possibly new) input set.
        before = finalizer._tx.serialize_to_network(include_sigs=False)
        finalizer.setProperty("sliderPos", max(0, finalizer.property("sliderPos") - 1))
        finalizer.doUpdate()
        if not finalizer.property("valid"):
            raise HarnessFailure(f"finalizer invalid after fee change: {finalizer.property('warning')}")
        self.results["valid_after_fee_change"] = True
        self.js("doAccept()", scope=dialog)
        return self.step_auth

    def step_auth(self):
        title = self.top_dialog_title()
        if title == "Sign and send on-chain transaction?":
            self.js("app.activeDialogs[app.activeDialogs.length-1].doAccept()")
            self.results["auth_dialog"] = title
            return self.step_wait_broadcast
        if title == "Error":
            text = self.js("String(app.activeDialogs[app.activeDialogs.length-1].text)")
            raise HarnessFailure(f"error dialog: {text}")
        return None

    def step_wait_broadcast(self):
        mempool = set(json.loads(bcli(self.args, "getrawmempool")))
        new = mempool - self.mempool_before
        if not new:
            return None
        if len(new) != 1:
            raise HarnessFailure(f"several new mempool txs: {new}")
        txid = new.pop()
        self.results["txid"] = txid
        wallet = self.app.daemon.currentWallet.wallet
        records = wallet.db.get("silent_payments_sender_records", {})
        self.results["record_stored"] = txid in records
        self.results["label"] = wallet.get_label_for_txid(txid)
        self.finish()
        return None


class QtDriver:
    """Drives Electrum's Qt desktop window; modal dialogs are handled per tick."""

    def __init__(self, args, gui, wallet_path, sp_text, expected_amount, results):
        from PyQt6.QtCore import QTimer
        self.args = args
        self.gui = gui
        self.sp_text = sp_text
        self.expected_amount = expected_amount
        self.results = results
        self.deadline = time.monotonic() + args.timeout
        self.step = self.step_window
        self.mempool_before = None
        self.messages = []
        self.timer = QTimer(gui.app)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def finish(self, error=None):
        self.timer.stop()
        if error:
            self.results["error"] = str(error)
        self.gui.app.quit()

    def tick(self):
        if time.monotonic() > self.deadline:
            self.finish(f"timeout in {self.step.__name__}; messages={self.messages}")
            return
        try:
            next_step = self.step()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.finish(exc)
            return
        if next_step is not None:
            print(f"[qt-harness] {self.step.__name__} -> {next_step.__name__}", flush=True)
            self.step = next_step

    @property
    def window(self):
        return self.gui.windows[0] if self.gui.windows else None

    def step_window(self):
        window = self.window
        if window is None:
            return None
        wallet = window.wallet
        if not wallet.is_up_to_date() or not wallet.network or not wallet.network.is_connected():
            return None
        if wallet.get_balance()[0] <= 0:
            return None
        self.results["balance_before"] = wallet.get_balance()[0]
        self.mempool_before = set(json.loads(bcli(self.args, "getrawmempool")))
        window.show_send_tab()
        self.negatives = negative_inputs(self.results["silent_payment_address"])
        return self.step_negative

    def step_negative(self):
        send_tab = self.window.send_tab
        if not self.negatives:
            send_tab.do_clear()
            # Paste/QR path (the plugin wraps try_payment_identifier).
            send_tab.payto_e.try_payment_identifier(self.sp_text)
            return self.step_fill
        name, text, expected = self.negatives[0]
        if not getattr(self, "_negative_sent", False):
            self._negative_sent = True
            send_tab.do_clear()
            send_tab.payto_e.try_payment_identifier(text)
            send_tab.amount_e.setAmount(self.expected_amount)
            return None
        message = send_tab.invoice_error.label.text()
        if expected not in message:
            return None
        if send_tab.send_button.isEnabled():
            raise HarnessFailure(f"{name}: Pay enabled for a rejected input")
        self.results.setdefault("rejected_inputs", {})[name] = message
        self.negatives.pop(0)
        self._negative_sent = False
        return None

    def step_fill(self):
        send_tab = self.window.send_tab
        if not getattr(send_tab, "_silent_payments_sender_active", False):
            return None
        if self.args.amount_in_uri:
            if send_tab.amount_e.get_amount() != self.expected_amount:
                return None
        else:
            send_tab.amount_e.setAmount(self.expected_amount)
        send_tab.message_e.setText("regtest harness")
        return self.step_click

    def step_click(self):
        from PyQt6.QtCore import QTimer
        send_tab = self.window.send_tab
        if not send_tab.send_button.isEnabled():
            return None
        self.results["payto_tooltip"] = send_tab.payto_e.line_edit.toolTip()
        # Run the click from the event loop: it opens nested modal loops.
        QTimer.singleShot(0, send_tab.send_button.click)
        return self.step_modals

    def step_modals(self):
        from PyQt6.QtWidgets import QApplication, QMessageBox
        modal = QApplication.activeModalWidget()
        if modal is not None:
            name = type(modal).__name__
            if name == "PasswordDialog":
                modal.pw.setText(WALLET_PASSWORD)
                modal.accept()
                self.results.setdefault("dialogs", []).append(name)
            elif name == "ConfirmTxDialog":
                if not modal.ok_button.isEnabled():
                    return None
                tx = modal.tx
                outs = [(o.get_ui_address_str(), o.value) for o in tx.outputs()]
                self.results["confirm_outputs"] = [
                    {"address": a, "value": v} for a, v in outs
                ]
                sp = [o for o in outs if str(o[0]).startswith("tsp1")]
                if len(sp) != 1 or sp[0][1] != self.expected_amount:
                    raise HarnessFailure(f"unexpected confirm outputs {outs}")
                self.results.setdefault("dialogs", []).append(name)
                modal.ok_button.click()
            elif isinstance(modal, QMessageBox):
                text = modal.text()
                self.messages.append(text)
                self.results.setdefault("dialogs", []).append(f"QMessageBox: {text[:80]}")
                if modal.icon() == QMessageBox.Icon.Critical:
                    modal.accept()
                    raise HarnessFailure(f"error dialog: {text}")
                modal.accept()
            else:
                self.results.setdefault("dialogs", []).append(f"unhandled {name}")
                return None
        mempool = set(json.loads(bcli(self.args, "getrawmempool")))
        new = mempool - self.mempool_before
        if not new:
            return None
        if len(new) != 1:
            raise HarnessFailure(f"several new mempool txs: {new}")
        txid = new.pop()
        self.results["txid"] = txid
        wallet = self.window.wallet
        records = wallet.db.get("silent_payments_sender_records", {})
        self.results["record_stored"] = txid in records
        self.results["label"] = wallet.get_label_for_txid(txid)
        return self.step_drain

    def step_drain(self):
        # Close the final "Payment sent" message, then stop.
        from PyQt6.QtWidgets import QApplication, QMessageBox
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QMessageBox):
            self.results.setdefault("dialogs", []).append(f"QMessageBox: {modal.text()[:80]}")
            modal.accept()
            return None
        self.finish()
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gui", choices=("qml", "qt"), default="qml")
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--server", default="127.0.0.1:60401:t")
    parser.add_argument("--bitcoin-cli", default="bitcoin-cli")
    parser.add_argument("--bitcoin-cli-arg", action="append", default=[])
    parser.add_argument("--miner-wallet", default="miner")
    parser.add_argument("--script-type", default="p2wpkh",
                        choices=("p2wpkh", "p2wpkh-p2sh", "p2pkh"))
    parser.add_argument("--amount-sat", type=int, default=1_234_567)
    parser.add_argument("--amount-in-uri", action="store_true")
    parser.add_argument("--funding-utxos", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from electrum import constants
    from electrum.simple_config import SimpleConfig
    from electrum.plugin import Plugins
    from electrum.daemon import Daemon
    from electrum import version as electrum_version

    from electrum import util

    constants.BitcoinRegtest.set_as_network()
    util.create_and_start_event_loop()
    workdir = args.workdir.resolve()
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    config = SimpleConfig({
        "electrum_path": str(workdir / "electrum"),
        "regtest": True,
        "server": args.server,
        "oneserver": True,
        "auto_connect": False,
        "check_updates": False,
        "dont_show_testnet_warning": True,
    })

    # Test-only plugin key location (see module docstring).
    keyfile = workdir / "plugins_key"
    Plugins.keyfile_posix = str(keyfile)
    Plugins._has_root_permissions = lambda self, path: Path(path) == keyfile
    probe = object.__new__(Plugins)
    keyfile.write_text(Plugins.create_new_key(probe, PLUGIN_PASSWORD))

    plugin_dir = Path(config.electrum_path()) / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.zip, plugin_dir / args.zip.name)

    # Receiver and wallet.
    receiver_file = workdir / "receiver.json"
    created = receiver(args, "generate", "--output", str(receiver_file), "--network", "testnet")
    sp_address = created["silent_payment_address"]
    amount_btc = f"{args.amount_sat / 100_000_000:.8f}"
    sp_text = (
        f"bitcoin:?sp={sp_address}&amount={amount_btc}"
        if args.amount_in_uri else sp_address
    )

    wallet_path = Path(config.get_datadir_wallet_path()) / f"sp-{args.script_type}"
    wallet_path.parent.mkdir(parents=True, exist_ok=True)
    addresses = make_wallet(args, config, wallet_path, args.script_type)
    miner = [f"-rpcwallet={args.miner_wallet}"]
    for address in addresses[:args.funding_utxos]:
        bcli(args, *miner, "sendtoaddress", address, "0.01")
    bcli(args, "generatetoaddress", "1", bcli(args, *miner, "getnewaddress"))
    time.sleep(3)  # let the Electrum server index the funding block

    plugins = Plugins(config, gui_name=args.gui)
    privkey = Plugins.derive_privkey(PLUGIN_PASSWORD, bytes.fromhex(keyfile.read_text())[1:33])
    plugins.authorize_plugin(PLUGIN_NAME, privkey)
    plugins.load_plugin_by_name(PLUGIN_NAME)

    results = {
        "gui": args.gui,
        "electrum_version": electrum_version.ELECTRUM_VERSION,
        "plugin_zip": args.zip.name,
        "script_type": args.script_type,
        "amount_sat": args.amount_sat,
        "amount_in_uri": args.amount_in_uri,
        "silent_payment_address": sp_address,
    }
    daemon = Daemon(config, listen_jsonrpc=False)
    cwd = os.getcwd()
    import electrum
    try:
        if args.gui == "qml":
            from electrum.gui.qml import ElectrumGui
            # Electrum loads main.qml relative to its source root.
            os.chdir(Path(electrum.__file__).resolve().parents[1])
            gui = ElectrumGui(config, daemon, plugins)
            driver = QmlDriver(args, gui, wallet_path, sp_text, args.amount_sat, results)
        else:
            from electrum.gui.qt import ElectrumGui
            # Preload so the window opens without the wizard's password page.
            daemon.load_wallet(str(wallet_path), WALLET_PASSWORD)
            config.set_key("gui_last_wallet", str(wallet_path))
            config.CURRENT_WALLET = str(wallet_path)
            gui = ElectrumGui(config=config, daemon=daemon, plugins=plugins)
            driver = QtDriver(args, gui, wallet_path, sp_text, args.amount_sat, results)
        assert driver  # keep the driver alive for the event loop
        gui.main()
    finally:
        os.chdir(cwd)

    if "error" not in results:
        raw = bcli(args, "getrawtransaction", results["txid"])
        scan = receiver(args, "scan", "--receiver", str(receiver_file), "--raw-transaction", raw)
        results["receiver_scan"] = scan
        decoded = json.loads(bcli(args, "decoderawtransaction", raw))
        results["tx_rbf_signalled"] = any(
            vin["sequence"] < 0xFFFFFFFE for vin in decoded["vin"]
        )
        bcli(args, "generatetoaddress", "1", bcli(args, *miner, "getnewaddress"))
        tx = json.loads(bcli(args, "getrawtransaction", results["txid"], "1"))
        results["confirmations"] = tx.get("confirmations", 0)

    text = json.dumps(results, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.write_text(text + "\n")
    sys.stdout.flush()
    # Electrum's network and daemon threads would keep the process alive.
    os._exit(1 if "error" in results else 0)


if __name__ == "__main__":
    main()
