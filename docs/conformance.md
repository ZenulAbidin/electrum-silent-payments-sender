# BIP352 conformance

Target: BIP352 1.1.2 (2026-10-25).

Runtime scope: Electrum 4.8.3+ (Qt and QML GUIs), mainnet, testnet, signet, and regtest.

## Implemented sender requirements

- Bech32m decoding with strict `sp`/`tsp` network HRP separation.
- Exact 66-byte payload validation for version 0.
- Forward-compatible versions 1–30 use the first 66 payload bytes.
- Backward-incompatible version 31 is rejected.
- BIP21 `sp` payment instructions and optional exact-satoshi `amount`.
- Unified Electrum Send-tab recognition for raw Silent Payment addresses and BIP21 `sp` payment instructions, in the Qt Send tab and in the QML payment-identifier flow (scan, paste, and opened links).
- P2PKH, P2SH-P2WPKH, and P2WPKH compressed-key inputs.
- Every selected input must be owned by the same standard BIP32 software
  wallet and eligible for shared-secret derivation.
- Lexicographically smallest serialized outpoint.
- Private-key summation with zero-sum rejection.
- `BIP0352/Inputs` and `BIP0352/SharedSecret` tagged hashes with invalid-scalar rejection.
- BIP341 Taproot recipient output.
- SIGHASH_ALL through Electrum's standard non-Taproot input signer.
- Input/output integrity verification immediately before signing.
- Non-RBF transactions, preventing post-sign input replacement.

## Deliberate scope limits

This release does not support receiving/scanning, sending the maximum amount, Taproot inputs, multiple Silent Payment outputs, hardware signing/BIP375, collaborative transactions, or RBF.

## Design notes

Electrum's normal recipient parser does not currently understand BIP352 addresses. The plugin recognizes them at the Send-tab boundary and routes only those payments through its BIP352 transaction builder; ordinary Bitcoin and Lightning recipients continue through Electrum unchanged. It uses a 34-byte P2TR placeholder during fee/coin selection. After inputs are known, it replaces that placeholder with the BIP352-derived P2TR script of exactly the same serialized size.

In the QML GUI, the plugin's QML document is read from the authorized plugin archive in memory and never written to disk. It uses only QML module imports; Electrum's confirmation dialog is loaded asynchronously, because compiling a file-based dependency synchronously can deadlock with Electrum's Python translator. Because the QML wallet is already unlocked, the output is derived when Electrum's fee dialog builds the transaction; signing still waits for Electrum's payment authentication, and the seal and integrity check run first.

The plugin never sends private keys anywhere. It derives each selected input's private key inside Electrum, uses the sum only to derive the recipient output, and lets the wallet's normal signer sign the transaction.

## Tests

- The complete upstream `bitcoin/bips` BIP352 1.1.2 vector JSON (commit a0190d01) is vendored at `tests/bip352-1.1.2-vectors.json`.
- `tests/test_official_vectors.py` runs every official sender vector fitting the documented scope, including ordering, repeated keys, labeled recipient addresses, non-standard P2PKH scriptSigs, zero-sum rejection, and the 1.1.1 intermediate-zero regression. Every other official vector is listed with the reason it is out of scope, so a new upstream vector fails the test until it is reviewed.
- `scripts/electrum_cli_harness.py` runs the full mainnet or testnet transaction lifecycle under official Electrum AppImage runtimes for every supported input type without connecting or broadcasting.
- `scripts/silent_payment_test_receiver.py` generates receiver keys, scans a complete signed sender transaction, and creates a direct BIP341 key-path recovery transaction to a normal SegWit address.
- `scripts/electrum_recovery_harness.py` independently verifies the recovery sighash and Schnorr signature using Electrum's own BIP341 implementation.
- `scripts/electrum_regtest_harness.py` drives the real Qt and QML GUIs through a broadcast payment on regtest for every supported input type and a BIP21 URI with an amount, and checks the result with the independent receiver.
- Published results cover Electrum 4.8.3 and 4.8.4: offline mainnet and testnet lifecycle, recovery, and the regtest GUI matrix in `test-results/regtest/`.

Upstream vector source:
https://github.com/bitcoin/bips/blob/master/bip-0352/send_and_receive_test_vectors.json
