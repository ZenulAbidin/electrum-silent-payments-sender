# BIP352 conformance

Target: BIP352 1.1.1 (2026-04-16).

Runtime scope: Electrum 4.6.0+, mainnet, testnet, signet, and regtest.

## Implemented sender requirements

- Bech32m decoding with strict `sp`/`tsp` network HRP separation.
- Exact 66-byte payload validation for version 0.
- Forward-compatible versions 1–30 use the first 66 payload bytes.
- Backward-incompatible version 31 is rejected.
- BIP21 `sp` payment instructions and optional exact-satoshi `amount`.
- Unified Electrum Send-tab recognition for raw Silent Payment addresses and BIP21 `sp` payment instructions.
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

This release does not support receiving/scanning, Taproot inputs, multiple Silent Payment outputs, hardware signing/BIP375, collaborative transactions, or RBF.

## Design notes

Electrum's normal recipient parser does not currently understand BIP352 addresses. The plugin recognizes them at the Send-tab boundary and routes only those payments through its BIP352 transaction builder; ordinary Bitcoin and Lightning recipients continue through Electrum unchanged. It uses a 34-byte P2TR placeholder during fee/coin selection. After inputs are known, it replaces that placeholder with the BIP352-derived P2TR script of exactly the same serialized size.

The plugin never sends private keys anywhere. It derives each selected input's private key inside Electrum, uses the sum only to derive the recipient output, and lets the wallet's normal signer sign the transaction.

## Tests

- The complete upstream `bitcoin/bips` BIP352 1.1.1 vector JSON is vendored at `tests/bip352-1.1.1-vectors.json`.
- `tests/test_official_vectors.py` runs every official sender vector fitting the documented scope, including ordering, repeated keys, labeled recipient addresses, non-standard P2PKH scriptSigs, zero-sum rejection, and the 1.1.1 intermediate-zero regression.
- `scripts/electrum_cli_harness.py` runs the full mainnet or testnet transaction lifecycle under official Electrum AppImage runtimes for every supported input type without connecting or broadcasting.
- `scripts/silent_payment_test_receiver.py` generates receiver keys, scans a complete signed sender transaction, and creates a direct BIP341 key-path recovery transaction to a normal SegWit address.
- `scripts/electrum_recovery_harness.py` independently verifies the recovery sighash and Schnorr signature using Electrum's own BIP341 implementation.
- Published mainnet and testnet results cover Electrum 4.6.0, 4.7.2, and 4.8.0. Electrum 4.5.8 is the tested incompatible boundary because it lacks `electrum_ecc`.

Upstream vector source:
https://github.com/bitcoin/bips/blob/master/bip-0352/send_and_receive_test_vectors.json
