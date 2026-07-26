from pathlib import Path
import sys
import unittest

from tests import electrum_test_shim


electrum_test_shim.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silent_payments_sender.core import (  # noqa: E402
    DerivationFailure,
    InvalidSilentPaymentAddress,
    SilentPaymentAddress,
    UnsupportedWallet,
    derive_output_script,
    expected_hrp,
    is_silent_payment_request_text,
    parse_silent_payment_request,
    transaction_commitment,
)


ADDRESS = (
    "sp1qqgste7k9hx0qftg6qmwlkqtwuy6cycyavzmzj85c6qdfhjdpdjtdgqjue"
    "xzk6murw56suy3e0rd2cgqvycxttddwsvgxe2usfpxumr70xc9pkqwv"
)
PRIVATE_KEYS = [
    bytes.fromhex("eadc78165ff1f8ea94ad7cfdc54990738a4c53f6e0507b42154201b8e5dff3b1"),
    bytes.fromhex("93f5ed907ad5b2bdbbdcb5d9116ebc0a4e1f92f910d5260237fa45a9408aad16"),
]


def outpoint(txid, vout):
    return bytes.fromhex(txid)[::-1] + vout.to_bytes(4, "little")


class CoreTests(unittest.TestCase):

    def test_silent_payment_send_tab_detection(self):
        self.assertTrue(is_silent_payment_request_text(ADDRESS))
        self.assertTrue(is_silent_payment_request_text(f"bitcoin:?sp={ADDRESS}"))
        self.assertTrue(is_silent_payment_request_text("tsp1unfinished"))
        self.assertFalse(is_silent_payment_request_text("bc1qexample"))
        self.assertFalse(is_silent_payment_request_text("bitcoin:bc1qexample"))

    def test_mainnet_address_and_vector(self):
        recipient = SilentPaymentAddress.parse(ADDRESS, expected_hrp="sp")
        script = derive_output_script(
            private_keys=PRIVATE_KEYS,
            serialized_outpoints=[
                outpoint(
                    "f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16",
                    0,
                ),
                outpoint(
                    "a1075db55d416d3ca199f55b6084e2115b9345e16c5cf302fc80e9d5fbf5d48d",
                    0,
                ),
            ],
            recipient=recipient,
        )
        self.assertEqual(
            script.hex(),
            "51203e9fce73d4e77a4809908e3c3a2e54ee147b9312dc5044a193d1fc85de46e3c1",
        )

    def test_wire_lexicographic_outpoint_order_vector(self):
        recipient = SilentPaymentAddress.parse(ADDRESS, expected_hrp="sp")
        txid = "f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16"
        script = derive_output_script(
            private_keys=PRIVATE_KEYS,
            serialized_outpoints=[outpoint(txid, 1), outpoint(txid, 256)],
            recipient=recipient,
        )
        self.assertEqual(
            script.hex(),
            "5120a85ef8701394b517a4b35217c4bd37ac01ebeed4b008f8d0879f9e09ba95319c",
        )

    def test_known_testnet_address(self):
        address = (
            "tsp1qqvs8aztfcfxsjtf4y759uaxpyw6h68jd40ptwe95ecplsugn84qyyq467"
            "304jp07mnxyu2xygnpw5j9wxc3l89l63v2sjul7lef6jljhsyvqp9wq"
        )
        recipient = SilentPaymentAddress.parse(address, expected_hrp="tsp")
        self.assertEqual(
            recipient.scan_key.get_public_key_hex(),
            "03207e8969c24d092d3527a85e74c123b57d1e4dabc2b764b4ce03f871133d4042",
        )
        self.assertEqual(expected_hrp(is_testnet=True), "tsp")

    def test_wrong_hrp_rejected(self):
        with self.assertRaises(InvalidSilentPaymentAddress):
            SilentPaymentAddress.parse(ADDRESS, expected_hrp="tsp")

    def test_bech32_instead_of_bech32m_rejected(self):
        decoded = electrum_test_shim.bech32_decode(
            ADDRESS, ignore_long_length=True
        )
        malformed = electrum_test_shim.bech32_encode(
            electrum_test_shim.Encoding.BECH32,
            decoded.hrp,
            decoded.data,
        )
        with self.assertRaises(InvalidSilentPaymentAddress):
            SilentPaymentAddress.parse(malformed, expected_hrp="sp")

    def test_forward_compatible_address_versions(self):
        decoded = electrum_test_shim.bech32_decode(
            ADDRESS, ignore_long_length=True
        )
        payload = electrum_test_shim.convertbits(
            decoded.data[1:], 5, 8, False
        )
        forward_data = [1] + electrum_test_shim.convertbits(
            payload + [0xAA, 0xBB], 8, 5, True
        )
        address = electrum_test_shim.bech32_encode(
            electrum_test_shim.Encoding.BECH32M, "sp", forward_data
        )
        recipient = SilentPaymentAddress.parse(address, expected_hrp="sp")
        self.assertEqual(recipient.version, 1)
        self.assertEqual(
            recipient.scan_key.get_public_key_hex(),
            "0220bcfac5b99e04ad1a06ddfb016ee13582609d60b6291e98d01a9bc9a16c96d4",
        )

    def test_incompatible_address_version_31_rejected(self):
        decoded = electrum_test_shim.bech32_decode(
            ADDRESS, ignore_long_length=True
        )
        incompatible = electrum_test_shim.bech32_encode(
            electrum_test_shim.Encoding.BECH32M,
            "sp",
            [31] + decoded.data[1:],
        )
        with self.assertRaises(InvalidSilentPaymentAddress):
            SilentPaymentAddress.parse(incompatible, expected_hrp="sp")

    def test_bip21_sp_request_and_amount(self):
        request = parse_silent_payment_request(
            f"bitcoin:?sp={ADDRESS}&amount=0.00012345&label=Donation",
            expected_hrp="sp",
        )
        self.assertEqual(request.recipient.encoded, ADDRESS)
        self.assertEqual(request.amount_sat, 12_345)

    def test_bip21_rejects_required_unknown_and_duplicate_sp(self):
        with self.assertRaises(InvalidSilentPaymentAddress):
            parse_silent_payment_request(
                f"bitcoin:?sp={ADDRESS}&req-extra=1", expected_hrp="sp"
            )
        with self.assertRaises(InvalidSilentPaymentAddress):
            parse_silent_payment_request(
                f"bitcoin:?sp={ADDRESS}&sp={ADDRESS}", expected_hrp="sp"
            )

    def test_zero_sum_private_keys_rejected(self):
        recipient = SilentPaymentAddress.parse(ADDRESS, expected_hrp="sp")
        first = int.from_bytes(PRIVATE_KEYS[0], "big")
        inverse = (electrum_test_shim.N - first).to_bytes(32, "big")
        with self.assertRaises(DerivationFailure):
            derive_output_script(
                private_keys=[PRIVATE_KEYS[0], inverse],
                serialized_outpoints=[bytes(36), bytes([1]) + bytes(35)],
                recipient=recipient,
            )

    def test_commitment_is_input_order_independent(self):
        first = bytes(36)
        second = bytes([1]) + bytes(35)
        script = b"\x51\x20" + bytes(range(32))
        left = transaction_commitment(
            serialized_outpoints=[first, second],
            recipient_script=script,
            amount_sat=1234,
        )
        right = transaction_commitment(
            serialized_outpoints=[second, first],
            recipient_script=script,
            amount_sat=1234,
        )
        self.assertEqual(left, right)
        self.assertNotEqual(
            left,
            transaction_commitment(
                serialized_outpoints=[first, second],
                recipient_script=script,
                amount_sat=1235,
            ),
        )


if __name__ == "__main__":
    unittest.main()
