"""Spending received silent payments, to an ordinary or a silent payment address.

Each output's key is d·G with d = (b_spend + tweak) mod n, where spscand's
tweak already includes any label tweak. It is a plain taproot key with no
BIP341 tweak, so Electrum's signer (which always applies one) cannot sign it:
the plugin computes Electrum's BIP341 sighash and signs the key path itself.
Before signing, every key is checked against the output it claims to spend.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .core import (
    PLACEHOLDER_SCRIPT,
    derive_output_script,
    expected_hrp,
    is_silent_payment_request_text,
    parse_silent_payment_request,
)
from .spscand_client import ReceivedOutput

CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
DUST_LIMIT = 546
RBF_SEQUENCE = 0xFFFFFFFD


class SpendError(Exception):
    """A problem building or signing a spend, shown to the user as is."""


def output_secret(spend_secret: int, output: ReceivedOutput) -> int:
    """The private key of a received output, checked against its x-only key."""
    from electrum_ecc import ECPrivkey

    secret = (spend_secret + int(output.tweak, 16)) % CURVE_ORDER
    if secret == 0:
        raise SpendError(f"spscand sent an unusable tweak for {output.outpoint}.")
    xonly = ECPrivkey(secret.to_bytes(32, "big")).get_public_key_bytes()[1:]
    if xonly.hex() != output.pubkey:
        raise SpendError(
            f"The tweak spscand reported for {output.outpoint} does not give its key. "
            "Nothing was signed."
        )
    return secret


def taproot_input_key(secret: int) -> int:
    """The key BIP352 counts for a taproot input: negated if its point has odd y."""
    from electrum_ecc import ECPrivkey

    odd = ECPrivkey(secret.to_bytes(32, "big")).get_public_key_bytes()[0] == 3
    return CURVE_ORDER - secret if odd else secret


def silent_payment_recipient(destination: str):
    """The silent payment address in destination, or None for an ordinary address."""
    from electrum import constants

    if not is_silent_payment_request_text(destination):
        return None
    request = parse_silent_payment_request(
        destination, expected_hrp=expected_hrp(is_testnet=constants.net.TESTNET)
    )
    return request.recipient


def estimated_vsize(input_count: int, output_scripts: Sequence[bytes]) -> int:
    """Virtual size of a transaction spending taproot key-path inputs."""
    weight = (
        4 * (4 + 1 + 1 + 4) + 2
    )  # version, input and output counts, locktime; marker
    weight += input_count * (
        4 * (36 + 1 + 4) + 66
    )  # outpoint, empty script, sequence; sig
    weight += sum(4 * (8 + 1 + len(script)) for script in output_scripts)
    return math.ceil(weight / 4)


def plan_fee(
    outputs: Sequence[ReceivedOutput],
    *,
    destination_script: bytes,
    change_script: bytes,
    amount: Optional[int],
    fee_rate: float,
) -> tuple[int, int, int]:
    """(amount sent, change, fee) for these inputs; amount None sends everything."""
    if not outputs:
        raise SpendError("Select at least one payment to spend.")
    if fee_rate <= 0:
        raise SpendError("The fee rate must be positive.")
    total = sum(o.amount for o in outputs)
    if amount is None:
        fee = math.ceil(estimated_vsize(len(outputs), [destination_script]) * fee_rate)
        send = total - fee
        if send < DUST_LIMIT:
            raise SpendError("The selected payments do not cover the fee.")
        return send, 0, fee
    if amount < DUST_LIMIT:
        raise SpendError("The amount is too small to send.")
    fee = math.ceil(
        estimated_vsize(len(outputs), [destination_script, change_script]) * fee_rate
    )
    change = total - amount - fee
    if change < 0:
        raise SpendError("The selected payments do not cover the amount and the fee.")
    if change < DUST_LIMIT:
        # Too little to keep: it goes to the fee instead.
        fee += change
        change = 0
    return amount, change, fee


def build_signed_spend(
    outputs: Sequence[ReceivedOutput],
    *,
    spend_secret: int,
    destination: str,
    change_address: str,
    amount: Optional[int],
    fee_rate: float,
    locktime: int,
):
    """A signed transaction spending these received outputs."""
    from electrum.bitcoin import address_to_script, construct_witness
    from electrum.transaction import (
        PartialTransaction,
        PartialTxInput,
        PartialTxOutput,
        TxOutpoint,
        TxOutput,
    )
    from electrum_ecc import ECPrivkey, ECPubkey
    from electrum_ecc.util import bip340_tagged_hash

    secrets = [output_secret(spend_secret, o) for o in outputs]
    recipient = silent_payment_recipient(destination)
    destination_script = (
        PLACEHOLDER_SCRIPT if recipient else address_to_script(destination)
    )
    change_script = address_to_script(change_address)
    send, change, _fee = plan_fee(
        outputs,
        destination_script=destination_script,
        change_script=change_script,
        amount=amount,
        fee_rate=fee_rate,
    )
    inputs = []
    for output in outputs:
        txin = PartialTxInput(
            prevout=TxOutpoint(txid=bytes.fromhex(output.txid), out_idx=output.vout),
            nsequence=RBF_SEQUENCE,
        )
        txin.witness_utxo = TxOutput(
            scriptpubkey=bytes.fromhex("5120" + output.pubkey), value=output.amount
        )
        inputs.append(txin)
    if recipient:
        # BIP352 with our inputs, which are all taproot key-path spends.
        destination_script = derive_output_script(
            private_keys=[taproot_input_key(s).to_bytes(32, "big") for s in secrets],
            serialized_outpoints=[
                bytes.fromhex(o.txid)[::-1] + o.vout.to_bytes(4, "little")
                for o in outputs
            ],
            recipient=recipient,
        )
    tx_outputs = [PartialTxOutput(scriptpubkey=destination_script, value=send)]
    if change:
        tx_outputs.append(PartialTxOutput(scriptpubkey=change_script, value=change))
    tx = PartialTransaction.from_io(
        inputs, tx_outputs, locktime=locktime, version=2, BIP69_sort=True
    )
    by_outpoint = {(o.txid, o.vout): s for o, s in zip(outputs, secrets)}
    for index, txin in enumerate(tx.inputs()):
        secret = by_outpoint[(txin.prevout.txid.hex(), txin.prevout.out_idx)]
        message = bip340_tagged_hash(b"TapSighash", tx.serialize_preimage(index))
        key = ECPrivkey(secret.to_bytes(32, "big"))
        signature = key.schnorr_sign(message)
        xonly = key.get_public_key_bytes()[1:]
        if not ECPubkey(b"\x02" + xonly).schnorr_verify(signature, message):
            raise SpendError("A signature did not verify. Nothing was sent.")
        txin.script_sig = b""
        txin.witness = construct_witness([signature])
    tx.invalidate_ser_cache()
    if not tx.is_complete():
        raise SpendError("The transaction is not fully signed.")
    return tx
