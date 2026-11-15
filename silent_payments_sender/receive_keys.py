"""Receiving keys and settings, kept in the Electrum wallet file.

The keys are BIP352's: the scan key at m/352'/coin'/0'/1'/0 and the spend key
at m/352'/coin'/0'/0'/0, from the wallet's BIP32 root. Electrum keeps the seed
only for Electrum seeds; for a BIP39 wallet the user types the seed words
once, and they are checked against the wallet's own key.

Stored: the scan secret (spscand needs it too, and it can only reveal
payments), the spend public key, and the spend secret encrypted with a key
derived from the wallet's account private key, so it is only readable with
the wallet password and survives password changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Optional

RECEIVE_KEY = "silent_payments_receive"
CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

# Electrum's network names, as spscand init --network wants them.
SPSCAND_NETWORKS = {
    "mainnet": "main",
    "testnet": "test",
    "testnet4": "testnet4",
    "signet": "signet",
    "regtest": "regtest",
}


class ReceiveSetupError(Exception):
    """A problem setting up receiving, shown to the user as is."""


@dataclass(frozen=True)
class ReceiveKeys:
    scan_secret: int
    spend_secret: int


def bip352_keys(root_seed: bytes, *, mainnet: bool) -> ReceiveKeys:
    from electrum.bip32 import BIP32Node

    root = BIP32Node.from_rootseed(root_seed, xtype="standard")
    coin = 0 if mainnet else 1

    def secret(path: str) -> int:
        return int.from_bytes(
            root.subkey_at_private_derivation(path).eckey.get_secret_bytes(), "big"
        )

    return ReceiveKeys(
        scan_secret=secret(f"m/352h/{coin}h/0h/1h/0"),
        spend_secret=secret(f"m/352h/{coin}h/0h/0h/0"),
    )


def _check_root_matches(keystore, root_seed: bytes) -> None:
    from electrum.bip32 import BIP32Node

    wallet_node = BIP32Node.from_xkey(keystore.xpub)
    prefix = keystore.get_derivation_prefix()
    if prefix is None:
        raise ReceiveSetupError(
            "This wallet's key path is unknown, so the seed cannot be "
            "checked against it."
        )
    derived = BIP32Node.from_rootseed(root_seed, xtype=wallet_node.xtype)
    derived = derived.subkey_at_private_derivation(prefix)
    if derived.to_xpub() != keystore.xpub:
        raise ReceiveSetupError(
            "These seed words (and passphrase) do not belong to this wallet."
        )


def wallet_root_seed(
    keystore,
    password: Optional[str],
    *,
    bip39_words: str = "",
    bip39_passphrase: str = "",
) -> bytes:
    """The wallet's BIP32 root seed, checked against its account key."""
    if keystore is None or getattr(keystore, "type", None) != "bip32":
        raise ReceiveSetupError(
            "Only deterministic BIP32 software wallets can receive silent payments."
        )
    if keystore.has_seed():
        from electrum.mnemonic import Mnemonic

        seed = Mnemonic.mnemonic_to_seed(
            keystore.get_seed(password), passphrase=keystore.get_passphrase(password)
        )
    else:
        from electrum.keystore import bip39_is_checksum_valid, bip39_to_seed

        words = " ".join(bip39_words.split())
        if not words:
            raise ReceiveSetupError(
                "Electrum does not keep this wallet's seed. Enter the "
                "BIP39 seed words it was restored from."
            )
        is_checksum_valid, is_wordlist_valid = bip39_is_checksum_valid(words)
        if not (is_checksum_valid and is_wordlist_valid):
            raise ReceiveSetupError("These are not valid BIP39 seed words.")
        seed = bip39_to_seed(words, passphrase=bip39_passphrase)
    _check_root_matches(keystore, seed)
    return seed


def _spend_key_wrapper(keystore, password: Optional[str]) -> bytes:
    """A key bound to the wallet's account private key, not to the password."""
    from electrum.bip32 import BIP32Node

    xprv = keystore.get_master_private_key(password)
    secret = BIP32Node.from_xkey(xprv).eckey.get_secret_bytes()
    return sha256(b"SilentPaymentsReceive/SpendKey\x00" + secret).digest()


def public_key(secret: int) -> bytes:
    from electrum_ecc import ECPrivkey

    return ECPrivkey(secret.to_bytes(32, "big")).get_public_key_bytes(compressed=True)


def encode_address(scan_pubkey: bytes, spend_pubkey: bytes, *, mainnet: bool) -> str:
    from electrum import segwit_addr

    data = [0] + list(segwit_addr.convertbits(scan_pubkey + spend_pubkey, 8, 5))
    return segwit_addr.bech32_encode(
        segwit_addr.Encoding.BECH32M, "sp" if mainnet else "tsp", data
    )


@dataclass(frozen=True)
class ReceiveSettings:
    scan_secret: int
    spend_pubkey: bytes
    spend_secret_encrypted: str
    birthday: int
    network: str  # Electrum's NET_NAME
    url: str = ""
    token: str = ""
    allow_unconfirmed: bool = False

    @property
    def mainnet(self) -> bool:
        return self.network == "mainnet"

    @property
    def address(self) -> str:
        return encode_address(
            public_key(self.scan_secret), self.spend_pubkey, mainnet=self.mainnet
        )

    def spend_secret(self, keystore, password: Optional[str]) -> int:
        from electrum.crypto import pw_decode_with_version_and_mac

        try:
            raw = pw_decode_with_version_and_mac(
                self.spend_secret_encrypted, _spend_key_wrapper(keystore, password)
            )
        except Exception:
            raise ReceiveSetupError(
                "The stored spend key does not match this wallet."
            ) from None
        secret = int.from_bytes(raw, "big")
        if public_key(secret) != self.spend_pubkey:
            raise ReceiveSetupError("The stored spend key does not match this wallet.")
        return secret

    def spscand_init_command(self) -> str:
        """Run where spscand lives; it then asks for the scan key."""
        return (
            f"spscand init --network {SPSCAND_NETWORKS[self.network]} "
            f"--address {self.address} --birthday {self.birthday}"
        )

    def to_json(self) -> dict:
        return {
            "scan_secret": f"{self.scan_secret:064x}",
            "spend_pubkey": self.spend_pubkey.hex(),
            "spend_secret_encrypted": self.spend_secret_encrypted,
            "birthday": self.birthday,
            "network": self.network,
            "url": self.url,
            "token": self.token,
            "allow_unconfirmed": self.allow_unconfirmed,
        }

    @classmethod
    def from_json(cls, data) -> "ReceiveSettings":
        try:
            settings = cls(
                scan_secret=int(data["scan_secret"], 16),
                spend_pubkey=bytes.fromhex(data["spend_pubkey"]),
                spend_secret_encrypted=str(data["spend_secret_encrypted"]),
                birthday=int(data["birthday"]),
                network=str(data["network"]),
                url=str(data.get("url", "")),
                token=str(data.get("token", "")),
                allow_unconfirmed=bool(data.get("allow_unconfirmed", False)),
            )
        except (KeyError, TypeError, ValueError):
            raise ReceiveSetupError(
                "The stored receiving settings are damaged."
            ) from None
        if (
            not 0 < settings.scan_secret < CURVE_ORDER
            or len(settings.spend_pubkey) != 33
        ):
            raise ReceiveSetupError("The stored receiving settings are damaged.")
        return settings


def create_settings(
    keystore,
    password: Optional[str],
    *,
    network: str,
    birthday: int,
    bip39_words: str = "",
    bip39_passphrase: str = "",
) -> ReceiveSettings:
    from electrum.crypto import pw_encode_with_version_and_mac

    if network not in SPSCAND_NETWORKS:
        raise ReceiveSetupError(f"spscand does not support the {network} network.")
    if birthday < 0:
        raise ReceiveSetupError("The start height cannot be negative.")
    seed = wallet_root_seed(
        keystore, password, bip39_words=bip39_words, bip39_passphrase=bip39_passphrase
    )
    keys = bip352_keys(seed, mainnet=network == "mainnet")
    encrypted = pw_encode_with_version_and_mac(
        keys.spend_secret.to_bytes(32, "big"), _spend_key_wrapper(keystore, password)
    )
    return ReceiveSettings(
        scan_secret=keys.scan_secret,
        spend_pubkey=public_key(keys.spend_secret),
        spend_secret_encrypted=encrypted,
        birthday=birthday,
        network=network,
    )


def load_settings(wallet) -> Optional[ReceiveSettings]:
    data = wallet.db.get(RECEIVE_KEY)
    return ReceiveSettings.from_json(data) if data else None


def save_settings(wallet, settings: ReceiveSettings) -> None:
    wallet.db.put(RECEIVE_KEY, settings.to_json())
    wallet.save_db()
