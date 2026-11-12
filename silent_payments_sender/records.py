"""Per-wallet Silent Payment records shared by the Qt and QML front ends."""

from .txflow import normalize_silent_payment_records

SILENT_PAYMENT_RECORDS_KEY = "silent_payments_sender_records"


def store_silent_payment_record(
    wallet,
    *,
    txid: str,
    recipient_address: str,
    derived_address: str,
) -> None:
    records = normalize_silent_payment_records(
        wallet.db.get(SILENT_PAYMENT_RECORDS_KEY, {})
    )
    records[str(txid)] = {
        "recipient_address": str(recipient_address),
        "derived_address": str(derived_address),
    }
    wallet.db.put(SILENT_PAYMENT_RECORDS_KEY, records)
    wallet.save_db()


def get_silent_payment_record(wallet, txid: str):
    records = wallet.db.get(SILENT_PAYMENT_RECORDS_KEY, {})
    if not isinstance(records, dict):
        return None
    record = records.get(txid)
    if not isinstance(record, dict):
        return None
    recipient = record.get("recipient_address")
    derived = record.get("derived_address")
    if not isinstance(recipient, str) or not isinstance(derived, str):
        return None
    return recipient, derived
