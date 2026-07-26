"""BIP352 sender derivation.

Adapted from Electrum PR #9900 by MorenoProg and Electrum contributors,
licensed under the MIT license. See NOTICE.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence
from urllib.parse import parse_qsl, urlsplit

import electrum_ecc as ecc
from electrum_ecc import ECPubkey, ECPrivkey
from electrum_ecc.util import bip340_tagged_hash

from electrum import segwit_addr


PLACEHOLDER_SCRIPT = b"\x51\x20" + bytes(32)
SUPPORTED_TXIN_TYPES = frozenset({"p2pkh", "p2wpkh", "p2wpkh-p2sh"})


class SilentPaymentError(Exception):
    """Base class for user-facing plugin errors."""


class InvalidSilentPaymentAddress(SilentPaymentError):
    pass


class UnsupportedWallet(SilentPaymentError):
    pass


class DerivationFailure(SilentPaymentError):
    pass


@dataclass(frozen=True)
class SilentPaymentAddress:
    encoded: str
    version: int
    scan_key: ECPubkey
    spend_key: ECPubkey

    @classmethod
    def parse(cls, address: str, *, expected_hrp: str) -> "SilentPaymentAddress":
        address = address.strip()
        try:
            decoded = segwit_addr.bech32_decode(address, ignore_long_length=True)
            if decoded.encoding != segwit_addr.Encoding.BECH32M:
                raise ValueError("silent payment addresses must use Bech32m")
            if decoded.hrp != expected_hrp or decoded.data is None:
                raise ValueError(f"expected an {expected_hrp} address")
            if not decoded.data:
                raise ValueError("missing address version")
            version = decoded.data[0]
            if version == 31:
                raise ValueError("silent payment version 31 is not compatible")
            if version < 0 or version > 30:
                raise ValueError("invalid silent payment version")
            payload = segwit_addr.convertbits(decoded.data[1:], 5, 8, False)
            if payload is None:
                raise ValueError("invalid silent payment address payload")
            if version == 0 and len(payload) != 66:
                raise ValueError("silent payment v0 payload must be exactly 66 bytes")
            if version > 0 and len(payload) < 66:
                raise ValueError(
                    "forward-compatible silent payment payload must be at least 66 bytes"
                )
            scan_key = ECPubkey(bytes(payload[:33]))
            spend_key = ECPubkey(bytes(payload[33:66]))
        except SilentPaymentError:
            raise
        except Exception as exc:
            raise InvalidSilentPaymentAddress(
                f"Invalid BIP352 address: {exc}"
            ) from exc
        return cls(
            encoded=address.lower(),
            version=version,
            scan_key=scan_key,
            spend_key=spend_key,
        )


@dataclass(frozen=True)
class SilentPaymentRequest:
    recipient: SilentPaymentAddress
    amount_sat: int | None


def is_silent_payment_request_text(text: str) -> bool:
    """Return whether text should be handled by the Silent Payments plugin."""
    text = text.strip()
    lowered = text.lower()
    if lowered.startswith(("sp1", "tsp1")):
        return True
    if not lowered.startswith("bitcoin:"):
        return False
    try:
        parsed = urlsplit(text)
        return any(
            key == "sp"
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        )
    except Exception:
        return False


def parse_silent_payment_request(
    text: str, *, expected_hrp: str
) -> SilentPaymentRequest:
    """Parse a raw address or the BIP21 ``sp`` payment instruction."""
    text = text.strip()
    if not text.lower().startswith("bitcoin:"):
        return SilentPaymentRequest(
            recipient=SilentPaymentAddress.parse(text, expected_hrp=expected_hrp),
            amount_sat=None,
        )

    parsed = urlsplit(text)
    if parsed.scheme.lower() != "bitcoin":
        raise InvalidSilentPaymentAddress("Invalid BIP21 URI scheme.")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    required_unknown = [key for key, _ in pairs if key.startswith("req-")]
    if required_unknown:
        raise InvalidSilentPaymentAddress(
            f"Unsupported required BIP21 parameter: {required_unknown[0]}"
        )
    sp_values = [value for key, value in pairs if key == "sp"]
    if len(sp_values) != 1 or not sp_values[0]:
        raise InvalidSilentPaymentAddress(
            "BIP21 URI must contain exactly one non-empty sp parameter."
        )
    amount_values = [value for key, value in pairs if key == "amount"]
    if len(amount_values) > 1:
        raise InvalidSilentPaymentAddress(
            "BIP21 URI contains more than one amount parameter."
        )

    amount_sat = None
    if amount_values:
        try:
            amount_btc = Decimal(amount_values[0])
            amount_sats = amount_btc * Decimal(100_000_000)
            if (
                not amount_btc.is_finite()
                or amount_btc <= 0
                or amount_sats != amount_sats.to_integral_value()
            ):
                raise ValueError
            amount_sat = int(amount_sats)
        except (InvalidOperation, ValueError):
            raise InvalidSilentPaymentAddress(
                "Invalid BIP21 amount; use a positive value with at most 8 decimals."
            ) from None

    return SilentPaymentRequest(
        recipient=SilentPaymentAddress.parse(
            sp_values[0], expected_hrp=expected_hrp
        ),
        amount_sat=amount_sat,
    )


def expected_hrp(*, is_testnet: bool) -> str:
    return "tsp" if is_testnet else "sp"


def _checked_scalar(tag: bytes, message: bytes) -> int:
    scalar = int.from_bytes(bip340_tagged_hash(tag, message), "big")
    if scalar == 0 or scalar >= ecc.CURVE_ORDER:
        raise DerivationFailure(
            "BIP352 produced an invalid scalar. Select a different set of coins and retry."
        )
    return scalar


def derive_output_script(
    *,
    private_keys: Sequence[bytes],
    serialized_outpoints: Sequence[bytes],
    recipient: SilentPaymentAddress,
    output_index: int = 0,
) -> bytes:
    """Derive one BIP352 v0 P2TR scriptPubKey.

    `serialized_outpoints` must contain each txid in wire (little-endian)
    order followed by the four-byte little-endian vout.
    """
    if not private_keys:
        raise ValueError("private_keys must not be empty")
    if len(private_keys) != len(serialized_outpoints):
        raise ValueError("private_keys and serialized_outpoints must have equal length")
    if not serialized_outpoints:
        raise ValueError("serialized_outpoints must not be empty")
    if output_index < 0 or output_index > 0xFFFFFFFF:
        raise ValueError("output_index must fit in four bytes")
    if any(len(outpoint) != 36 for outpoint in serialized_outpoints):
        raise ValueError("each serialized outpoint must be exactly 36 bytes")

    scalars = []
    for secret in private_keys:
        if len(secret) != 32:
            raise ValueError("each private key must be exactly 32 bytes")
        scalar = int.from_bytes(secret, "big")
        if scalar == 0 or scalar >= ecc.CURVE_ORDER:
            raise ValueError("invalid secp256k1 private key")
        scalars.append(scalar)

    a_sum = sum(scalars) % ecc.CURVE_ORDER
    if a_sum == 0:
        raise DerivationFailure(
            "The selected input keys sum to zero. Select different coins and retry."
        )

    lowest_outpoint = min(serialized_outpoints)
    public_key_sum = a_sum * ecc.GENERATOR
    input_hash = _checked_scalar(
        b"BIP0352/Inputs",
        lowest_outpoint + public_key_sum.get_public_key_bytes(),
    )
    shared_secret = input_hash * a_sum * recipient.scan_key
    tweak = _checked_scalar(
        b"BIP0352/SharedSecret",
        shared_secret.get_public_key_bytes() + output_index.to_bytes(4, "big"),
    )
    try:
        output_key = recipient.spend_key + ECPrivkey(tweak.to_bytes(32, "big"))
        output_key_bytes = output_key.get_public_key_bytes()
    except Exception as exc:
        raise DerivationFailure(
            "BIP352 produced an invalid output key. "
            "Select a different set of coins and retry."
        ) from exc
    return b"\x51\x20" + output_key_bytes[1:]


def transaction_commitment(
    *,
    serialized_outpoints: Iterable[bytes],
    recipient_script: bytes,
    amount_sat: int,
) -> bytes:
    """Commit to the input set and silent output before asynchronous signing."""
    outpoints = sorted(serialized_outpoints)
    message = b"".join(outpoints) + amount_sat.to_bytes(8, "little") + recipient_script
    return bip340_tagged_hash(b"SilentPaymentsSender/Transaction", message)
