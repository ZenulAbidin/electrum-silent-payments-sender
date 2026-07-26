#!/usr/bin/env python3
"""Create, scan, and recover a disposable BIP352 receiver.

The generated JSON file contains private scan and spend keys and must be treated
like a wallet backup. Mainnet generation requires an explicit risk flag.
"""

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import electrum_ecc as ecc
    from electrum import segwit_addr
    from electrum_ecc.util import bip340_tagged_hash
except ImportError as exc:
    try:
        from tests.electrum_test_shim import install

        install()
        import electrum_ecc as ecc
        from electrum import segwit_addr
        from electrum_ecc.util import bip340_tagged_hash
    except ImportError:
        raise SystemExit(
            "Run this utility from the unpacked source archive with Python 3, "
            "or in an Electrum 4.6.0+ Python environment."
        ) from exc


RECEIVER_FORMAT = "electrum-silent-payments-test-receiver-v1"


@dataclass
class ParsedInput:
    prevout: bytes
    script_sig: bytes
    witness: list[bytes]


@dataclass
class ParsedOutput:
    value: int
    scriptpubkey: bytes


@dataclass
class ParsedTransaction:
    inputs: list[ParsedInput]
    outputs: list[ParsedOutput]
    txid: str


def _read_varint(raw: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(raw):
        raise ValueError("truncated compact integer")
    prefix = raw[offset]
    offset += 1
    if prefix < 0xFD:
        return prefix, offset
    sizes = {0xFD: 2, 0xFE: 4, 0xFF: 8}
    size = sizes[prefix]
    end = offset + size
    if end > len(raw):
        raise ValueError("truncated compact integer")
    return int.from_bytes(raw[offset:end], "little"), end


def _encode_varint(value: int) -> bytes:
    if value < 0xFD:
        return bytes([value])
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    if value <= 0xFFFFFFFF:
        return b"\xfe" + value.to_bytes(4, "little")
    return b"\xff" + value.to_bytes(8, "little")


def _read_bytes(raw: bytes, offset: int, size: int) -> tuple[bytes, int]:
    end = offset + size
    if end > len(raw):
        raise ValueError("truncated transaction")
    return raw[offset:end], end


def _read_varbytes(raw: bytes, offset: int) -> tuple[bytes, int]:
    size, offset = _read_varint(raw, offset)
    return _read_bytes(raw, offset, size)


def _parse_transaction(raw_hex: str) -> ParsedTransaction:
    raw = bytes.fromhex(raw_hex)
    if len(raw) < 10:
        raise ValueError("truncated transaction")
    version = raw[:4]
    offset = 4
    is_segwit = raw[offset:offset + 2] == b"\x00\x01"
    if is_segwit:
        offset += 2

    input_count, offset = _read_varint(raw, offset)
    inputs = []
    stripped_inputs = bytearray()
    for _ in range(input_count):
        prevout, offset = _read_bytes(raw, offset, 36)
        script_sig, offset = _read_varbytes(raw, offset)
        sequence, offset = _read_bytes(raw, offset, 4)
        inputs.append(ParsedInput(prevout, script_sig, []))
        stripped_inputs += (
            prevout + _encode_varint(len(script_sig)) + script_sig + sequence
        )

    output_count, offset = _read_varint(raw, offset)
    outputs = []
    stripped_outputs = bytearray()
    for _ in range(output_count):
        value_bytes, offset = _read_bytes(raw, offset, 8)
        scriptpubkey, offset = _read_varbytes(raw, offset)
        outputs.append(
            ParsedOutput(int.from_bytes(value_bytes, "little"), scriptpubkey)
        )
        stripped_outputs += (
            value_bytes
            + _encode_varint(len(scriptpubkey))
            + scriptpubkey
        )

    if is_segwit:
        for txin in inputs:
            item_count, offset = _read_varint(raw, offset)
            for _ in range(item_count):
                item, offset = _read_varbytes(raw, offset)
                txin.witness.append(item)

    locktime, offset = _read_bytes(raw, offset, 4)
    if offset != len(raw):
        raise ValueError("transaction contains trailing data")
    stripped = (
        version
        + _encode_varint(input_count)
        + stripped_inputs
        + _encode_varint(output_count)
        + stripped_outputs
        + locktime
    )
    txid = sha256(sha256(stripped).digest()).digest()[::-1].hex()
    return ParsedTransaction(inputs, outputs, txid)


def _new_secret() -> int:
    while True:
        value = int.from_bytes(secrets.token_bytes(32), "big")
        if 0 < value < ecc.CURVE_ORDER:
            return value


def _public_key(secret: int):
    return secret * ecc.GENERATOR


def _address(scan_secret: int, spend_secret: int, network: str) -> str:
    payload = (
        _public_key(scan_secret).get_public_key_bytes()
        + _public_key(spend_secret).get_public_key_bytes()
    )
    data = [0] + list(segwit_addr.convertbits(payload, 8, 5))
    return segwit_addr.bech32_encode(
        segwit_addr.Encoding.BECH32M,
        "sp" if network == "mainnet" else "tsp",
        data,
    )


def generate_receiver(output: Path, *, network: str = "testnet") -> dict:
    if network not in ("testnet", "mainnet"):
        raise ValueError("network must be testnet or mainnet")
    scan_secret = _new_secret()
    spend_secret = _new_secret()
    receiver = {
        "format": RECEIVER_FORMAT,
        "network": network,
        "silent_payment_address": _address(
            scan_secret,
            spend_secret,
            network,
        ),
        "scan_secret": f"{scan_secret:064x}",
        "spend_secret": f"{spend_secret:064x}",
        "warning": (
            "Private Silent Payments receiver keys. Anyone with this file can "
            "derive and spend every payment sent to its address."
        ),
    }
    if output.exists():
        raise ValueError(
            f"refusing to overwrite existing receiver file: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(receiver, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    return receiver


def load_receiver(path: Path) -> tuple[int, int, str, str]:
    receiver = json.loads(path.read_text(encoding="utf-8"))
    if receiver.get("format") != RECEIVER_FORMAT:
        raise ValueError("unrecognized receiver file format")
    network = receiver.get("network")
    if network not in ("testnet", "mainnet"):
        raise ValueError("receiver has an invalid network")
    scan_secret = int(receiver["scan_secret"], 16)
    spend_secret = int(receiver["spend_secret"], 16)
    if not (
        0 < scan_secret < ecc.CURVE_ORDER
        and 0 < spend_secret < ecc.CURVE_ORDER
    ):
        raise ValueError("receiver contains an invalid private key")
    address = _address(scan_secret, spend_secret, network)
    if address != receiver.get("silent_payment_address"):
        raise ValueError("receiver address does not match its private keys")
    return scan_secret, spend_secret, address, network


def _input_public_key(txin):
    if txin.witness:
        if len(txin.witness) != 2:
            raise ValueError("unsupported transaction input witness")
        public_key = txin.witness[1]
    else:
        items = []
        offset = 0
        while offset < len(txin.script_sig):
            size = txin.script_sig[offset]
            offset += 1
            if size > 75 or offset + size > len(txin.script_sig):
                raise ValueError("unsupported transaction input script")
            items.append(txin.script_sig[offset:offset + size])
            offset += size
        if len(items) != 2:
            raise ValueError("unsupported transaction input script")
        public_key = items[1]
    if len(public_key) != 33:
        raise ValueError("transaction input does not reveal a compressed key")
    return ecc.ECPubkey(public_key)


def _aggregate_input_key(tx):
    if not tx.inputs:
        raise ValueError("transaction has no inputs")
    keys = [_input_public_key(txin) for txin in tx.inputs]
    aggregate = keys[0]
    for key in keys[1:]:
        aggregate += key
    return aggregate


def scan_transaction(
    receiver_path: Path,
    raw_transaction: str,
    *,
    include_private_keys: bool = False,
) -> dict:
    scan_secret, spend_secret, address, network = load_receiver(receiver_path)
    raw_transaction = "".join(raw_transaction.split())
    try:
        tx = _parse_transaction(raw_transaction)
        aggregate = _aggregate_input_key(tx)
    except Exception as exc:
        raise ValueError(
            "could not parse a complete signed transaction with supported inputs"
        ) from exc

    outpoints = [txin.prevout for txin in tx.inputs]
    input_hash = int.from_bytes(
        bip340_tagged_hash(
            b"BIP0352/Inputs",
            min(outpoints) + aggregate.get_public_key_bytes(),
        ),
        "big",
    )
    if not 0 < input_hash < ecc.CURVE_ORDER:
        raise ValueError("BIP352 input hash is an invalid scalar")

    shared_secret = input_hash * scan_secret * aggregate
    outputs = tx.outputs
    matches = []
    # This sender emits one silent output, whose BIP352 output counter is zero.
    # Trying the remaining counters also makes the diagnostic useful for a
    # transaction containing multiple independently derived silent outputs.
    for counter in range(max(1, len(outputs))):
        tweak = int.from_bytes(
            bip340_tagged_hash(
                b"BIP0352/SharedSecret",
                shared_secret.get_public_key_bytes()
                + counter.to_bytes(4, "big"),
            ),
            "big",
        )
        if not 0 < tweak < ecc.CURVE_ORDER:
            continue
        output_secret = (spend_secret + tweak) % ecc.CURVE_ORDER
        if not output_secret:
            continue
        output_key = _public_key(output_secret)
        scriptpubkey = b"\x51\x20" + output_key.get_public_key_bytes()[1:]
        for tx_output_index, txout in enumerate(outputs):
            if txout.scriptpubkey != scriptpubkey:
                continue
            # BIP340 uses the secret corresponding to the even-Y representative
            # of the x-only output key.
            output_key_bytes = output_key.get_public_key_bytes()
            signing_secret = (
                output_secret
                if output_key_bytes[0] == 2
                else ecc.CURVE_ORDER - output_secret
            )
            match = {
                "amount_sat": txout.value,
                "bip352_output_counter": counter,
                "output_index": tx_output_index,
                "scriptpubkey": scriptpubkey.hex(),
            }
            if include_private_keys:
                match["taproot_private_key"] = f"{signing_secret:064x}"
            matches.append(match)

    return {
        "status": "match" if matches else "no_match",
        "network": network,
        "silent_payment_address": address,
        "txid": tx.txid,
        "matches": matches,
    }


def _decode_segwit_destination(address: str, network: str) -> bytes:
    decoded = segwit_addr.bech32_decode(address)
    if hasattr(decoded, "encoding"):
        encoding, hrp, data = decoded.encoding, decoded.hrp, decoded.data
    else:
        encoding, hrp, data = decoded
    expected_hrp = "bc" if network == "mainnet" else "tb"
    if hrp != expected_hrp or not data:
        raise ValueError(
            f"destination must be a {expected_hrp}1... SegWit address"
        )
    witness_version = data[0]
    program = segwit_addr.convertbits(data[1:], 5, 8, False)
    if (
        program is None
        or witness_version > 16
        or not 2 <= len(program) <= 40
        or (witness_version == 0 and len(program) not in (20, 32))
    ):
        raise ValueError("destination has an invalid witness program")
    expected_encoding = (
        segwit_addr.Encoding.BECH32
        if witness_version == 0
        else segwit_addr.Encoding.BECH32M
    )
    if encoding != expected_encoding:
        raise ValueError("destination uses the wrong Bech32 checksum")
    version_opcode = (
        b"\x00"
        if witness_version == 0
        else bytes([0x50 + witness_version])
    )
    return version_opcode + bytes([len(program)]) + bytes(program)


def _schnorr_sign(message: bytes, secret: int) -> bytes:
    if len(message) != 32 or not 0 < secret < ecc.CURVE_ORDER:
        raise ValueError("invalid Schnorr signing input")
    public_key = _public_key(secret)
    public_bytes = public_key.get_public_key_bytes()
    normalized_secret = (
        secret if public_bytes[0] == 2 else ecc.CURVE_ORDER - secret
    )
    public_x = public_bytes[1:]
    auxiliary = secrets.token_bytes(32)
    auxiliary_hash = bip340_tagged_hash(b"BIP0340/aux", auxiliary)
    masked_secret = bytes(
        left ^ right
        for left, right in zip(
            normalized_secret.to_bytes(32, "big"),
            auxiliary_hash,
        )
    )
    nonce = int.from_bytes(
        bip340_tagged_hash(
            b"BIP0340/nonce",
            masked_secret + public_x + message,
        ),
        "big",
    ) % ecc.CURVE_ORDER
    if not nonce:
        raise ValueError("BIP340 nonce is zero")
    nonce_point = _public_key(nonce)
    nonce_bytes = nonce_point.get_public_key_bytes()
    if nonce_bytes[0] == 3:
        nonce = ecc.CURVE_ORDER - nonce
        nonce_point = _public_key(nonce)
        nonce_bytes = nonce_point.get_public_key_bytes()
    challenge = int.from_bytes(
        bip340_tagged_hash(
            b"BIP0340/challenge",
            nonce_bytes[1:] + public_x + message,
        ),
        "big",
    ) % ecc.CURVE_ORDER
    signature = (
        nonce_bytes[1:]
        + ((nonce + challenge * normalized_secret) % ecc.CURVE_ORDER).to_bytes(
            32,
            "big",
        )
    )
    if not _schnorr_verify(message, public_x, signature):
        raise ValueError("internal Schnorr signature verification failed")
    return signature


def _schnorr_verify(message: bytes, public_x: bytes, signature: bytes) -> bool:
    if len(message) != 32 or len(public_x) != 32 or len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if r >= 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F:
        return False
    if s >= ecc.CURVE_ORDER:
        return False
    try:
        public_key = ecc.ECPubkey(b"\x02" + public_x)
        challenge = int.from_bytes(
            bip340_tagged_hash(
                b"BIP0340/challenge",
                signature[:32] + public_x + message,
            ),
            "big",
        ) % ecc.CURVE_ORDER
        nonce_point = (
            s * ecc.GENERATOR
            + (ecc.CURVE_ORDER - challenge) * public_key
        )
        nonce_bytes = nonce_point.get_public_key_bytes()
    except Exception:
        return False
    return nonce_bytes[0] == 2 and nonce_bytes[1:] == signature[:32]


def recover_transaction(
    receiver_path: Path,
    receiving_transaction: str,
    *,
    destination: str,
    fee_sat: int,
) -> dict:
    scanned = scan_transaction(
        receiver_path,
        receiving_transaction,
        include_private_keys=True,
    )
    if scanned["status"] != "match":
        raise ValueError("receiver does not own an output in this transaction")
    if len(scanned["matches"]) != 1:
        raise ValueError("recovery requires exactly one matched output")
    match = scanned["matches"][0]
    if not isinstance(fee_sat, int) or fee_sat <= 0:
        raise ValueError("fee must be a positive integer number of satoshis")
    output_value = match["amount_sat"] - fee_sat
    if output_value < 330:
        raise ValueError("recovery output would be below the dust limit")

    destination_script = _decode_segwit_destination(
        destination,
        scanned["network"],
    )
    prevout = (
        bytes.fromhex(scanned["txid"])[::-1]
        + match["output_index"].to_bytes(4, "little")
    )
    sequence = bytes.fromhex("fdffffff")
    output = (
        output_value.to_bytes(8, "little")
        + _encode_varint(len(destination_script))
        + destination_script
    )
    source_script = bytes.fromhex(match["scriptpubkey"])
    sig_message = (
        b"\x00"  # epoch
        + b"\x00"  # SIGHASH_DEFAULT
        + (2).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + sha256(prevout).digest()
        + sha256(match["amount_sat"].to_bytes(8, "little")).digest()
        + sha256(
            _encode_varint(len(source_script)) + source_script
        ).digest()
        + sha256(sequence).digest()
        + sha256(output).digest()
        + b"\x00"  # key-path spend, no annex
        + (0).to_bytes(4, "little")
    )
    sighash = bip340_tagged_hash(b"TapSighash", sig_message)
    signing_secret = int(match["taproot_private_key"], 16)
    signature = _schnorr_sign(sighash, signing_secret)

    stripped = (
        (2).to_bytes(4, "little")
        + b"\x01"
        + prevout
        + b"\x00"
        + sequence
        + b"\x01"
        + output
        + (0).to_bytes(4, "little")
    )
    signed = (
        (2).to_bytes(4, "little")
        + b"\x00\x01"
        + b"\x01"
        + prevout
        + b"\x00"
        + sequence
        + b"\x01"
        + output
        + b"\x01\x40"
        + signature
        + (0).to_bytes(4, "little")
    )
    weight = len(stripped) * 4 + (len(signed) - len(stripped))
    return {
        "status": "signed",
        "network": scanned["network"],
        "source_txid": scanned["txid"],
        "source_output_index": match["output_index"],
        "input_amount_sat": match["amount_sat"],
        "fee_sat": fee_sat,
        "output_amount_sat": output_value,
        "destination": destination,
        "estimated_vbytes": (weight + 3) // 4,
        "txid": sha256(sha256(stripped).digest()).digest()[::-1].hex(),
        "raw_transaction": signed.hex(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and scan a disposable BIP352 testnet receiver",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate",
        help="create a fresh tsp1 receiver and private test key file",
    )
    generate_parser.add_argument(
        "--output",
        type=Path,
        default=Path("silent-payment-test-receiver.json"),
    )
    generate_parser.add_argument(
        "--network",
        choices=("testnet", "mainnet"),
        default="testnet",
    )
    generate_parser.add_argument(
        "--i-understand-mainnet-risk",
        action="store_true",
        help="required to generate a mainnet sp1 receiver",
    )

    scan_parser = subparsers.add_parser(
        "scan",
        help="check a complete signed raw transaction for the receiver output",
    )
    scan_parser.add_argument("--receiver", type=Path, required=True)
    transaction_source = scan_parser.add_mutually_exclusive_group(required=True)
    transaction_source.add_argument("--raw-transaction")
    transaction_source.add_argument("--raw-transaction-file", type=Path)

    verify_parser = subparsers.add_parser(
        "verify-backup",
        help="validate a receiver backup and reproduce its payment address",
    )
    verify_parser.add_argument("--receiver", type=Path, required=True)

    recover_parser = subparsers.add_parser(
        "recover",
        help="sign a sweep of the matched output to a normal SegWit address",
    )
    recover_parser.add_argument("--receiver", type=Path, required=True)
    recovery_source = recover_parser.add_mutually_exclusive_group(required=True)
    recovery_source.add_argument("--receiving-transaction")
    recovery_source.add_argument("--receiving-transaction-file", type=Path)
    recover_parser.add_argument("--destination", required=True)
    recover_parser.add_argument("--fee-sat", required=True, type=int)
    recover_parser.add_argument(
        "--output",
        type=Path,
        help="optionally save the signed recovery transaction hex",
    )

    args = parser.parse_args()
    try:
        if args.command == "generate":
            if (
                args.network == "mainnet"
                and not args.i_understand_mainnet_risk
            ):
                raise ValueError(
                    "mainnet generation requires "
                    "--i-understand-mainnet-risk"
                )
            result = generate_receiver(args.output, network=args.network)
            result = {
                "status": "created",
                "receiver_file": str(args.output),
                "network": result["network"],
                "silent_payment_address": result["silent_payment_address"],
            }
        elif args.command == "scan":
            raw_transaction = args.raw_transaction
            if args.raw_transaction_file:
                raw_transaction = args.raw_transaction_file.read_text(
                    encoding="ascii",
                )
            result = scan_transaction(args.receiver, raw_transaction)
        elif args.command == "verify-backup":
            _, _, address, network = load_receiver(args.receiver)
            result = {
                "status": "valid",
                "network": network,
                "silent_payment_address": address,
                "receiver_file": str(args.receiver),
            }
        else:
            receiving_transaction = args.receiving_transaction
            if args.receiving_transaction_file:
                receiving_transaction = args.receiving_transaction_file.read_text(
                    encoding="ascii",
                )
            result = recover_transaction(
                args.receiver,
                receiving_transaction,
                destination=args.destination,
                fee_sat=args.fee_sat,
            )
            if args.output:
                args.output.write_text(
                    result["raw_transaction"] + "\n",
                    encoding="ascii",
                )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "scan" and result["status"] != "match":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
