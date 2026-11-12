# Changelog

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
