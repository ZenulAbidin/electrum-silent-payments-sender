"""Minimal Electrum/ECC API shim used only by the self-contained tests."""

from enum import Enum
from hashlib import sha256
from types import ModuleType
import sys


P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G_POINT = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHARSET_MAP = {char: index for index, char in enumerate(CHARSET)}


def inverse(value):
    return pow(value, P - 2, P)


def point_add(left, right):
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2 and (y1 != y2 or y1 == 0):
        return None
    if left == right:
        slope = (3 * x1 * x1) * inverse(2 * y1) % P
    else:
        slope = (y2 - y1) * inverse(x2 - x1) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def point_mul(scalar, point):
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        scalar >>= 1
    return result


class ECPubkey:
    def __init__(self, value):
        if isinstance(value, tuple):
            point = value
        else:
            value = bytes(value)
            if len(value) != 33 or value[0] not in (2, 3):
                raise ValueError("invalid compressed public key")
            x = int.from_bytes(value[1:], "big")
            if x >= P:
                raise ValueError("invalid public key x coordinate")
            alpha = (pow(x, 3, P) + 7) % P
            y = pow(alpha, (P + 1) // 4, P)
            if pow(y, 2, P) != alpha:
                raise ValueError("point is not on curve")
            if y & 1 != value[0] & 1:
                y = P - y
            point = x, y
        if point is None:
            raise ValueError("point at infinity")
        self.point = point

    def __rmul__(self, scalar):
        point = point_mul(scalar % N, self.point)
        if point is None:
            raise ValueError("point at infinity")
        return ECPubkey(point)

    def __add__(self, other):
        point = point_add(self.point, other.point)
        if point is None:
            raise ValueError("point at infinity")
        return ECPubkey(point)

    def get_public_key_bytes(self):
        x, y = self.point
        return bytes([2 | (y & 1)]) + x.to_bytes(32, "big")

    def get_public_key_hex(self):
        return self.get_public_key_bytes().hex()


class ECPrivkey(ECPubkey):
    def __init__(self, secret):
        scalar = int.from_bytes(secret, "big")
        if scalar == 0 or scalar >= N:
            raise ValueError("invalid private key")
        super().__init__(point_mul(scalar, G_POINT))
        self.secret = bytes(secret)

    def get_secret_bytes(self):
        return self.secret


def tagged_hash(tag, message):
    tag_hash = sha256(tag).digest()
    return sha256(tag_hash + tag_hash + message).digest()


class Encoding(Enum):
    BECH32 = 1
    BECH32M = 2


def _polymod(values):
    generators = (
        0x3B6A57B2, 0x26508E6D, 0x1EA119FA,
        0x3D4233DD, 0x2A1462B3,
    )
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for index, generator in enumerate(generators):
            if top >> index & 1:
                checksum ^= generator
    return checksum


def _hrp_expand(hrp):
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def bech32_encode(encoding, hrp, data):
    constant = 1 if encoding == Encoding.BECH32 else 0x2BC830A3
    polymod = _polymod(_hrp_expand(hrp) + list(data) + [0] * 6) ^ constant
    checksum = [(polymod >> (5 * (5 - index))) & 31 for index in range(6)]
    return hrp + "1" + "".join(CHARSET[value] for value in list(data) + checksum)


class Decoded:
    def __init__(self, encoding=None, hrp=None, data=None):
        self.encoding = encoding
        self.hrp = hrp
        self.data = data


def bech32_decode(value, *, ignore_long_length=False, with_checksum=True):
    if value.lower() != value and value.upper() != value:
        return Decoded()
    value = value.lower()
    separator = value.rfind("1")
    if separator < 1 or separator + 7 > len(value):
        return Decoded()
    if not ignore_long_length and len(value) > 90:
        return Decoded()
    try:
        data = [CHARSET_MAP[char] for char in value[separator + 1:]]
    except KeyError:
        return Decoded()
    check = _polymod(_hrp_expand(value[:separator]) + data)
    if check == 1:
        encoding = Encoding.BECH32
    elif check == 0x2BC830A3:
        encoding = Encoding.BECH32M
    else:
        return Decoded()
    return Decoded(encoding, value[:separator], data[:-6])


def convertbits(data, frombits, tobits, pad=True):
    accumulator = 0
    bits = 0
    result = []
    max_value = (1 << tobits) - 1
    max_accumulator = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or value >> frombits:
            return None
        accumulator = ((accumulator << frombits) | value) & max_accumulator
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            result.append((accumulator >> bits) & max_value)
    if pad:
        if bits:
            result.append((accumulator << (tobits - bits)) & max_value)
    elif bits >= frombits or ((accumulator << (tobits - bits)) & max_value):
        return None
    return result


def install():
    ecc = ModuleType("electrum_ecc")
    ecc.CURVE_ORDER = N
    ecc.GENERATOR = ECPubkey(G_POINT)
    ecc.ECPubkey = ECPubkey
    ecc.ECPrivkey = ECPrivkey
    ecc_util = ModuleType("electrum_ecc.util")
    ecc_util.bip340_tagged_hash = tagged_hash

    electrum = ModuleType("electrum")
    electrum.__path__ = []
    segwit = ModuleType("electrum.segwit_addr")
    segwit.Encoding = Encoding
    segwit.bech32_decode = bech32_decode
    segwit.bech32_encode = bech32_encode
    segwit.convertbits = convertbits
    electrum.segwit_addr = segwit

    sys.modules.update({
        "electrum_ecc": ecc,
        "electrum_ecc.util": ecc_util,
        "electrum": electrum,
        "electrum.segwit_addr": segwit,
    })
