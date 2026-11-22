# Silent Payments Sender for Electrum

[![Tests](https://github.com/ZenulAbidin/electrum-silent-payments-sender/actions/workflows/tests.yml/badge.svg)](https://github.com/ZenulAbidin/electrum-silent-payments-sender/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/v/release/ZenulAbidin/electrum-silent-payments-sender)](https://github.com/ZenulAbidin/electrum-silent-payments-sender/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

![Silent Payments Sender logo](https://talkimg.com/images/2026/07/26/Uh9ryv.png)

A sender implementation of [BIP352 Silent Payments](https://bips.dev/352/) for Electrum's Qt desktop wallet and its QML (mobile) interface.

It integrates directly into Electrum's existing **Send** tab. Paste a Silent Payment address into **Pay to**, enter the amount, and press **Pay…** as usual. The plugin detects the address and builds a normal Bitcoin transaction whose recipient output is derived from the wallet's selected inputs. 

On the Qt desktop GUI it can also receive silent payments, with [spscand](https://github.com/ZenulAbidin/spscand) doing the scanning on your own computer or server: see [Receive](#receive).

## Safety status

This is an independent plugin, not an official Electrum feature, and has not received a professional security audit. Review the source, verify the release hash, test on testnet, and use a small amount for any first mainnet payment.

## Features

Version 1.2.1 targets BIP352 1.1.2 and supports:

- Electrum 4.8.3 or newer, Qt desktop GUI and QML GUI (4.8.3 fixes CVE-2026-117601, a server-side transaction-replacement attack)
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

## Receive

Receiving needs [spscand](https://github.com/ZenulAbidin/spscand) 0.1.1 or newer, running where you choose: the same computer, a home server or a VPS. spscand holds the scan key and finds payments to your silent payment address; Electrum keeps the spend key and signs. The Qt GUI shows a **Silent Payments** tab for standard software wallets (not hardware, watch-only or multisig wallets).

1. In the tab, press **Set up receiving…**, choose the block to start scanning from, and enter the wallet password. A wallet restored from BIP39 seed words asks for those words once, because Electrum does not keep them; they are checked against the wallet and not stored. The keys are BIP352's, at `m/352'/0'/0'/1'/0` (scan) and `m/352'/0'/0'/0'/0` (spend), with `1'` instead of the second `0'` on test networks, so other BIP352 wallets restored from the same seed find the same payments.
2. Settings then show an `spscand init …` command and the scan key. Run the command where spscand lives and paste the scan key when it asks. The scan key lets spscand see your payments, never spend them.
3. Start `spscand run`, run `spscand pair` (or `spscand pair --url http://….onion:7352` for a Tor onion service), and paste the line it prints into **Pairing string** in Settings.

spscand on the same computer is reached directly. A `.onion` address is reached through the Tor proxy set in Electrum's network settings, and any other address only over `https`.

The tab lists every payment found, with its status, amount, label and block. Payments still in the mempool show as **Unconfirmed** and count as incoming, not in the balance; the sender can still replace them. spscand shows them only when it watches the mempool, which needs a Bitcoin Core node (see spscand's README). The tab updates as soon as spscand reports something, and every 15 seconds anyway.

- **Spend…** sends selected payments to an address or to another silent payment address, with change to this wallet. Spending unconfirmed payments is off unless enabled in Settings.
- **New labelled address…** hands out an address with a label number and a note to yourself, so you can tell payments apart.
- **Rescan…** has spscand scan again from a block.

Received payments appear in the Silent Payments tab, not in Electrum's History: Electrum gives plugins no way to add them there.

## QML GUI

The QML GUI is the interface of Electrum for Android. On desktop it runs with `electrum -g qml`. Scan, paste, or open a Silent Payment address or BIP21 `sp` link as usual. The plugin asks for the amount, then shows Electrum's normal fee confirmation, and signs after Electrum's usual payment authentication.

The QML GUI cannot install or authorize external plugins. On desktop, add and enable the plugin in the normal Qt GUI first; the QML GUI then loads it. Electrum for Android does not allow external plugins at all yet, so phone support depends on Electrum adding that.

## Community and support

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.
- Use [GitHub Discussions](https://github.com/ZenulAbidin/electrum-silent-payments-sender/discussions) for usage questions and [GitHub Issues](https://github.com/ZenulAbidin/electrum-silent-payments-sender/issues) for reproducible non-security bugs.
- Read [SECURITY.md](SECURITY.md) and use [private vulnerability reporting](https://github.com/ZenulAbidin/electrum-silent-payments-sender/security/advisories/new) for security-sensitive reports. Never post wallet seeds, private keys, or other secrets.
- Feel free to reach out to me on [X/Twitter](https://x.com/Zenul_Abidin) and follow if you like my work.

## License and attribution

See `LICENSE` and `NOTICE`.

The BIP352 derivation is adapted from Electrum PR [#9900](https://github.com/spesmilo/electrum/pull/9900) by MorenoProg and Electrum contributors.

The packaged plugin includes the SilentPayments.xyz SVG mark as its Electrum plugin-manager icon.
