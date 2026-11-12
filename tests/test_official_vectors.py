"""Sender conformance checks against bitcoin/bips BIP352 1.1.2 vectors.

Vendored from bitcoin/bips at a0190d01 (2026-11-05), BIP352 version 1.1.2.
"""

import json
from pathlib import Path
import sys
import unittest

from tests import electrum_test_shim


electrum_test_shim.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silent_payments_sender.core import (  # noqa: E402
    DerivationFailure,
    SilentPaymentAddress,
    derive_output_script,
)


VECTORS = json.loads(
    (Path(__file__).with_name("bip352-1.1.2-vectors.json")).read_text(
        encoding="utf-8"
    )
)

# These cases match the plugin's deliberately narrow scope: one recipient,
# compressed-key non-Taproot software inputs, and one derived payment output.
SUPPORTED_SUCCESS_CASES = (
    "Simple send: two inputs",
    "Simple send: two inputs, order reversed",
    "Simple send: two inputs from the same transaction",
    "Simple send: two inputs from the same transaction, order reversed",
    "Outpoint ordering byte-lexicographically vs. vout-integer",
    "Single recipient: multiple UTXOs from the same public key",
    "Receiving with labels: label with even parity",
    "Receiving with labels: label with odd parity",
    "Receiving with labels: large label integer",
    "Pubkey extraction from malleated p2pkh",
    "Input keys intermediate sum is zero but final sum is non-zero",
)
ZERO_SUM_CASE_PREFIX = "Input keys sum up to zero"

# Every other official vector needs a feature the plugin rejects up front.
OUT_OF_SCOPE_CASES = {
    "Single recipient: taproot only inputs with even y-values": "taproot inputs",
    "Single recipient: taproot only with mixed even/odd y-values": "taproot inputs",
    "Single recipient: taproot input with even y-value and non-taproot input": "taproot inputs",
    "Single recipient: taproot input with odd y-value and non-taproot input": "taproot inputs",
    "Multiple outputs: multiple outputs, same recipient": "multiple outputs",
    "Multiple outputs: multiple outputs, multiple recipients": "multiple recipients",
    "Multiple outputs with labels: un-labeled and labeled address; same recipient": "multiple outputs",
    "Multiple outputs with labels: multiple outputs for labeled address; same recipient": "multiple outputs",
    "Multiple outputs with labels: un-labeled, labeled, and multiple outputs for labeled address; same recipients": "multiple outputs",
    "Single recipient: use silent payments for sender change": "SP change",
    "Single recipient: taproot input with NUMS point": "taproot inputs",
    "P2PKH and P2WPKH Uncompressed Keys are skipped": "uncompressed keys rejected",
    "Skip invalid P2SH inputs": "non-wallet scripts rejected",
    "Recipient ignores unrelated outputs": "receiver-only",
    "No valid inputs, sender generates no outputs": "non-wallet scripts rejected",
    "Single recipient: taproot key-path spend with annex": "taproot inputs",
    "Maximum per-group recipient limit K_max is exceeded (2324 matches): sending fails, receiver doesn't scan beyond limit": (
        "one recipient per transaction, so K_max can never be reached"
    ),
}


def _vector(comment):
    matches = [vector for vector in VECTORS if vector["comment"] == comment]
    if len(matches) != 1:
        raise AssertionError(f"expected one official vector named {comment!r}")
    return matches[0]


def _outpoint(vin):
    return bytes.fromhex(vin["txid"])[::-1] + vin["vout"].to_bytes(4, "little")


class OfficialSenderVectorTests(unittest.TestCase):

    def test_supported_sender_vectors(self):
        for comment in SUPPORTED_SUCCESS_CASES:
            vector = _vector(comment)
            with self.subTest(comment=vector["comment"]):
                sending = vector["sending"][0]
                given = sending["given"]
                expected = sending["expected"]
                recipient_data = given["recipients"][0]
                recipient = SilentPaymentAddress.parse(
                    recipient_data["address"], expected_hrp="sp"
                )
                script = derive_output_script(
                    private_keys=[
                        bytes.fromhex(vin["private_key"]) for vin in given["vin"]
                    ],
                    serialized_outpoints=[
                        _outpoint(vin) for vin in given["vin"]
                    ],
                    recipient=recipient,
                )
                self.assertEqual(script.hex(), "5120" + expected["outputs"][0][0])

    def test_every_official_vector_is_classified(self):
        classified = set(SUPPORTED_SUCCESS_CASES) | set(OUT_OF_SCOPE_CASES)
        for vector in VECTORS:
            comment = vector["comment"]
            if comment.startswith(ZERO_SUM_CASE_PREFIX):
                continue
            with self.subTest(comment=comment):
                self.assertIn(comment, classified)

    def test_official_zero_sum_sender_vector(self):
        sending = [
            vector for vector in VECTORS
            if vector["comment"].startswith(ZERO_SUM_CASE_PREFIX)
        ][0]["sending"][0]
        given = sending["given"]
        recipient = SilentPaymentAddress.parse(
            given["recipients"][0]["address"], expected_hrp="sp"
        )
        with self.assertRaises(DerivationFailure):
            derive_output_script(
                private_keys=[
                    bytes.fromhex(vin["private_key"]) for vin in given["vin"]
                ],
                serialized_outpoints=[_outpoint(vin) for vin in given["vin"]],
                recipient=recipient,
            )


if __name__ == "__main__":
    unittest.main()
