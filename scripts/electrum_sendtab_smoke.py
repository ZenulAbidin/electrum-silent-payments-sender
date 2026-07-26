#!/usr/bin/env python3
"""Smoke-test the packaged Qt Send-tab integration in an Electrum runtime."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import RLock
from types import ModuleType, SimpleNamespace
import zipfile
from xml.etree import ElementTree


TESTNET_ADDRESS = (
    "tsp1qqvs8aztfcfxsjtf4y759uaxpyw6h68jd40ptwe95ecplsugn84qyyq467"
    "304jp07mnxyu2xygnpw5j9wxc3l89l63v2sjul7lef6jljhsyvqp9wq"
)


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class Button:
    def __init__(self, func=lambda: None):
        self.func = func
        self.clicked = Signal()
        self.clicked.connect(func)
        self.enabled = False
        self.checked = False
        self.tooltip = ""

    def setEnabled(self, value):
        self.enabled = value

    def setChecked(self, value):
        self.checked = value

    def setToolTip(self, text):
        self.tooltip = text


class LineEdit:
    def __init__(self):
        self.value = ""

    def text(self):
        return self.value


class PayTo:
    def __init__(self):
        self.line_edit = LineEdit()
        self.textChanged = Signal()
        self.paymentIdentifierChanged = Signal()
        self.tooltip = ""
        self.style_sheet = ""
        self.native_try_calls = []

    def try_payment_identifier(self, text):
        self.native_try_calls.append(text)

    def setText(self, text):
        self.line_edit.value = text
        self.textChanged.emit()

    def setToolTip(self, text):
        self.tooltip = text

    def setStyleSheet(self, style_sheet):
        self.style_sheet = style_sheet


class AmountEdit:
    def __init__(self, amount=None):
        self.amount = amount
        self.textChanged = Signal()

    def get_amount(self):
        return self.amount

    def setAmount(self, amount):
        self.amount = amount
        self.textChanged.emit("")


class ErrorLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class SendTab:
    def __init__(self):
        self.payto_e = PayTo()
        self.amount_e = AmountEdit()
        self.save_button = Button()
        self.max_button = Button()
        self.invoice_error = ErrorLabel()
        self.native_pay_calls = 0
        self.send_button = Button(self.native_pay)
        self.validated = None
        self.update_fields_calls = 0

    def native_pay(self):
        self.native_pay_calls += 1

    def set_field_validated(self, _field, *, validated):
        self.validated = validated
        _field.setStyleSheet("green" if validated else "red")

    def update_fields(self):
        self.update_fields_calls += 1


class ImmediateTimer:
    @staticmethod
    def singleShot(_milliseconds, callback):
        callback()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", required=True)
    args = parser.parse_args()
    plugin_path = Path(args.plugin).resolve()
    sys.path.insert(0, str(plugin_path))

    from electrum import constants

    pyqt = ModuleType("PyQt6")
    qtcore = ModuleType("PyQt6.QtCore")
    qtcore.QTimer = ImmediateTimer
    pyqt.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt
    sys.modules["PyQt6.QtCore"] = qtcore
    from silent_payments_sender import qt as plugin_qt
    from silent_payments_sender.core import SilentPaymentAddress
    from silent_payments_sender.txflow import (
        OUTPUT_CONTINUATION_PREFIX,
        annotate_silent_payment_output,
    )
    from electrum.transaction import PartialTxOutput

    constants.BitcoinTestnet.set_as_network()
    plugin_qt.QTimer = ImmediateTimer

    with zipfile.ZipFile(plugin_path) as archive:
        manifest = json.loads(
            archive.read("silent_payments_sender/manifest.json")
        )
        icon_bytes = archive.read(
            f"silent_payments_sender/{manifest['icon']}"
        )

    icon_root = ElementTree.fromstring(icon_bytes)
    assert icon_root.tag.endswith("svg")
    assert icon_root.get("viewBox") == "0 0 180 180"

    recipient = SilentPaymentAddress.parse(
        TESTNET_ADDRESS,
        expected_hrp="tsp",
    )
    derived_script = b"\x51\x20" + bytes.fromhex("22" * 32)
    output = PartialTxOutput(scriptpubkey=derived_script, value=1_000)
    fake_tx = SimpleNamespace(outputs=lambda: [output])
    annotate_silent_payment_output(
        fake_tx,
        SimpleNamespace(output_index=0),
        recipient,
    )
    display = output.get_ui_address_str()
    assert len(display) == 37
    assert str(display).startswith("tsp1")
    assert "(tb1p" in str(display)
    assert output.address.startswith("tb1p")
    assert output._silent_payment_derived_address == output.address
    assert output._silent_payment_address == TESTNET_ADDRESS

    plugin = object.__new__(plugin_qt.Plugin)

    class FakeDB:
        def __init__(self):
            class StoredDict(dict):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.db_lock = RLock()

            self.data = {
                plugin_qt.SILENT_PAYMENT_RECORDS_KEY: StoredDict({
                    "00" * 32: StoredDict({
                        "recipient_address": TESTNET_ADDRESS,
                        "derived_address": output.address,
                    }),
                }),
            }

        def get(self, key, default=None):
            return self.data.get(key, default)

        def put(self, key, value):
            # Electrum JsonDB deep-copies values before storing them.
            self.data[key] = deepcopy(value)

    record_wallet = SimpleNamespace(
        db=FakeDB(),
        save_db_calls=0,
    )

    def save_db():
        record_wallet.save_db_calls += 1

    record_wallet.save_db = save_db
    plugin_qt._store_silent_payment_record(
        record_wallet,
        txid="11" * 32,
        recipient_address=TESTNET_ADDRESS,
        derived_address=output.address,
    )
    assert record_wallet.save_db_calls == 1
    assert set(record_wallet.db.data[plugin_qt.SILENT_PAYMENT_RECORDS_KEY]) == {
        "00" * 32,
        "11" * 32,
    }

    restored_output = PartialTxOutput(
        scriptpubkey=derived_script,
        value=1_000,
    )
    restored_tx = SimpleNamespace(
        txid=lambda: "11" * 32,
        outputs=lambda: [restored_output],
    )
    rendered = []
    dialog = SimpleNamespace(
        tx=restored_tx,
        wallet=record_wallet,
        io_widget=SimpleNamespace(update=lambda tx: rendered.append(tx)),
    )
    plugin.transaction_dialog_update(dialog)
    assert rendered == [restored_tx]
    restored_lines = str(
        restored_output.get_ui_address_str()
    ).splitlines()
    assert TESTNET_ADDRESS == (
        restored_lines[0]
        + restored_lines[1].removeprefix(OUTPUT_CONTINUATION_PREFIX)
        + restored_lines[2].removeprefix(OUTPUT_CONTINUATION_PREFIX)
    )
    assert restored_output.address == (
        restored_lines[3].removeprefix(OUTPUT_CONTINUATION_PREFIX)
        + restored_lines[4].removeprefix(OUTPUT_CONTINUATION_PREFIX).rstrip()
    )

    routed = []
    plugin._send_from_send_tab = lambda send_tab, text: routed.append(
        (send_tab, text)
    )
    send_tab = SendTab()
    plugin._integrate_send_tab(object.__new__(type(
        "Window", (), {"send_tab": send_tab}
    )))

    uri = f"bitcoin:?sp={TESTNET_ADDRESS}&amount=0.00012345"
    send_tab.payto_e.try_payment_identifier(uri)
    assert send_tab.validated is True
    assert send_tab.payto_e.style_sheet == "green"
    assert send_tab.amount_e.get_amount() == 12_345
    assert send_tab.send_button.enabled is True
    assert not send_tab.payto_e.native_try_calls

    send_tab.send_button.func()
    assert routed == [(send_tab, uri)]
    assert send_tab.native_pay_calls == 0

    # Electrum do_clear() emits textChanged before it sets
    # payment_identifier=None. Leaving Silent Payment mode must not call the
    # native update_fields() during that intermediate state.
    send_tab.payto_e.setText("")
    assert send_tab.update_fields_calls == 0
    assert send_tab.payto_e.style_sheet == (
        "QWidget { background-color: palette(base); }"
    )

    send_tab.payto_e.setText("bc1qordinary")
    send_tab.send_button.func()
    assert send_tab.native_pay_calls == 1

    send_tab.payto_e.try_payment_identifier("lnbcordinary")
    assert send_tab.payto_e.native_try_calls == ["lnbcordinary"]
    print("Electrum packaged Send-tab integration and SVG manifest asset: OK")


if __name__ == "__main__":
    main()
