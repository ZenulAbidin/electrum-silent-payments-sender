"""What the Silent Payments tab shows, computed from spscand's outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .spscand_client import ReceivedOutput


@dataclass(frozen=True)
class Balance:
    confirmed: int  # confirmed and unspent, including outputs an unconfirmed spend uses
    incoming: int  # unconfirmed payments, not yet counted in confirmed
    outgoing: int  # confirmed outputs that an unconfirmed transaction spends

    @property
    def spendable(self) -> int:
        return self.confirmed - self.outgoing


def balance(outputs: Iterable[ReceivedOutput]) -> Balance:
    confirmed = incoming = outgoing = 0
    for output in outputs:
        if output.spent:
            continue
        if not output.confirmed:
            incoming += output.amount
            continue
        confirmed += output.amount
        if output.pending_spent_txid:
            outgoing += output.amount
    return Balance(confirmed, incoming, outgoing)


def status_text(output: ReceivedOutput) -> str:
    if output.spent:
        return f"Spent in block {output.spent_height}"
    if output.pending_spent_txid:
        return "Spending (unconfirmed)"
    if not output.confirmed:
        return "Unconfirmed"
    if output.confirmations == 1:
        return "1 confirmation"
    return f"{output.confirmations} confirmations"


def spendable(
    outputs: Iterable[ReceivedOutput], *, allow_unconfirmed: bool
) -> list[ReceivedOutput]:
    """Outputs that can go into a new transaction."""
    return [
        o
        for o in outputs
        if not o.spent
        and not o.pending_spent_txid
        and (o.confirmed or allow_unconfirmed)
    ]


def display_order(outputs: Iterable[ReceivedOutput]) -> list[ReceivedOutput]:
    """Unconfirmed first, then newest first; spent outputs last."""
    return sorted(
        outputs, key=lambda o: (o.spent, o.confirmed, -(o.height or 0), o.txid, o.vout)
    )
