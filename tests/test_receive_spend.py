"""Spending received outputs. Needs Electrum itself, not the test shim:

    PYTHONPATH=/path/to/electrum python3 -m unittest tests.test_receive_spend

The regtest harness (scripts/electrum_receive_harness.py) checks that Bitcoin
Core accepts the transactions.
"""

import sys
import unittest

# Ubuntu's apport hook trips an import-time assertion in exceptiongroup.
sys.excepthook = sys.__excepthook__

try:
    if not getattr(sys.modules.get("electrum"), "__file__", True):
        raise ImportError("the test shim is installed")
    from electrum import constants
    from electrum.bitcoin import address_to_script, pubkey_to_address
    from electrum.transaction import Transaction
    from electrum_ecc import ECPrivkey, ECPubkey
    from electrum_ecc.util import bip340_tagged_hash
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest(f"needs Electrum: {exc}")

from silent_payments_sender.receive_spend import (
    CURVE_ORDER,
    SpendError,
    build_signed_spend,
    estimated_vsize,
    output_secret,
    plan_fee,
)
from silent_payments_sender.spscand_client import ReceivedOutput

SPEND_SECRET = 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF
DESTINATION = CHANGE = ""  # regtest addresses, set once the network is


def public_key_hex(secret):
    return ECPrivkey(secret.to_bytes(32, "big")).get_public_key_hex()


def received(txid, amount, tweak, *, vout=0):
    secret = (SPEND_SECRET + tweak) % CURVE_ORDER
    xonly = ECPrivkey(secret.to_bytes(32, "big")).get_public_key_bytes()[1:]
    return ReceivedOutput(
        txid=txid * 64,
        vout=vout,
        amount=amount,
        pubkey=xonly.hex(),
        tweak=f"{tweak:064x}",
        label=None,
        status="confirmed",
        height=200,
        confirmations=2,
        spent_txid=None,
        spent_height=None,
        pending_spent_txid=None,
    )


class TestReceiveSpend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global DESTINATION, CHANGE
        constants.BitcoinRegtest.set_as_network()
        DESTINATION = pubkey_to_address("p2wpkh", public_key_hex(3))
        CHANGE = pubkey_to_address("p2wpkh", public_key_hex(4))

    @classmethod
    def tearDownClass(cls):
        constants.BitcoinMainnet.set_as_network()

    def test_output_secret_is_checked(self):
        good = received("a", 50_000, 777)
        self.assertEqual(output_secret(SPEND_SECRET, good), SPEND_SECRET + 777)
        bad = ReceivedOutput(**{**good.__dict__, "tweak": f"{778:064x}"})
        with self.assertRaisesRegex(SpendError, "does not give its key"):
            output_secret(SPEND_SECRET, bad)

    def test_odd_y_output_key_signs(self):
        # Find a tweak whose output key has an odd y: BIP340 negates the key.
        for tweak in range(1, 50):
            secret = SPEND_SECRET + tweak
            if ECPrivkey(secret.to_bytes(32, "big")).get_public_key_bytes()[0] == 3:
                break
        tx = build_signed_spend(
            [received("b", 40_000, tweak)],
            spend_secret=SPEND_SECRET,
            destination=DESTINATION,
            change_address=CHANGE,
            amount=None,
            fee_rate=2,
            locktime=200,
        )
        self.assertTrue(tx.is_complete())

    def test_fee_plan(self):
        dest, change = address_to_script(DESTINATION), address_to_script(CHANGE)
        outputs = [received("c", 30_000, 1), received("d", 20_000, 2)]
        send, left, fee = plan_fee(
            outputs,
            destination_script=dest,
            change_script=change,
            amount=None,
            fee_rate=3,
        )
        self.assertEqual((send + fee, left), (50_000, 0))
        self.assertEqual(fee, estimated_vsize(2, [dest]) * 3)
        send, left, fee = plan_fee(
            outputs,
            destination_script=dest,
            change_script=change,
            amount=10_000,
            fee_rate=3,
        )
        self.assertEqual(send + left + fee, 50_000)
        self.assertGreater(left, 0)
        # Change below the dust limit goes to the fee.
        send, left, fee = plan_fee(
            outputs,
            destination_script=dest,
            change_script=change,
            amount=49_300,
            fee_rate=1,
        )
        self.assertEqual((left, send + fee), (0, 50_000))
        with self.assertRaisesRegex(SpendError, "cover"):
            plan_fee(
                outputs,
                destination_script=dest,
                change_script=change,
                amount=60_000,
                fee_rate=1,
            )

    def test_signed_spend(self):
        outputs = [received("e", 60_000, 11), received("f", 25_000, 12, vout=3)]
        tx = build_signed_spend(
            outputs,
            spend_secret=SPEND_SECRET,
            destination=DESTINATION,
            change_address=CHANGE,
            amount=50_000,
            fee_rate=2,
            locktime=210,
        )
        raw = tx.serialize()
        parsed = Transaction(raw)
        self.assertEqual(parsed.locktime, 210)
        self.assertEqual(sorted(o.value for o in parsed.outputs())[-1], 50_000)
        fee = 85_000 - sum(o.value for o in parsed.outputs())
        self.assertEqual(
            parsed.estimated_size(),
            estimated_vsize(
                2, [address_to_script(DESTINATION), address_to_script(CHANGE)]
            ),
        )
        self.assertGreaterEqual(fee / parsed.estimated_size(), 2)
        self.assertTrue(all(i.nsequence == 0xFFFFFFFD for i in parsed.inputs()))
        for index, txin in enumerate(tx.inputs()):
            signature = txin.witness[2:66]
            self.assertEqual(txin.witness[:2], b"\x01\x40")
            spent = next(
                o for o in outputs if bytes.fromhex(o.txid) == txin.prevout.txid
            )
            message = bip340_tagged_hash(b"TapSighash", tx.serialize_preimage(index))
            key = ECPubkey(b"\x02" + bytes.fromhex(spent.pubkey))
            self.assertTrue(key.schnorr_verify(signature, message))

    def test_tampered_output_is_refused(self):
        good = received("a", 50_000, 5)
        bad = ReceivedOutput(**{**good.__dict__, "pubkey": "11" * 32})
        with self.assertRaises(SpendError):
            build_signed_spend(
                [bad],
                spend_secret=SPEND_SECRET,
                destination=DESTINATION,
                change_address=CHANGE,
                amount=None,
                fee_rate=1,
                locktime=0,
            )


if __name__ == "__main__":
    unittest.main()
