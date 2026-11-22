# Changelog

## 1.2.1

- Fix a crash report from the transaction window when "send change to Lightning" is on: change
  from a silent payment now stays on-chain, and Electrum says so first
- Show other errors while building a silent payment as a message instead of a crash report

## 1.2.0

- Receive silent payments on the Qt desktop GUI with spscand 0.1.1 or newer, which holds the scan
  key and does the scanning; the spend key stays in Electrum. A Silent Payments tab lists the
  payments, including unconfirmed ones (counted as incoming, not in the balance), and updates as
  soon as spscand reports something
- Spend received payments to an address or to another silent payment address; spending
  unconfirmed payments is off unless enabled
- Labelled receiving addresses, rescans, and connecting with the line `spscand pair` prints
- BIP352 receiving keys (`m/352'/…`); a wallet restored from BIP39 seed words asks for them once
- Add tests for receiving and a regtest harness against a real spscand

## 1.1.0

- Add Electrum QML GUI support (the interface of Electrum for Android; `electrum -g qml` on desktop)
- Require Electrum 4.8.3 or newer, the first release fixing CVE-2026-117601 (GHSA-6jp4-8v48-cc5v)
- Update to the BIP352 1.1.2 test vectors; every official vector is now explicitly classified
- Fix a missing import on an unexpected confirmation-dialog result
- Add an end-to-end regtest harness for both GUIs

## 1.0.1

- Add a repository-level Electrum plugin manifest

## 1.0.0

- Initial release
