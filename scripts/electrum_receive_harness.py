#!/usr/bin/env python3
"""Receive and spend silent payments in Electrum's Qt GUI with spscand, on regtest.

An end-to-end test, not a unit test. It packs the plugin from this tree,
installs it as an external plugin, creates a regtest wallet and its receiving
keys, and starts a real spscand (Bitcoin Core backend, watching the mempool)
for them. Payments are made with the BIP352 reference sender from spscand's
tests. The Silent Payments tab must show them unconfirmed, drop one replaced
with RBF, show the rest confirmed, and spend one through the Spend dialog,
broadcast by Electrum; the spend must show as pending, then spent, and the
coins must arrive in the wallet.

Requirements: Electrum (4.8.3+) with PyQt6 on PYTHONPATH; a regtest bitcoind
with a funded wallet named "miner" and an Electrum server; and a checkout of
spscand:

    PYTHONPATH=/path/to/electrum python3 scripts/electrum_receive_harness.py \\
        --spscand /path/to/spscand --bitcoin-conf /path/to/bitcoin.conf \\
        --server 127.0.0.1:60401:t --workdir /path/to/scratch --output result.json

The plugin-password key file normally has to be root-owned; as in
electrum_regtest_harness.py, it is put in the work directory. With
HARNESS_VERBOSE=1, Electrum logs to the work directory.
"""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Ubuntu's apport hook trips an import-time assertion in exceptiongroup.
sys.excepthook = sys.__excepthook__

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "silent_payments_sender"
PLUGIN_PASSWORD = "receive-harness-plugin-password"
WALLET_PASSWORD = "receive-harness-wallet-password"
PAY = Decimal("0.1")
PAY_SAT = 10_000_000


class HarnessFailure(Exception):
    pass


def pack_plugin(path: Path) -> None:
    plugin = ROOT / PLUGIN_NAME
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(plugin.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                bundle.write(
                    file, (Path(PLUGIN_NAME) / file.relative_to(plugin)).as_posix()
                )


class Spscand:
    def __init__(self, args, settings, birthday: int, node) -> None:
        self.dir = args.spscand
        self.python = args.spscand_python
        self.datadir = args.workdir / "spscand"
        self.port = 27352 + secrets.randbelow(1000)
        self.token = secrets.token_hex(16)
        self.cli(
            "init",
            "--network",
            "regtest",
            "--address",
            settings.address,
            "--birthday",
            str(birthday),
            "--backend",
            "core",
            stdin=f"{settings.scan_secret:064x}\n",
        )
        (self.datadir / "spscand.toml").write_text(
            f'[backend]\ntype = "core"\nrpc_url = "{node.base}"\n'
            f'rpc_user = "{node.user}"\nrpc_password = "{node.password}"\n\n'
            "[scan]\npoll_seconds = 1\n\n"
            '[mempool]\nsource = "auto"\npoll_seconds = 0.5\n\n'
            f'[api]\nlisten = "127.0.0.1:{self.port}"\ntoken = "{self.token}"\n'
        )
        self.log = open(args.workdir / "spscand.log", "w")
        self.python = args.spscand_python
        self.proc = subprocess.Popen(
            [self.python, "-m", "spscand", "--datadir", str(self.datadir), "run"],
            cwd=self.dir,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def cli(self, *argv, stdin=None):
        result = subprocess.run(
            [self.python, "-m", "spscand", "--datadir", str(self.datadir), *argv],
            cwd=self.dir,
            input=stdin,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise HarnessFailure(f"spscand {argv[0]}: {result.stderr}")
        return result.stdout

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(30)
        self.log.close()


class Driver:
    """Steps through the scenario on the Qt event loop, one check per tick."""

    def __init__(self, args, gui, plugin, chain, sender, settings, results):
        from PyQt6.QtCore import QTimer

        self.args, self.gui, self.plugin = args, gui, plugin
        self.chain, self.sender, self.settings = chain, sender, settings
        self.results = results
        self.deadline = time.monotonic() + args.timeout
        self.step = self.step_window
        self.timer = QTimer(gui.app)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def check(self, name, condition, detail=""):
        self.results["checks"].append({"check": name, "ok": bool(condition)})
        print(
            ("PASS " if condition else "FAIL ") + name,
            "" if condition else detail,
            flush=True,
        )
        if not condition:
            raise HarnessFailure(f"{name}: {detail}")

    def finish(self, error=None):
        self.timer.stop()
        if error:
            self.results["error"] = str(error)
        self.gui.app.quit()

    def tick(self):
        if time.monotonic() > self.deadline:
            self.finish(f"timeout in {self.step.__name__}")
            return
        try:
            next_step = self.step()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.finish(exc)
            return
        if next_step is not None:
            print(
                f"[receive-harness] {self.step.__name__} -> {next_step.__name__}",
                flush=True,
            )
            self.step = next_step
            if next_step == self.step_done:
                self.finish()

    @property
    def window(self):
        return self.gui.windows[0] if self.gui.windows else None

    @property
    def tab(self):
        return self.plugin._receive_tabs().get(self.window)

    def outputs(self):
        self.tab.refresh()
        return self.tab.outputs

    def step_window(self):
        window = self.window
        if window is None or self.tab is None:
            return None
        if not window.wallet.is_up_to_date() or not window.network.is_connected():
            now = time.monotonic()
            if now - getattr(self, "_last_wait_note", 0) > 10:
                self._last_wait_note = now
                print(
                    f"[receive-harness] waiting: connected={window.network.is_connected()} "
                    f"up_to_date={window.wallet.is_up_to_date()} "
                    f"height={window.network.get_local_height()}",
                    flush=True,
                )
            return None
        self.check("Silent Payments tab added", window.tabs.indexOf(self.tab) >= 0)
        self.check(
            "tab shows the wallet's address",
            self.tab.address_e.text() == self.settings.address,
        )
        from PyQt6.QtWidgets import QLineEdit
        from silent_payments_sender.receive_qt import ReceiveSettingsDialog

        dialog = ReceiveSettingsDialog(self.tab)
        texts = [e.text() for e in dialog.findChildren(QLineEdit)]
        dialog.reject()
        self.check(
            "settings show the spscand init command",
            self.settings.spscand_init_command() in texts,
            texts,
        )
        return self.step_synced

    def step_synced(self):
        self.tab.refresh()
        if "scanned to block" not in self.tab.status_label.text():
            return None
        self.check("tab reports spscand synced", True)
        recipient = {
            "address": self.settings.address,
            "scan_pub_key": self.results["scan_pubkey"],
            "spend_pub_key": self.settings.spend_pubkey.hex(),
        }
        coins = [self.sender.fund("p2wpkh", Decimal(1)) for _ in range(3)]
        self.sender.mine()
        self.coins = coins
        self.paid = [
            self.sender.pay([coins[0]], [recipient]),
            self.sender.pay([coins[1]], [recipient]),
        ]
        self.replaced = self.sender.pay([coins[2]], [recipient], replaceable=True)
        self.recipient = recipient
        return self.step_unconfirmed

    def step_unconfirmed(self):
        outputs = self.outputs()
        unconfirmed = {o.txid for o in outputs if not o.confirmed}
        if unconfirmed != set(self.paid) | {self.replaced}:
            return None
        from silent_payments_sender.receive_state import spendable

        self.check("payments listed while unconfirmed", True)
        fmt = self.window.format_amount_and_units
        text = self.tab.balance_label.text()
        self.check(
            "unconfirmed shown as incoming, not in the balance",
            text.startswith(f"Balance: {fmt(0)}")
            and f"Incoming, unconfirmed: {fmt(3 * PAY_SAT)}" in text,
            text,
        )
        self.check(
            "table rows say Unconfirmed",
            all(
                self.tab.table.topLevelItem(i).text(0) == "Unconfirmed"
                for i in range(self.tab.table.topLevelItemCount())
            ),
        )
        self.check(
            "unconfirmed payments not spendable by default",
            spendable(outputs, allow_unconfirmed=False) == [],
        )
        self.sender.pay([self.coins[2]], [], fee=Decimal("0.0003"), replaceable=True)
        return self.step_replaced

    def step_replaced(self):
        if any(o.txid == self.replaced for o in self.outputs()):
            return None
        self.check("payment replaced with RBF disappears", True)
        self.sender.mine()
        return self.step_confirmed

    def step_confirmed(self):
        outputs = self.outputs()
        if len(outputs) != 2 or not all(o.confirmed for o in outputs):
            return None
        self.check(
            "payments confirmed once mined",
            {o.txid for o in outputs} == set(self.paid),
            outputs,
        )
        fmt = self.window.format_amount_and_units
        self.check(
            "confirmed balance",
            self.tab.balance_label.text() == f"Balance: {fmt(2 * PAY_SAT)}",
            self.tab.balance_label.text(),
        )
        return self.step_spend

    def step_spend(self):
        from silent_payments_sender.receive_qt import SpendDialog

        window = self.window
        self.balance_before = window.wallet.get_balance()
        candidates = [o for o in self.tab.outputs if o.txid == self.paid[0]]
        dialog = SpendDialog(self.tab, self.tab.settings(), candidates)
        self.destination = dialog.destination_e.text()
        dialog.fee_rate_e.setText("2")
        captured = []
        window.get_password = lambda message=None: WALLET_PASSWORD
        dialog.question = lambda *a, **k: True
        window.broadcast_or_show = captured.append
        dialog.accept()
        self.check(
            "spend dialog signed a transaction",
            len(captured) == 1 and captured[0].is_complete(),
        )
        tx = captured[0]
        accepted = self.chain.call("testmempoolaccept", [tx.serialize()])[0]
        self.check("Bitcoin Core accepts the spend", accepted["allowed"], accepted)
        window.network.run_from_another_thread(window.network.broadcast_transaction(tx))
        self.spend_txid = tx.txid()
        self.check(
            "broadcast through Electrum's server",
            self.spend_txid in self.chain.call("getrawmempool"),
        )
        return self.step_pending_spend

    def step_pending_spend(self):
        spent = [o for o in self.outputs() if o.txid == self.paid[0]]
        if not spent or spent[0].pending_spent_txid != self.spend_txid:
            return None
        rows = [
            self.tab.table.topLevelItem(i).text(0)
            for i in range(self.tab.table.topLevelItemCount())
        ]
        self.check("spend shown as pending", "Spending (unconfirmed)" in rows, rows)
        self.check(
            "pending spend in the balance line",
            "Being spent" in self.tab.balance_label.text(),
            self.tab.balance_label.text(),
        )
        self.sender.mine()
        return self.step_spent

    def step_spent(self):
        spent = [o for o in self.outputs() if o.txid == self.paid[0]]
        wallet = self.window.wallet
        if not spent or not spent[0].spent or not wallet.is_up_to_date():
            return None
        confirmed, unconfirmed, _ = wallet.get_balance()[:3]
        if confirmed <= self.balance_before[0]:
            return None
        self.check(
            "spend confirmed and shown as spent", spent[0].spent_txid == self.spend_txid
        )
        gained = confirmed - self.balance_before[0]
        self.check(
            "coins arrived in the wallet", PAY_SAT - 1000 < gained < PAY_SAT, gained
        )
        return self.step_done

    def step_done(self):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spscand", type=Path, required=True)
    parser.add_argument(
        "--spscand-python", default="python3", help="Python 3.11+ to run spscand with"
    )
    parser.add_argument("--bitcoin-conf", type=Path, required=True)
    parser.add_argument("--server", default="127.0.0.1:60401:t")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--miner-wallet", default="miner")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.spscand = args.spscand.resolve()
    sys.path[:0] = [str(args.spscand), str(args.spscand / "tests")]
    from regtest_e2e import Node, Sender  # spscand's regtest helpers

    from electrum import constants, util
    from electrum.daemon import Daemon
    from electrum.mnemonic import Mnemonic
    from electrum.plugin import Plugins
    from electrum.simple_config import SimpleConfig
    from electrum.wallet import restore_wallet_from_text

    constants.BitcoinRegtest.set_as_network()
    util.create_and_start_event_loop()
    workdir = args.workdir = args.workdir.resolve()
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    config = SimpleConfig(
        {
            "electrum_path": str(workdir / "electrum"),
            "regtest": True,
            "server": args.server,
            "oneserver": True,
            "auto_connect": False,
            "check_updates": False,
            "dont_show_testnet_warning": True,
            **(
                {"verbosity": "*", "log_to_file": True}
                if os.environ.get("HARNESS_VERBOSE")
                else {}
            ),
        }
    )
    if os.environ.get("HARNESS_VERBOSE"):
        from electrum.logging import configure_logging

        configure_logging(config)
    keyfile = workdir / "plugins_key"
    Plugins.keyfile_posix = str(keyfile)
    Plugins._has_root_permissions = lambda self, path: Path(path) == keyfile
    keyfile.write_text(Plugins.create_new_key(object.__new__(Plugins), PLUGIN_PASSWORD))
    plugin_dir = Path(config.electrum_path()) / "plugins"
    plugin_dir.mkdir(parents=True)
    pack_plugin(plugin_dir / f"{PLUGIN_NAME}-dev.zip")

    node = Node(args.bitcoin_conf)
    chain = node.rpc()
    sender = Sender(node, args.miner_wallet)
    birthday = chain.call("getblockcount")

    sys.path.insert(0, str(ROOT))
    from silent_payments_sender.receive_keys import (
        create_settings,
        public_key,
        save_settings,
    )
    import dataclasses

    wallet_path = Path(config.get_datadir_wallet_path()) / "sp-receive"
    wallet_path.parent.mkdir(parents=True, exist_ok=True)
    wallet = restore_wallet_from_text(
        Mnemonic("en").make_seed(seed_type="segwit"),
        path=str(wallet_path),
        config=config,
        password=WALLET_PASSWORD,
        encrypt_file=True,
        gap_limit=20,
    )["wallet"]
    settings = create_settings(
        wallet.get_keystore(), WALLET_PASSWORD, network="regtest", birthday=birthday
    )
    spscand = Spscand(args, settings, birthday, node)
    settings = dataclasses.replace(settings, url=spscand.url, token=spscand.token)
    save_settings(wallet, settings)

    results = {
        "checks": [],
        "silent_payment_address": settings.address,
        "scan_pubkey": public_key(settings.scan_secret).hex(),
    }
    plugins = Plugins(config, gui_name="qt")
    privkey = Plugins.derive_privkey(
        PLUGIN_PASSWORD, bytes.fromhex(keyfile.read_text())[1:33]
    )
    plugins.authorize_plugin(PLUGIN_NAME, privkey)
    plugins.load_plugin_by_name(PLUGIN_NAME)
    plugin = plugins.get_plugin(PLUGIN_NAME)

    daemon = Daemon(config, listen_jsonrpc=False)
    from electrum.gui.qt import ElectrumGui

    try:
        daemon.load_wallet(str(wallet_path), WALLET_PASSWORD)
        config.set_key("gui_last_wallet", str(wallet_path))
        config.CURRENT_WALLET = str(wallet_path)
        gui = ElectrumGui(config=config, daemon=daemon, plugins=plugins)
        driver = Driver(args, gui, plugin, chain, sender, settings, results)
        assert driver
        gui.main()
    finally:
        spscand.stop()
        try:
            chain.call("unloadwallet", sender.name)
        except Exception:
            pass

    passed = sum(c["ok"] for c in results["checks"])
    print(
        f"{passed} of {len(results['checks'])} checks passed"
        + (f"; error: {results['error']}" if "error" in results else ""),
        flush=True,
    )
    if args.output:
        args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    os._exit(1 if "error" in results else 0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)  # Electrum's threads would keep the process alive
