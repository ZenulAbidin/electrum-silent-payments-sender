import unittest

from silent_payments_sender.receive_state import (
    balance,
    display_order,
    spendable,
    status_text,
)
from silent_payments_sender.spscand_client import ReceivedOutput


def output(
    txid, amount, *, height=100, confirmations=3, spent_height=None, pending=None
):
    return ReceivedOutput(
        txid=txid * 64,
        vout=0,
        amount=amount,
        pubkey="b" * 64,
        tweak="c" * 64,
        label=None,
        status="confirmed" if height is not None else "unconfirmed",
        height=height,
        confirmations=confirmations if height is not None else 0,
        spent_txid=None,
        spent_height=spent_height,
        pending_spent_txid=pending,
    )


CONFIRMED = output("a", 1000, height=100, confirmations=4)
PENDING_SPEND = output("b", 2000, height=101, confirmations=3, pending="e" * 64)
INCOMING = output("c", 300, height=None)
SPENT = output("d", 5000, height=90, confirmations=15, spent_height=102)
ALL = [SPENT, CONFIRMED, INCOMING, PENDING_SPEND]


class TestReceiveState(unittest.TestCase):
    def test_balance(self):
        result = balance(ALL)
        self.assertEqual(result.confirmed, 3000)
        self.assertEqual(result.incoming, 300)
        self.assertEqual(result.outgoing, 2000)
        self.assertEqual(result.spendable, 1000)

    def test_status_text(self):
        self.assertEqual(status_text(CONFIRMED), "4 confirmations")
        self.assertEqual(status_text(output("f", 1, confirmations=1)), "1 confirmation")
        self.assertEqual(status_text(INCOMING), "Unconfirmed")
        self.assertEqual(status_text(PENDING_SPEND), "Spending (unconfirmed)")
        self.assertEqual(status_text(SPENT), "Spent in block 102")

    def test_unconfirmed_spending_is_opt_in(self):
        self.assertEqual(spendable(ALL, allow_unconfirmed=False), [CONFIRMED])
        self.assertEqual(spendable(ALL, allow_unconfirmed=True), [CONFIRMED, INCOMING])

    def test_display_order(self):
        self.assertEqual(
            display_order(ALL), [INCOMING, PENDING_SPEND, CONFIRMED, SPENT]
        )


if __name__ == "__main__":
    unittest.main()
