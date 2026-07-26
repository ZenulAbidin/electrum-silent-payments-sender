#!/usr/bin/env python3
"""Headless end-to-end harness against a supported Electrum runtime.

This intentionally imports the real Electrum wallet, transaction, keystore,
Bech32m, and electrum_ecc implementations. It does not use the unit-test shim.
No network connection is made and no real seed or funds are used.
"""

from hashlib import sha256
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import electrum_ecc as ecc  # noqa: E402
from electrum import bitcoin, constants, keystore, segwit_addr, util  # noqa: E402
from electrum.address_synchronizer import TX_HEIGHT_UNCONFIRMED  # noqa: E402
from electrum.fee_policy import FixedFeePolicy  # noqa: E402
from electrum.simple_config import SimpleConfig  # noqa: E402
from electrum.transaction import (  # noqa: E402
    PartialTxOutput,
    Transaction,
    script_GetOp,
)
from electrum.version import ELECTRUM_VERSION  # noqa: E402
from electrum.wallet import Standard_Wallet  # noqa: E402
from electrum.wallet_db import WalletDB  # noqa: E402
from electrum_ecc.util import bip340_tagged_hash  # noqa: E402

from silent_payments_sender.core import (  # noqa: E402
    PLACEHOLDER_SCRIPT,
    SilentPaymentAddress,
)
from silent_payments_sender.txflow import (  # noqa: E402
    finalize_transaction,
    seal_after_confirmation,
    verify_transaction,
)


TESTED_ELECTRUM_VERSIONS = ("4.6.0", "4.7.2", "4.8.0")
TEST_SEED = "bitter grass shiver impose acquire brush forget axis eager alone wine silver"
LEGACY_TEST_SEED = "cycle rocket west magnet parrot shuffle foot correct salt library feed song"
NESTED_SEGWIT_ROOT_SEED = bytes.fromhex("42" * 32)
INPUT_TYPES = ("p2pkh", "p2wpkh-p2sh", "p2wpkh")
FUNDING_VALUE = 1_000_000
SEND_VALUE = 100_000
FIXED_FEE = 1_000
SCAN_SECRET = 11
SPEND_SECRET = 29


def make_wallet(config, input_type):
    if input_type == "p2pkh":
        wallet_keystore = keystore.from_seed(
            LEGACY_TEST_SEED,
            passphrase="",
            for_multisig=False,
        )
    elif input_type == "p2wpkh-p2sh":
        coin_type = 1 if constants.net.TESTNET else 0
        wallet_keystore = keystore.from_bip43_rootseed(
            NESTED_SEGWIT_ROOT_SEED,
            derivation=f"m/49'/{coin_type}'/0'",
            xtype="p2wpkh-p2sh",
        )
    elif input_type == "p2wpkh":
        wallet_keystore = keystore.from_seed(
            TEST_SEED,
            passphrase="",
            for_multisig=False,
        )
    else:
        raise ValueError(f"unknown input type: {input_type}")
    db = WalletDB("", storage=None, upgrade=True)
    db.put("keystore", wallet_keystore.dump())
    db.put("gap_limit", 2)
    db.put("gap_limit_for_change", 1)
    wallet = Standard_Wallet(db, config=config)
    wallet.synchronize()
    assert wallet.get_txin_type() == input_type
    return wallet


def make_synthetic_funding_tx(address):
    """Create an offline-only transaction paying the test wallet."""
    scriptpubkey = bitcoin.address_to_script(address)
    raw = (
        (2).to_bytes(4, "little")
        + b"\x01"
        + bytes.fromhex("11" * 32)
        + (0).to_bytes(4, "little")
        + b"\x00"
        + (0xFFFFFFFF).to_bytes(4, "little")
        + b"\x01"
        + FUNDING_VALUE.to_bytes(8, "little")
        + bitcoin.var_int(len(scriptpubkey))
        + scriptpubkey
        + (0).to_bytes(4, "little")
    )
    return Transaction(raw.hex())


def make_silent_payment_address(*, hrp):
    scan_key = SCAN_SECRET * ecc.GENERATOR
    spend_key = SPEND_SECRET * ecc.GENERATOR
    payload = scan_key.get_public_key_bytes() + spend_key.get_public_key_bytes()
    data = [0] + list(segwit_addr.convertbits(payload, 8, 5))
    return segwit_addr.bech32_encode(
        segwit_addr.Encoding.BECH32M,
        hrp,
        data,
    )


def receiver_output_key(wallet, tx):
    """Independently derive the receiver's spendable output key."""
    input_secrets = []
    serialized_outpoints = []
    for txin in tx.inputs():
        wallet.add_input_info(txin)
        address_index = wallet.get_address_index(txin.address)
        secret, compressed = wallet.get_keystore().get_private_key(
            address_index,
            None,
        )
        assert compressed
        input_secrets.append(int.from_bytes(secret, "big"))
        serialized_outpoints.append(txin.prevout.serialize_to_network())

    input_secret_sum = sum(input_secrets) % ecc.CURVE_ORDER
    input_public_key_sum = input_secret_sum * ecc.GENERATOR
    input_hash = int.from_bytes(
        bip340_tagged_hash(
            b"BIP0352/Inputs",
            min(serialized_outpoints)
            + input_public_key_sum.get_public_key_bytes(),
        ),
        "big",
    )
    shared_secret = input_hash * SCAN_SECRET * input_public_key_sum
    tweak = int.from_bytes(
        bip340_tagged_hash(
            b"BIP0352/SharedSecret",
            shared_secret.get_public_key_bytes() + (0).to_bytes(4, "big"),
        ),
        "big",
    )
    output_secret = (SPEND_SECRET + tweak) % ecc.CURVE_ORDER
    assert output_secret
    return output_secret * ecc.GENERATOR


def verify_input_signatures(tx):
    for index, txin in enumerate(tx.inputs()):
        witness = txin.witness_elements()
        if witness:
            if len(witness) != 2:
                return False
            signature, pubkey = witness
        else:
            script_items = [item[1] for item in script_GetOp(txin.script_sig)]
            if len(script_items) != 2 or any(item is None for item in script_items):
                return False
            signature, pubkey = script_items
        if not signature or signature[-1] != 0x01:  # SIGHASH_ALL
            return False
        if not tx.verify_sig_for_txin(
            txin_index=index,
            pubkey_bytes=pubkey,
            sig=signature,
        ):
            return False
    return True


def run_case(config, input_type):
    wallet = make_wallet(config, input_type)
    funding_address = wallet.get_receiving_addresses()[0]
    funding_tx = make_synthetic_funding_tx(funding_address)
    wallet.adb.receive_tx_callback(
        funding_tx,
        tx_height=TX_HEIGHT_UNCONFIRMED,
    )
    coins = wallet.get_spendable_coins(
        nonlocal_only=False,
        confirmed_only=False,
    )
    assert len(coins) == 1
    assert coins[0].value_sats() == FUNDING_VALUE

    network_name = "testnet" if constants.net.TESTNET else "mainnet"
    hrp = "tsp" if constants.net.TESTNET else "sp"
    silent_address = make_silent_payment_address(hrp=hrp)
    recipient = SilentPaymentAddress.parse(
        silent_address,
        expected_hrp=hrp,
    )
    tx = wallet.make_unsigned_transaction(
        fee_policy=FixedFeePolicy(FIXED_FEE),
        coins=coins,
        outputs=[
            PartialTxOutput(
                scriptpubkey=PLACEHOLDER_SCRIPT,
                value=SEND_VALUE,
            )
        ],
        rbf=False,
        send_change_to_lightning=False,
        merge_duplicate_outputs=False,
    )
    finalized = finalize_transaction(
        wallet=wallet,
        tx=tx,
        password=None,
        recipient=recipient,
    )

    # Reproduce the mutations Electrum's fee dialog applies after make_tx.
    tx.set_rbf(True)
    tx.locktime = 123
    assert not verify_transaction(tx, finalized)
    finalized = seal_after_confirmation(tx, finalized)
    assert verify_transaction(tx, finalized)
    assert all(txin.nsequence == 0xFFFFFFFE for txin in tx.inputs())

    receiver_key = receiver_output_key(wallet, tx)
    assert finalized.scriptpubkey == (
        b"\x51\x20" + receiver_key.get_public_key_bytes()[1:]
    )

    unsigned_digest = sha256(
        bytes.fromhex(tx.serialize_to_network(include_sigs=False))
    ).hexdigest()
    wallet.sign_transaction(tx, password=None, ignore_warnings=True)
    assert tx.is_complete()
    assert verify_transaction(tx, finalized)
    signature_verified = verify_input_signatures(tx)
    assert signature_verified

    raw_transaction = tx.serialize()
    parsed = Transaction(raw_transaction)
    assert parsed.txid() == tx.txid()
    assert parsed.outputs()[finalized.output_index].scriptpubkey == (
        finalized.scriptpubkey
    )
    assert parsed.outputs()[finalized.output_index].value == SEND_VALUE
    assert tx.get_fee() == FIXED_FEE
    assert all(txin.nsequence == 0xFFFFFFFE for txin in parsed.inputs())

    return {
        "status": "ok",
        "network": network_name,
        "wallet_type": wallet.wallet_type,
        "input_type": wallet.get_txin_type(),
        "inputs": len(tx.inputs()),
        "outputs": len(tx.outputs()),
        "amount_sat": SEND_VALUE,
        "fee_sat": tx.get_fee(),
        "rbf": False,
        "silent_address": silent_address,
        "recipient_scriptpubkey": finalized.scriptpubkey.hex(),
        "receiver_can_derive_output_key": True,
        "input_signatures_verified": signature_verified,
        "sighash_all": True,
        "transaction_complete": tx.is_complete(),
        "unsigned_transaction_sha256": unsigned_digest,
        "signed_txid": tx.txid(),
        "raw_transaction": raw_transaction,
    }


async def async_main(*, network_name):
    if ELECTRUM_VERSION not in TESTED_ELECTRUM_VERSIONS:
        raise SystemExit(
            f"expected one of Electrum {TESTED_ELECTRUM_VERSIONS}, "
            f"found {ELECTRUM_VERSION}"
        )
    if network_name == "testnet":
        constants.BitcoinTestnet.set_as_network()
    elif network_name == "mainnet":
        constants.BitcoinMainnet.set_as_network()
    else:
        raise ValueError(f"unsupported network: {network_name}")
    util._asyncio_event_loop = asyncio.get_running_loop()

    try:
        with tempfile.TemporaryDirectory(prefix="silent-payment-cli-") as directory:
            config = SimpleConfig({
                "electrum_path": directory,
                "wallet_spend_confirmed_only": False,
            })
            result = {
                "status": "ok",
                "electrum_version": ELECTRUM_VERSION,
                "network": network_name,
                "cases": [
                    run_case(config, input_type)
                    for input_type in INPUT_TYPES
                ],
            }
            return result
    finally:
        util._asyncio_event_loop = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        help="optionally save the successful result as JSON",
    )
    parser.add_argument(
        "--network",
        choices=("testnet", "mainnet"),
        default="testnet",
        help="Bitcoin network whose address encoding and wallet rules to test",
    )
    args = parser.parse_args()
    result = asyncio.run(async_main(network_name=args.network))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print("CLI_INTEGRATION_OK")


if __name__ == "__main__":
    main()
