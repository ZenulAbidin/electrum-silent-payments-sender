"""Receiving keys and settings. Needs Electrum itself, not the test shim:

PYTHONPATH=/path/to/electrum python3 -m unittest tests.test_receive_keys
"""

import hashlib
import hmac
import sys
import unittest

from tests import electrum_test_shim as shim

# Ubuntu's apport hook trips an import-time assertion in exceptiongroup.
sys.excepthook = sys.__excepthook__

try:
    if not getattr(sys.modules.get("electrum"), "__file__", True):
        raise ImportError("the test shim is installed")
    from electrum import constants
    from electrum import keystore as electrum_keystore
    from electrum.mnemonic import Mnemonic
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest(f"needs Electrum: {exc}")

from silent_payments_sender.receive_keys import (
    ReceiveSettings,
    ReceiveSetupError,
    create_settings,
    public_key,
)

BIP39_WORDS = "abandon " * 11 + "about"
ELECTRUM_SEED = (
    "wild father tree among universe such mobile favorite target dynamic credit identify"
)
PASSWORD = "correct horse"


def _ser_point(point) -> bytes:
    x, y = point
    return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")


def reference_secret(root_seed: bytes, path: list) -> int:
    """BIP32 private derivation, written out independently of Electrum."""
    digest = hmac.new(b"Bitcoin seed", root_seed, hashlib.sha512).digest()
    secret, chain = int.from_bytes(digest[:32], "big"), digest[32:]
    for index in path:
        if index & 0x80000000:
            data = b"\x00" + secret.to_bytes(32, "big")
        else:
            data = _ser_point(shim.point_mul(secret, shim.G_POINT))
        digest = hmac.new(
            chain, data + index.to_bytes(4, "big"), hashlib.sha512
        ).digest()
        secret = (int.from_bytes(digest[:32], "big") + secret) % shim.N
        chain = digest[32:]
    return secret


H = 0x80000000


def bip39_keystore(words=BIP39_WORDS, passphrase=""):
    seed = electrum_keystore.bip39_to_seed(words, passphrase=passphrase)
    ks = electrum_keystore.from_bip43_rootseed(
        seed, derivation="m/84h/1h/0h", xtype="p2wpkh"
    )
    ks.update_password(None, PASSWORD)
    return ks


def electrum_keystore_from_seed():
    ks = electrum_keystore.from_seed(ELECTRUM_SEED, passphrase="", for_multisig=False)
    ks.update_password(None, PASSWORD)
    return ks


class TestReceiveKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        constants.BitcoinRegtest.set_as_network()

    @classmethod
    def tearDownClass(cls):
        constants.BitcoinMainnet.set_as_network()

    def test_bip39_wallet_uses_bip352_paths(self):
        ks = bip39_keystore()
        settings = create_settings(
            ks, PASSWORD, network="regtest", birthday=120, bip39_words=BIP39_WORDS
        )
        root = electrum_keystore.bip39_to_seed(BIP39_WORDS, passphrase="")
        self.assertEqual(
            settings.scan_secret, reference_secret(root, [352 | H, 1 | H, H, 1 | H, 0])
        )
        spend = reference_secret(root, [352 | H, 1 | H, H, H, 0])
        self.assertEqual(settings.spend_pubkey, public_key(spend))
        self.assertEqual(settings.spend_secret(ks, PASSWORD), spend)
        self.assertTrue(settings.address.startswith("tsp1q"))
        self.assertEqual(
            settings.spscand_init_command(),
            f"spscand init --network regtest --address {settings.address} "
            "--birthday 120",
        )

    def test_mainnet_coin_type(self):
        ks = bip39_keystore()
        settings = create_settings(
            ks, PASSWORD, network="mainnet", birthday=0, bip39_words=BIP39_WORDS
        )
        root = electrum_keystore.bip39_to_seed(BIP39_WORDS, passphrase="")
        self.assertEqual(
            settings.scan_secret, reference_secret(root, [352 | H, H, H, 1 | H, 0])
        )
        self.assertTrue(settings.address.startswith("sp1q"))

    def test_electrum_seed_needs_no_words(self):
        ks = electrum_keystore_from_seed()
        settings = create_settings(ks, PASSWORD, network="regtest", birthday=5)
        root = Mnemonic.mnemonic_to_seed(ELECTRUM_SEED, passphrase="")
        self.assertEqual(
            settings.scan_secret, reference_secret(root, [352 | H, 1 | H, H, 1 | H, 0])
        )

    def test_wrong_words_or_passphrase_are_refused(self):
        ks = bip39_keystore(passphrase="secret")
        with self.assertRaisesRegex(ReceiveSetupError, "do not belong"):
            create_settings(
                ks, PASSWORD, network="regtest", birthday=0, bip39_words=BIP39_WORDS
            )
        with self.assertRaisesRegex(ReceiveSetupError, "not valid BIP39"):
            create_settings(
                ks, PASSWORD, network="regtest", birthday=0, bip39_words="abandon " * 12
            )
        with self.assertRaisesRegex(ReceiveSetupError, "Enter the BIP39"):
            create_settings(ks, PASSWORD, network="regtest", birthday=0)
        settings = create_settings(
            ks,
            PASSWORD,
            network="regtest",
            birthday=0,
            bip39_words=BIP39_WORDS,
            bip39_passphrase="secret",
        )
        self.assertTrue(settings.address)

    def test_spend_key_survives_a_password_change(self):
        ks = bip39_keystore()
        settings = create_settings(
            ks, PASSWORD, network="regtest", birthday=0, bip39_words=BIP39_WORDS
        )
        before = settings.spend_secret(ks, PASSWORD)
        ks.update_password(PASSWORD, "new password")
        stored = ReceiveSettings.from_json(settings.to_json())
        self.assertEqual(stored.spend_secret(ks, "new password"), before)

    def test_spend_key_of_another_wallet_is_refused(self):
        settings = create_settings(
            bip39_keystore(),
            PASSWORD,
            network="regtest",
            birthday=0,
            bip39_words=BIP39_WORDS,
        )
        with self.assertRaisesRegex(ReceiveSetupError, "does not match"):
            settings.spend_secret(electrum_keystore_from_seed(), PASSWORD)

    def test_damaged_settings(self):
        for data in (
            {},
            {"scan_secret": "zz"},
            {
                "scan_secret": "00" * 32,
                "spend_pubkey": "02" + "11" * 32,
                "spend_secret_encrypted": "x",
                "birthday": 0,
                "network": "regtest",
            },
        ):
            with self.subTest(data), self.assertRaises(ReceiveSetupError):
                ReceiveSettings.from_json(data)


if __name__ == "__main__":
    unittest.main()
