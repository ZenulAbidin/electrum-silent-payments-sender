from pathlib import Path
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace
import sys
import unittest

from tests import electrum_test_shim


electrum_test_shim.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silent_payments_sender.core import (  # noqa: E402
    DerivationFailure,
    PLACEHOLDER_SCRIPT,
    SilentPaymentAddress,
    UnsupportedWallet,
)
from silent_payments_sender.txflow import (  # noqa: E402
    OUTPUT_CONTINUATION_PREFIX,
    OUTPUT_DISPLAY_WIDTH,
    annotate_silent_payment_output,
    confirm_transaction_compat,
    finalize_transaction,
    full_silent_payment_output_label,
    get_silent_payment_coins,
    make_unsigned_silent_transaction,
    normalize_silent_payment_records,
    seal_after_confirmation,
    silent_payment_history_label,
    silent_payment_output_label,
    validate_wallet,
    verify_transaction,
)


ADDRESS = (
    "sp1qqgste7k9hx0qftg6qmwlkqtwuy6cycyavzmzj85c6qdfhjdpdjtdgqjue"
    "xzk6murw56suy3e0rd2cgqvycxttddwsvgxe2usfpxumr70xc9pkqwv"
)
SECRETS = [
    bytes.fromhex("eadc78165ff1f8ea94ad7cfdc54990738a4c53f6e0507b42154201b8e5dff3b1"),
    bytes.fromhex("93f5ed907ad5b2bdbbdcb5d9116ebc0a4e1f92f910d5260237fa45a9408aad16"),
]
TXIDS = [
    "f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16",
    "a1075db55d416d3ca199f55b6084e2115b9345e16c5cf302fc80e9d5fbf5d48d",
]


class FakeOutpoint:
    def __init__(self, txid, vout):
        self.txid = bytes.fromhex(txid)
        self.vout = vout

    def serialize_to_network(self):
        return self.txid[::-1] + self.vout.to_bytes(4, "little")


class FakeOutput:
    def __init__(self, scriptpubkey, value):
        self.scriptpubkey = scriptpubkey
        self.value = value
        self.address = "bc1ptestderived"

    def serialize_to_network(self):
        return (
            self.value.to_bytes(8, "little")
            + bytes([len(self.scriptpubkey)])
            + self.scriptpubkey
        )


class FakeTx:
    def __init__(self):
        self._inputs = [
            SimpleNamespace(address="mine-0", prevout=FakeOutpoint(TXIDS[0], 0)),
            SimpleNamespace(address="mine-1", prevout=FakeOutpoint(TXIDS[1], 0)),
        ]
        self._outputs = [FakeOutput(PLACEHOLDER_SCRIPT, 50_000)]
        self.rbf = True

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def set_rbf(self, value):
        self.rbf = value

    def invalidate_ser_cache(self):
        pass

    def serialize_to_network(self, *, include_sigs=False):
        material = b"".join(
            txin.prevout.serialize_to_network() for txin in self._inputs
        )
        material += bytes([self.rbf])
        for output in self._outputs:
            material += output.value.to_bytes(8, "little") + output.scriptpubkey
        return material.hex()


class FakeKeystore:
    type = "bip32"

    def get_private_key(self, index, password):
        return SECRETS[index], True


class FakeWallet:
    wallet_type = "standard"

    def __init__(self):
        self.keystore = FakeKeystore()

    def is_watching_only(self):
        return False

    def get_keystore(self):
        return self.keystore

    def get_txin_type(self, address=None):
        return "p2wpkh"

    def add_input_info(self, txin):
        pass

    def is_mine(self, address):
        return address in ("mine-0", "mine-1")

    def get_address_index(self, address):
        return int(address[-1])


class TxFlowTests(unittest.TestCase):

    @staticmethod
    def _change_policy_fixture(
        *,
        selected_change_addresses,
        transaction_change_addresses,
        multiple_change,
        fee=1_000,
        estimated_fee=1_000,
        merge_duplicate_outputs=False,
        send_change_to_lightning=False,
    ):
        outputs = [
            SimpleNamespace(
                address="bc1psilent",
                is_change=False,
                scriptpubkey=PLACEHOLDER_SCRIPT,
            ),
            *[
                SimpleNamespace(
                    address=address,
                    is_change=True,
                    scriptpubkey=b"change",
                )
                for address in transaction_change_addresses
            ],
        ]
        tx = SimpleNamespace(
            outputs=lambda: outputs,
            inputs=lambda: [SimpleNamespace(address="bc1qinput")],
            get_fee=lambda: fee,
            estimated_size=lambda: 200,
            locktime=0,
        )

        class Wallet:
            max_change_outputs = 3
            network = None

            def __init__(self):
                self.use_change = bool(selected_change_addresses)
                self.multiple_change = multiple_change
                self.make_kwargs = None
                self.config = SimpleNamespace(
                    WALLET_SPEND_CONFIRMED_ONLY=False,
                    WALLET_MERGE_DUPLICATE_OUTPUTS=merge_duplicate_outputs,
                    WALLET_COIN_CHOOSER_OUTPUT_ROUNDING=True,
                    WALLET_COIN_CHOOSER_POLICY="Privacy",
                    WALLET_SEND_CHANGE_TO_LIGHTNING=send_change_to_lightning,
                    WALLET_ENABLE_SUBMARINE_PAYMENTS=False,
                )

            def get_change_addresses_for_new_transaction(self):
                return list(selected_change_addresses)

            def make_unsigned_transaction(self, **kwargs):
                self.make_kwargs = kwargs
                return tx

            def dust_threshold(self):
                return 546

        fee_policy = SimpleNamespace(
            estimate_fee=lambda _size, network=None: estimated_fee,
            get_descriptor=lambda: "fixed:1000",
        )
        return Wallet(), tx, fee_policy

    def test_confirmed_input_setting_filters_manual_coin_selection(self):
        confirmed = SimpleNamespace(
            prevout=SimpleNamespace(
                serialize_to_network=lambda: b"confirmed",
            ),
        )
        unconfirmed = SimpleNamespace(
            prevout=SimpleNamespace(
                serialize_to_network=lambda: b"unconfirmed",
            ),
        )

        class Wallet:
            def get_spendable_coins(
                self,
                _domain,
                *,
                nonlocal_only,
                confirmed_only,
            ):
                self.call = (nonlocal_only, confirmed_only)
                return [confirmed]

        wallet = Wallet()
        get_coins_calls = []
        window = SimpleNamespace(
            config=SimpleNamespace(WALLET_SPEND_CONFIRMED_ONLY=True),
            wallet=wallet,
            get_coins=lambda **kwargs: (
                get_coins_calls.append(kwargs) or [confirmed, unconfirmed]
            ),
        )
        self.assertEqual(
            [confirmed],
            get_silent_payment_coins(window=window),
        )
        self.assertEqual(
            [{"nonlocal_only": False, "confirmed_only": True}],
            get_coins_calls,
        )
        self.assertEqual((False, True), wallet.call)

    def test_unconfirmed_input_setting_allows_unconfirmed_coins(self):
        coins = [
            SimpleNamespace(
                prevout=SimpleNamespace(
                    serialize_to_network=lambda: b"unconfirmed",
                ),
            ),
        ]
        window = SimpleNamespace(
            config=SimpleNamespace(WALLET_SPEND_CONFIRMED_ONLY=False),
            wallet=SimpleNamespace(),
            get_coins=lambda **_kwargs: coins,
        )
        self.assertEqual(
            coins,
            get_silent_payment_coins(window=window),
        )

    def test_confirmation_dialog_can_request_confirmed_coins_conservatively(self):
        confirmed = SimpleNamespace(
            prevout=SimpleNamespace(
                serialize_to_network=lambda: b"confirmed",
            ),
        )
        window = SimpleNamespace(
            config=SimpleNamespace(WALLET_SPEND_CONFIRMED_ONLY=False),
            wallet=SimpleNamespace(
                get_spendable_coins=lambda *_args, **_kwargs: [confirmed],
            ),
            get_coins=lambda **_kwargs: [confirmed],
        )
        self.assertEqual(
            [confirmed],
            get_silent_payment_coins(
                window=window,
                confirmed_only=True,
            ),
        )

    def test_change_addresses_are_passed_to_electrum(self):
        addresses = ["bc1qchange0", "bc1qchange1", "bc1qchange2"]
        wallet, tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=addresses,
            transaction_change_addresses=addresses,
            multiple_change=True,
        )
        result = make_unsigned_silent_transaction(
            wallet=wallet,
            fee_policy=fee_policy,
            coins=["coin"],
            outputs=["silent-output"],
        )
        self.assertIs(result, tx)
        self.assertEqual(addresses, wallet.make_kwargs["change_addr"])
        self.assertFalse(wallet.make_kwargs["rbf"])
        self.assertFalse(wallet.make_kwargs["merge_duplicate_outputs"])
        self.assertEqual(
            addresses,
            list(tx._silent_payment_settings.change_addresses),
        )
        self.assertEqual(
            "fixed:1000",
            tx._silent_payment_settings.fee_policy,
        )
        self.assertFalse(
            tx._silent_payment_settings.send_change_to_lightning
        )
        self.assertFalse(
            tx._silent_payment_settings.submarine_payments_enabled
        )

    def test_effective_confirmed_only_decision_is_recorded(self):
        wallet, tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=["bc1qchange0"],
            multiple_change=False,
        )
        make_unsigned_silent_transaction(
            wallet=wallet,
            fee_policy=fee_policy,
            coins=["coin"],
            outputs=["silent-output"],
            spend_confirmed_only=True,
        )
        self.assertTrue(
            tx._silent_payment_settings.spend_confirmed_only
        )

    def test_change_returns_to_input_when_change_addresses_are_disabled(self):
        wallet, tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=[],
            transaction_change_addresses=["bc1qinput"],
            multiple_change=False,
        )
        result = make_unsigned_silent_transaction(
            wallet=wallet,
            fee_policy=fee_policy,
            coins=["coin"],
            outputs=["silent-output"],
        )
        self.assertIs(result, tx)
        self.assertEqual([], wallet.make_kwargs["change_addr"])

    def test_multiple_change_setting_is_enforced(self):
        wallet, _tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=["bc1qchange0", "bc1qchange1"],
            multiple_change=False,
        )
        with self.assertRaisesRegex(
            DerivationFailure,
            "multiple change outputs",
        ):
            make_unsigned_silent_transaction(
                wallet=wallet,
                fee_policy=fee_policy,
                coins=["coin"],
                outputs=["silent-output"],
            )

    def test_excessive_fee_without_change_is_rejected(self):
        wallet, _tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=[],
            multiple_change=False,
            fee=50_000,
        )
        with self.assertRaisesRegex(
            DerivationFailure,
            "assigned the remainder",
        ):
            make_unsigned_silent_transaction(
                wallet=wallet,
                fee_policy=fee_policy,
                coins=["coin"],
                outputs=["silent-output"],
            )

    def test_dust_remainder_without_change_is_rejected(self):
        wallet, _tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=[],
            multiple_change=False,
            fee=700,
            estimated_fee=200,
        )
        with self.assertRaisesRegex(
            DerivationFailure,
            "assigned the remainder",
        ):
            make_unsigned_silent_transaction(
                wallet=wallet,
                fee_policy=fee_policy,
                coins=["800-sat coin"],
                outputs=["100-sat silent output"],
            )

    def test_exact_no_change_transaction_is_allowed(self):
        wallet, tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=[],
            multiple_change=False,
            fee=1_000,
            estimated_fee=1_000,
        )
        self.assertIs(
            tx,
            make_unsigned_silent_transaction(
                wallet=wallet,
                fee_policy=fee_policy,
                coins=["exact-value coin"],
                outputs=["silent-output"],
            ),
        )

    def test_merge_duplicate_outputs_setting_is_passed_to_electrum(self):
        wallet, tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=["bc1qchange0"],
            multiple_change=False,
            merge_duplicate_outputs=True,
        )
        make_unsigned_silent_transaction(
            wallet=wallet,
            fee_policy=fee_policy,
            coins=["coin"],
            outputs=["silent-output"],
        )
        self.assertTrue(wallet.make_kwargs["merge_duplicate_outputs"])
        self.assertTrue(
            tx._silent_payment_settings.merge_duplicate_outputs
        )

    def test_send_change_to_lightning_is_explicitly_rejected(self):
        wallet, _tx, fee_policy = self._change_policy_fixture(
            selected_change_addresses=["bc1qchange0"],
            transaction_change_addresses=["bc1qchange0"],
            multiple_change=False,
            send_change_to_lightning=True,
        )
        with self.assertRaisesRegex(
            UnsupportedWallet,
            "Sending change to Lightning",
        ):
            make_unsigned_silent_transaction(
                wallet=wallet,
                fee_policy=fee_policy,
                coins=["coin"],
                outputs=["silent-output"],
            )

    def test_electrum_46_confirmation_api(self):
        class Window46:
            def confirm_tx_dialog(
                self, make_tx, output_value, allow_preview=True,
                batching_candidates=None,
            ):
                self.call = (make_tx, output_value, batching_candidates)
                return "tx46", False

        window = Window46()
        result = confirm_transaction_compat(
            window=window, make_tx="factory", amount_sat=123
        )
        self.assertEqual(result, ("tx46", False, False))
        self.assertEqual(window.call, ("factory", 123, []))

    def test_electrum_47_confirmation_api(self):
        class Window47:
            def confirm_tx_dialog(
                self, make_tx, output_value, *,
                payee_outputs=None, context=None, batching_candidates=None,
            ):
                self.call = (
                    make_tx, output_value, payee_outputs, batching_candidates
                )
                return "tx47", False, False

        window = Window47()
        result = confirm_transaction_compat(
            window=window, make_tx="factory", amount_sat=456
        )
        self.assertEqual(result, ("tx47", False, False))
        self.assertEqual(window.call, ("factory", 456, None, []))

    def test_finalize_and_verify(self):
        wallet = FakeWallet()
        tx = FakeTx()
        recipient = SilentPaymentAddress.parse(ADDRESS, expected_hrp="sp")
        finalized = finalize_transaction(
            wallet=wallet,
            tx=tx,
            password=None,
            recipient=recipient,
        )
        self.assertFalse(tx.rbf)
        self.assertEqual(
            finalized.scriptpubkey.hex(),
            "51203e9fce73d4e77a4809908e3c3a2e54ee147b9312dc5044a193d1fc85de46e3c1",
        )
        self.assertTrue(verify_transaction(tx, finalized))
        annotate_silent_payment_output(tx, finalized, recipient)
        self.assertEqual(
            "sp1qqgst…xc9pkqwv (bc1ptestderived)",
            tx.outputs()[0].get_ui_address_str(),
        )
        self.assertEqual(
            ADDRESS,
            tx.outputs()[0]._silent_payment_address,
        )
        self.assertTrue(
            tx.outputs()[0]._silent_payment_derived_address.startswith("bc1p")
        )
        self.assertTrue(verify_transaction(tx, finalized))
        tx.rbf = True
        self.assertFalse(verify_transaction(tx, finalized))
        finalized = seal_after_confirmation(tx, finalized)
        self.assertFalse(tx.rbf)
        self.assertTrue(verify_transaction(tx, finalized))
        tx.outputs()[0].value += 1
        self.assertFalse(verify_transaction(tx, finalized))

    def test_send_preview_label_abbreviates_both_addresses(self):
        recipient = "sp1" + ("q" * 114)
        derived = "bc1p" + ("x" * 58)
        label = silent_payment_output_label(
            recipient_address=recipient,
            derived_address=derived,
        )
        self.assertEqual(37, len(label))
        self.assertEqual(
            "sp1qqqqq…qqqqqqqq (bc1pxxxx…xxxxxxxx)",
            label,
        )

    def test_final_details_keep_both_complete_addresses_wrapped(self):
        recipient = "sp1" + ("q" * 114)
        derived = "bc1p" + ("x" * 58)
        label = full_silent_payment_output_label(
            recipient_address=recipient,
            derived_address=derived,
        )
        self.assertEqual(42, len(label))
        lines = str(label).splitlines()
        self.assertEqual(5, len(lines))
        self.assertEqual(recipient[:42], lines[0])
        self.assertEqual(
            recipient,
            lines[0]
            + lines[1].removeprefix(OUTPUT_CONTINUATION_PREFIX)
            + lines[2].removeprefix(OUTPUT_CONTINUATION_PREFIX),
        )
        self.assertEqual(
            derived,
            lines[3].removeprefix(OUTPUT_CONTINUATION_PREFIX)
            + lines[4].removeprefix(OUTPUT_CONTINUATION_PREFIX).rstrip(),
        )
        self.assertEqual(
            OUTPUT_DISPLAY_WIDTH + len(OUTPUT_CONTINUATION_PREFIX),
            len(lines[-1]),
        )

    def test_history_label_keeps_description_concise(self):
        recipient = "sp1" + ("q" * 114)
        derived = "bc1p" + ("x" * 58)
        label = silent_payment_history_label(
            recipient_address=recipient,
            derived_address=derived,
            description="Coffee",
        )
        self.assertEqual("Coffee", label)
        self.assertEqual(
            "Silent Payment",
            silent_payment_history_label(
                recipient_address=recipient,
                derived_address=derived,
            ),
        )

    def test_wallet_db_records_are_detached_from_stored_dict_wrappers(self):
        class StoredDict(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.db_lock = RLock()

        records = StoredDict({
            "txid-one": StoredDict({
                "recipient_address": "sp1recipient",
                "derived_address": "bc1pderived",
            }),
        })
        normalized = normalize_silent_payment_records(records)

        self.assertIs(type(normalized), dict)
        self.assertIs(type(normalized["txid-one"]), dict)
        self.assertEqual(
            {
                "txid-one": {
                    "recipient_address": "sp1recipient",
                    "derived_address": "bc1pderived",
                },
            },
            deepcopy(normalized),
        )

    def test_seal_rejects_output_mutation(self):
        wallet = FakeWallet()
        tx = FakeTx()
        recipient = SilentPaymentAddress.parse(ADDRESS, expected_hrp="sp")
        finalized = finalize_transaction(
            wallet=wallet,
            tx=tx,
            password=None,
            recipient=recipient,
        )
        tx.outputs()[0].value += 1
        with self.assertRaises(DerivationFailure):
            seal_after_confirmation(tx, finalized)

    def test_watch_only_rejected(self):
        wallet = FakeWallet()
        wallet.is_watching_only = lambda: True
        with self.assertRaises(UnsupportedWallet):
            validate_wallet(wallet)

    def test_hardware_keystore_rejected(self):
        wallet = FakeWallet()
        wallet.keystore.type = "hardware"
        with self.assertRaises(UnsupportedWallet):
            validate_wallet(wallet)


if __name__ == "__main__":
    unittest.main()
