# Silent Payments Sender for Electrum

[![Tests](https://github.com/ZenulAbidin/electrum-silent-payments-sender/actions/workflows/tests.yml/badge.svg)](https://github.com/ZenulAbidin/electrum-silent-payments-sender/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/v/release/ZenulAbidin/electrum-silent-payments-sender)](https://github.com/ZenulAbidin/electrum-silent-payments-sender/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A sender implementation of [BIP352 Silent Payments](https://bips.dev/352/) for Electrum's Qt desktop wallet.

It integrates directly into Electrum's existing **Send** tab. Paste a Silent Payment address into **Pay to**, enter the amount, and press **Pay…** as usual. The plugin detects the address and builds a normal Bitcoin transaction whose recipient output is derived from the wallet's selected inputs. 

## Safety status

This is an independent plugin, not an official Electrum feature, and has not received a professional security audit. Review the source, verify the release hash, test on testnet, and use a small amount for any first mainnet payment.

## Features

Version 1.0.0 targets BIP352 1.1.1 and supports:

- Electrum 4.6.0 or newer, Qt desktop GUI
- Standard, single-signature deterministic software wallets
- Compressed-key P2PKH, nested SegWit, and native SegWit inputs
- `sp1...` on mainnet and `tsp1...` on testnet, signet, or regtest
- Raw addresses and BIP21 `sp` payment instructions
- Forward-compatible Silent Payment address versions 1 through 30

Not supported yet:

- Hardware, watch-only, imported-key, multisig, and 2FA wallets
- Taproot inputs, transaction batching, payjoin, and swaps
- Unsigned preview/export and later signing
- Multiple recipients or multiple silent outputs

The transaction is made non-RBF, although with the mempoolfullrbf protocol flag, it can signal RBF support anyway. The plugin derives the final output only after Electrum selects the inputs, verifies the inputs and output again after the fee confirmation dialog, and then signs immediately. These restrictions keep the input-dependent BIP352 derivation bound to the transaction that is signed.

## Install

1. Download the `silent_payments_sender-*.zip` installable asset from the [latest release](https://github.com/ZenulAbidin/electrum-silent-payments-sender/releases/latest) and verify its SHA-256 against the release's `SHA256SUMS-*` asset.
2. In Electrum, open **Tools → Plugins**.
3. Click **Add plugin**, select the ZIP, review Electrum's external-plugin warning, and enable **Silent Payments Sender**.
4. Restart/reload the wallet if Electrum asks, then open the normal **Send** tab.

## Use

Paste a BIP352 address or a BIP21 URI containing the `sp` parameter into the normal **Pay to** field, enter the amount in Electrum's normal **Amount** field, and press **Pay...**. The existing Description field is retained. If the URI contains an amount, the plugin fills the amount when the field is empty.

In Electrum's **New Transaction** send preview, the payment output uses the compact single-line form `first-8...last-8 (bc1p-first-8...last-8)`. In final transaction details, the Outputs area wraps the complete reusable `sp1...`/`tsp1...` recipient and the complete one-time derived `bc1p...`/`tb1p...` output into aligned 42-character lines. The output's right-click **Copy Address** action continues to copy the derived on-chain address.

Both the Silent Payment address and the derived Taproot address can be copied from the transaction details dialog.

Coin control is supported through Electrum's normal Coins tab selection. If an extremely unlikely invalid scalar is encountered, select a different set of coins and retry.

## Community and support

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.
- Use [GitHub Discussions](https://github.com/ZenulAbidin/electrum-silent-payments-sender/discussions) for usage questions and [GitHub Issues](https://github.com/ZenulAbidin/electrum-silent-payments-sender/issues) for reproducible non-security bugs.
- Read [SECURITY.md](SECURITY.md) and use [private vulnerability reporting](https://github.com/ZenulAbidin/electrum-silent-payments-sender/security/advisories/new) for security-sensitive reports. Never post wallet seeds, private keys, or other secrets.

## License and attribution

See `LICENSE` and `NOTICE`.

The BIP352 derivation is adapted from Electrum PR [#9900](https://github.com/spesmilo/electrum/pull/9900) by MorenoProg and Electrum contributors.

The packaged plugin includes the SilentPayments.xyz SVG mark as its Electrum plugin-manager icon.
