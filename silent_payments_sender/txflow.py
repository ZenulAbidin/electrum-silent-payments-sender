"""Electrum transaction integration kept separate from the Qt UI."""

from dataclasses import dataclass
from hashlib import sha256
from inspect import signature
from types import MethodType

from .core import (
    PLACEHOLDER_SCRIPT,
    SUPPORTED_TXIN_TYPES,
    DerivationFailure,
    SilentPaymentAddress,
    UnsupportedWallet,
    derive_output_script,
    transaction_commitment,
)


@dataclass(frozen=True)
class FinalizedSilentOutput:
    output_index: int
    scriptpubkey: bytes
    amount_sat: int
    commitment: bytes
    construction_digest: bytes
    unsigned_tx_digest: bytes


OUTPUT_DISPLAY_WIDTH = 42
OUTPUT_CONTINUATION_PREFIX = (" " * 15) + "\t"
SILENT_ADDRESS_DISPLAY_EDGE_CHARS = 8


def make_unsigned_silent_transaction(
    *,
    wallet,
    fee_policy,
    coins,
    outputs,
):
    """Build with Electrum's wallet change policy and reject fee leakage."""
    change_addresses = wallet.get_change_addresses_for_new_transaction()
    tx = wallet.make_unsigned_transaction(
        fee_policy=fee_policy,
        coins=coins,
        outputs=outputs,
        change_addr=change_addresses,
        base_tx=None,
        is_sweep=False,
        rbf=False,
        send_change_to_lightning=False,
        merge_duplicate_outputs=False,
    )

    silent_outputs = [
        output for output in tx.outputs()
        if output.scriptpubkey == PLACEHOLDER_SCRIPT
    ]
    if len(silent_outputs) != 1:
        raise DerivationFailure(
            "Expected exactly one silent-payment placeholder output."
        )
    change_outputs = [
        output for output in tx.outputs()
        if output is not silent_outputs[0]
    ]
    if not wallet.multiple_change and len(change_outputs) > 1:
        raise DerivationFailure(
            "Electrum created multiple change outputs while that setting is disabled."
        )
    if len(change_outputs) > wallet.max_change_outputs:
        raise DerivationFailure("Electrum created too many change outputs.")

    if change_addresses:
        expected_addresses = set(change_addresses)
        if any(output.address not in expected_addresses for output in change_outputs):
            raise DerivationFailure(
                "Electrum did not use the wallet's selected change addresses."
            )
    elif change_outputs:
        input_addresses = {
            txin.address for txin in tx.inputs()
            if getattr(txin, "address", None)
        }
        if any(output.address not in input_addresses for output in change_outputs):
            raise DerivationFailure(
                "Electrum did not return change to a selected input address."
            )

    fee = tx.get_fee()
    estimated_fee = fee_policy.estimate_fee(
        tx.estimated_size(),
        network=wallet.network,
    )
    allowed_rounding = wallet.dust_threshold() + 100
    if (
        not isinstance(fee, int)
        or fee < 0
        or fee > estimated_fee + allowed_rounding
    ):
        raise DerivationFailure(
            "Electrum did not create a change output and assigned the remainder "
            "to the transaction fee. Nothing was signed."
        )
    return tx


def _display_chunks(address: str) -> list[str]:
    return [
        address[offset:offset + OUTPUT_DISPLAY_WIDTH]
        for offset in range(0, len(address), OUTPUT_DISPLAY_WIDTH)
    ]


class FullSilentPaymentDisplay(str):
    """Wrap complete addresses within Electrum's existing output columns."""

    def __new__(cls, recipient_address: str, derived_address: str):
        lines = (
            _display_chunks(recipient_address)
            + _display_chunks(derived_address)
        )
        # Electrum appends the amount after the returned address text. Pad the
        # final wrapped line to keep that amount in its normal column.
        lines[-1] = lines[-1].ljust(OUTPUT_DISPLAY_WIDTH)
        rendered = lines[0] + "".join(
            "\n" + OUTPUT_CONTINUATION_PREFIX + line
            for line in lines[1:]
        )
        return super().__new__(cls, rendered)

    def __len__(self) -> int:
        # TxInOutWidget truncates strings longer than its 42-character column.
        # The rendered lines are already wrapped to exactly that width.
        return OUTPUT_DISPLAY_WIDTH


def abbreviate_silent_payment_address(address: str) -> str:
    edge = SILENT_ADDRESS_DISPLAY_EDGE_CHARS
    if len(address) <= edge * 2 + 1:
        return address
    return address[:edge] + "…" + address[-edge:]

def silent_payment_output_label(
    *,
    recipient_address: str,
    derived_address: str,
) -> str:
    """Fit both identities into the Send preview's fixed output column."""
    return (
        abbreviate_silent_payment_address(recipient_address)
        + " ("
        + abbreviate_silent_payment_address(derived_address)
        + ")"
    )


def full_silent_payment_output_label(
    *,
    recipient_address: str,
    derived_address: str,
) -> str:
    """Show both complete identities in final transaction details."""
    return FullSilentPaymentDisplay(
        recipient_address,
        derived_address,
    )


def silent_payment_history_label(
    *,
    recipient_address: str,
    derived_address: str,
    description: str = "",
) -> str:
    """Keep Electrum's History description concise."""
    return description or "Silent Payment"


def normalize_silent_payment_records(records) -> dict[str, dict[str, str]]:
    """Detach wallet-DB wrappers so Electrum can safely deepcopy and persist them."""
    normalized = {}
    if not isinstance(records, dict):
        return normalized
    for txid, record in records.items():
        if not isinstance(txid, str) or not isinstance(record, dict):
            continue
        recipient = record.get("recipient_address")
        derived = record.get("derived_address")
        if not isinstance(recipient, str) or not isinstance(derived, str):
            continue
        normalized[str(txid)] = {
            "recipient_address": str(recipient),
            "derived_address": str(derived),
        }
    return normalized


def annotate_silent_payment_output_addresses(
    tx,
    *,
    output_index: int,
    recipient_address: str,
    full: bool = False,
) -> None:
    """Attach Silent Payment display metadata to one transaction output."""
    output = tx.outputs()[output_index]
    label_factory = (
        full_silent_payment_output_label
        if full
        else silent_payment_output_label
    )
    display_text = label_factory(
        recipient_address=recipient_address,
        derived_address=output.address,
    )
    output._silent_payment_address = recipient_address
    output._silent_payment_derived_address = output.address
    output.get_ui_address_str = MethodType(
        lambda _output: display_text,
        output,
    )


def annotate_silent_payment_output(
    tx,
    finalized: FinalizedSilentOutput,
    recipient: SilentPaymentAddress,
) -> None:
    """Display the reusable SP address while preserving the actual P2TR output."""
    annotate_silent_payment_output_addresses(
        tx,
        output_index=finalized.output_index,
        recipient_address=recipient.encoded,
    )


def confirm_transaction_compat(*, window, make_tx, amount_sat):
    """Normalize Electrum 4.6 and 4.7+ confirmation-dialog APIs."""
    parameters = signature(window.confirm_tx_dialog).parameters
    kwargs = {"batching_candidates": []}
    if "payee_outputs" in parameters:
        kwargs["payee_outputs"] = None
    result = window.confirm_tx_dialog(make_tx, amount_sat, **kwargs)
    if not isinstance(result, tuple):
        raise SilentPaymentError(
            "Electrum returned an unexpected transaction confirmation result."
        )
    if len(result) == 2:
        tx, is_preview = result
        return tx, is_preview, False
    if len(result) == 3:
        return result
    raise SilentPaymentError(
        "Electrum returned an unsupported transaction confirmation result."
    )


def validate_wallet(wallet) -> None:
    if getattr(wallet, "wallet_type", None) != "standard":
        raise UnsupportedWallet(
            "Only standard single-signature wallets are supported. "
            "Imported, multisig, and 2FA wallets are not supported."
        )
    if wallet.is_watching_only():
        raise UnsupportedWallet("Watch-only wallets cannot create silent payments.")
    keystore = wallet.get_keystore()
    if keystore is None or getattr(keystore, "type", None) != "bip32":
        raise UnsupportedWallet(
            "Only deterministic BIP32 software keystores are supported. "
            "Hardware and legacy keystores are not supported."
        )
    txin_type = wallet.get_txin_type()
    if txin_type not in SUPPORTED_TXIN_TYPES:
        raise UnsupportedWallet(
            f"Wallet input type {txin_type!r} is not supported for silent payments."
        )


def _selected_private_keys(wallet, tx, password) -> tuple[list[bytes], list[bytes]]:
    keystore = wallet.get_keystore()
    private_keys: list[bytes] = []
    serialized_outpoints: list[bytes] = []
    for txin in tx.inputs():
        wallet.add_input_info(txin)
        address = txin.address
        if not address or not wallet.is_mine(address):
            raise UnsupportedWallet(
                "Every transaction input must belong to this wallet."
            )
        if wallet.get_txin_type(address) not in SUPPORTED_TXIN_TYPES:
            raise UnsupportedWallet(
                "The transaction contains an unsupported input script type."
            )
        address_index = wallet.get_address_index(address)
        if address_index is None:
            raise UnsupportedWallet("Could not derive one of the selected input keys.")
        secret, compressed = keystore.get_private_key(address_index, password)
        if not compressed:
            raise UnsupportedWallet("Uncompressed input keys are not supported.")
        private_keys.append(secret)
        serialized_outpoints.append(txin.prevout.serialize_to_network())
    if not private_keys:
        raise DerivationFailure("The transaction has no inputs.")
    return private_keys, serialized_outpoints


def finalize_transaction(
    *,
    wallet,
    tx,
    password,
    recipient: SilentPaymentAddress,
) -> FinalizedSilentOutput:
    """Replace the unique placeholder and bind the derived output to the tx inputs."""
    validate_wallet(wallet)
    matches = [
        index for index, output in enumerate(tx.outputs())
        if output.scriptpubkey == PLACEHOLDER_SCRIPT
    ]
    if len(matches) != 1:
        raise DerivationFailure(
            "Expected exactly one silent-payment placeholder output."
        )
    output_index = matches[0]
    output = tx.outputs()[output_index]
    if not isinstance(output.value, int) or output.value <= 0:
        raise DerivationFailure("The silent-payment amount is invalid.")

    private_keys, serialized_outpoints = _selected_private_keys(wallet, tx, password)
    scriptpubkey = derive_output_script(
        private_keys=private_keys,
        serialized_outpoints=serialized_outpoints,
        recipient=recipient,
    )
    output.scriptpubkey = scriptpubkey
    tx.set_rbf(False)
    tx.invalidate_ser_cache()
    commitment = transaction_commitment(
        serialized_outpoints=serialized_outpoints,
        recipient_script=scriptpubkey,
        amount_sat=output.value,
    )
    construction_digest = _construction_digest(tx)
    unsigned_tx_digest = _unsigned_tx_digest(tx)
    return FinalizedSilentOutput(
        output_index=output_index,
        scriptpubkey=scriptpubkey,
        amount_sat=output.value,
        commitment=commitment,
        construction_digest=construction_digest,
        unsigned_tx_digest=unsigned_tx_digest,
    )


def _construction_digest(tx) -> bytes:
    """Hash inputs and all outputs, excluding locktime and input sequences."""
    material = len(tx.inputs()).to_bytes(4, "big")
    for txin in tx.inputs():
        material += txin.prevout.serialize_to_network()
    material += len(tx.outputs()).to_bytes(4, "big")
    for output in tx.outputs():
        material += output.serialize_to_network()
    return sha256(material).digest()


def _unsigned_tx_digest(tx) -> bytes:
    serialized = tx.serialize_to_network(include_sigs=False)
    if isinstance(serialized, str):
        serialized = bytes.fromhex(serialized)
    return sha256(serialized).digest()


def seal_after_confirmation(tx, finalized: FinalizedSilentOutput) -> FinalizedSilentOutput:
    """Accept Electrum's locktime/RBF UI updates, reject input/output changes."""
    outputs = tx.outputs()
    if finalized.output_index >= len(outputs):
        raise DerivationFailure("The silent-payment output disappeared.")
    output = outputs[finalized.output_index]
    if (
        output.scriptpubkey != finalized.scriptpubkey
        or output.value != finalized.amount_sat
        or _construction_digest(tx) != finalized.construction_digest
    ):
        raise DerivationFailure(
            "The transaction inputs or outputs changed after BIP352 derivation."
        )
    tx.set_rbf(False)
    return FinalizedSilentOutput(
        output_index=finalized.output_index,
        scriptpubkey=finalized.scriptpubkey,
        amount_sat=finalized.amount_sat,
        commitment=finalized.commitment,
        construction_digest=finalized.construction_digest,
        unsigned_tx_digest=_unsigned_tx_digest(tx),
    )


def verify_transaction(tx, finalized: FinalizedSilentOutput) -> bool:
    outputs = tx.outputs()
    if finalized.output_index >= len(outputs):
        return False
    output = outputs[finalized.output_index]
    if output.scriptpubkey != finalized.scriptpubkey:
        return False
    if output.value != finalized.amount_sat:
        return False
    commitment = transaction_commitment(
        serialized_outpoints=[txin.prevout.serialize_to_network() for txin in tx.inputs()],
        recipient_script=output.scriptpubkey,
        amount_sat=output.value,
    )
    return (
        commitment == finalized.commitment
        and _construction_digest(tx) == finalized.construction_digest
        and _unsigned_tx_digest(tx) == finalized.unsigned_tx_digest
    )
