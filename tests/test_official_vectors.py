"""Sender conformance checks against bitcoin/bips BIP352 1.1.1 vectors."""

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
    (Path(__file__).with_name("bip352-1.1.1-vectors.json")).read_text(
        encoding="utf-8"
    )
)

# These cases match the plugin's deliberately narrow scope: one recipient,
# compressed-key non-Taproot software inputs, and one derived payment output.
SUPPORTED_SUCCESS_CASES = (0, 1, 2, 3, 4, 5, 12, 13, 14, 20, 26)
ZERO_SUM_CASE = 25


def _outpoint(vin):
    return bytes.fromhex(vin["txid"])[::-1] + vin["vout"].to_bytes(4, "little")


class OfficialSenderVectorTests(unittest.TestCase):

    def test_supported_sender_vectors(self):
        for case_index in SUPPORTED_SUCCESS_CASES:
            vector = VECTORS[case_index]
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

    def test_official_zero_sum_sender_vector(self):
        sending = VECTORS[ZERO_SUM_CASE]["sending"][0]
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
