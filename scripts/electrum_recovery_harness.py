#!/usr/bin/env python3
"""Validate a recovery transaction with Electrum's BIP341 implementation."""

import argparse
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import electrum_ecc as ecc  # noqa: E402
from electrum import segwit_addr  # noqa: E402
from electrum.transaction import (  # noqa: E402
    PartialTransaction,
    PartialTxInput,
    PartialTxOutput,
    Sighash,
    Transaction,
    TxOutpoint,
)
from electrum.version import ELECTRUM_VERSION  # noqa: E402
from electrum_ecc.util import bip340_tagged_hash  # noqa: E402

from scripts.silent_payment_test_receiver import recover_transaction  # noqa: E402


SCAN_SECRET = 11
SPEND_SECRET = 29
MAINNET_ADDRESS = (
    "sp1qqdm54elctz55z8j77sjxkuxxt2k9vjvcp0juz7y3h0kp0z2a5qyvkqky"
    "f5fvwpjasyhg4neg6l9mr8usz8kdn60a72qmpe4rkh586gh8mv34hv4c"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result_path = (
        ROOT
        / f"test-results/electrum-{ELECTRUM_VERSION}-mainnet-cli-integration.json"
    )
    source_case = json.loads(
        result_path.read_text(encoding="utf-8")
    )["cases"][2]
    destination_program = bytes.fromhex("22" * 20)
    destination = segwit_addr.bech32_encode(
        segwit_addr.Encoding.BECH32,
        "bc",
        [0] + list(segwit_addr.convertbits(destination_program, 8, 5)),
    )

    with tempfile.TemporaryDirectory(prefix="silent-payment-recovery-") as temp:
        receiver_path = Path(temp) / "receiver.json"
        receiver_path.write_text(
            json.dumps({
                "format": "electrum-silent-payments-test-receiver-v1",
                "network": "mainnet",
                "silent_payment_address": MAINNET_ADDRESS,
                "scan_secret": f"{SCAN_SECRET:064x}",
                "spend_secret": f"{SPEND_SECRET:064x}",
            }),
            encoding="utf-8",
        )
        recovered = recover_transaction(
            receiver_path,
            source_case["raw_transaction"],
            destination=destination,
            fee_sat=500,
        )

    source_tx = Transaction(source_case["raw_transaction"])
    source_index = recovered["source_output_index"]
    txin = PartialTxInput(
        prevout=TxOutpoint.from_str(
            f"{source_tx.txid()}:{source_index}"
        ),
        nsequence=0xFFFFFFFD,
    )
    txin.utxo = source_tx
    txin._is_taproot = True
    txout = PartialTxOutput(
        scriptpubkey=bytes.fromhex("0014" + "22" * 20),
        value=recovered["output_amount_sat"],
    )
    recovery_tx = PartialTransaction.from_io(
        [txin],
        [txout],
        version=2,
        locktime=0,
    )
    preimage = recovery_tx.serialize_preimage(
        0,
        sighash=Sighash.DEFAULT,
    )
    message_hash = bip340_tagged_hash(b"TapSighash", preimage)

    parsed = Transaction(recovered["raw_transaction"])
    signature = parsed.inputs()[0].witness_elements()[0]
    source_script = source_tx.outputs()[source_index].scriptpubkey
    public_key = ecc.ECPubkey(b"\x02" + source_script[2:])
    if not public_key.schnorr_verify(signature, message_hash):
        raise SystemExit("Electrum rejected the recovery Schnorr signature")
    if parsed.txid() != recovered["txid"]:
        raise SystemExit("recovery txid mismatch")
    result = {
        "status": "ok",
        "electrum_version": ELECTRUM_VERSION,
        "source_txid": source_tx.txid(),
        "recovery_txid": recovered["txid"],
        "fee_sat": recovered["fee_sat"],
        "signature_verified_by_electrum": True,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print("RECOVERY_INTEGRATION_OK")


if __name__ == "__main__":
    main()
