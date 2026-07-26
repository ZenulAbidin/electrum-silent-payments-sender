[ANN] Silent Payments Sender 1.0.0 — BIP352 plugin for Electrum

I am releasing an experimental external plugin that lets Electrum Qt desktop
users send to BIP352 Silent Payment addresses.

It integrates with Electrum's normal Send tab:

    Paste sp1... or tsp1... into Pay to -> enter Amount -> Pay...

The New Transaction preview uses a compact abbreviated recipient/output label.
Final transaction details wrap the complete reusable Silent Payment address
and complete derived Taproot address into aligned output-column lines. The
association is restored after restarting Electrum, while History keeps the
user's Description concise.

No SeedSigner or other hardware is required. The sender uses a supported
standard Electrum software wallet. The plugin waits for Electrum to select the
transaction inputs, derives the BIP352 Taproot output from those exact inputs,
then signs immediately using Electrum's normal signer.

Version 1.0.0 scope:

- Electrum 4.6.0+
- sender only; no scanning/receiving
- mainnet, testnet, signet, and regtest
- standard single-signature deterministic software wallets
- P2PKH, P2WPKH-P2SH, and P2WPKH inputs
- one recipient/output per transaction
- non-RBF
- raw addresses and BIP21 `sp` payment instructions
- BIP352 1.1.1, including forward-compatible v1-v30 address parsing

Not supported:

- hardware, watch-only, imported-key, multisig, or 2FA wallets
- Taproot inputs, batching, payjoin, or swaps
- unsigned preview/export or later signing

This is independent software, not an official Electrum feature, and it has not
received a professional security audit. Use a small amount for the first
mainnet payment.

Files:

- silent_payments_sender-1.0.0.zip — installable Electrum plugin
- electrum-silent-payments-sender-1.0.0-source.zip — source, tests, and build tools
- SHA256SUMS — release hashes

Install:

1. Verify the ZIP hash.
2. Electrum -> Tools -> Plugins -> Add plugin.
3. Select silent_payments_sender-1.0.0.zip and enable it.
4. Use Electrum's normal Send tab.

License: MIT.
Author: Ali Sherief (Zenul_Abidin).

The BIP352 derivation is adapted from Electrum PR #9900 by MorenoProg and
Electrum contributors. The release includes the complete official BIP352 1.1.1
vector corpus, automated conformance tests for every sender case within the
plugin's scope, strict Bech32m/address validation, wallet/input restrictions,
an Electrum plugin-manager logo, unified Send-tab handling, and a pre-sign
transaction-integrity check. Headless integration harnesses under
the official Electrum 4.6.0, 4.7.2, and 4.8.0 AppImage runtimes also create and
fund real Electrum Standard_Wallet instances offline under both mainnet and
testnet rules, run the complete plugin flow, sign through Electrum,
independently verify receiver spendability, and
cryptographically verifies the input signatures before round-tripping the final
raw transaction. The harness repeats this for P2PKH, P2WPKH-P2SH, and P2WPKH
wallets.

For environments where testnet is unusable, the source archive includes a
self-contained receiver/recovery utility. Mainnet receiver creation requires an
explicit risk flag, receiver files cannot be overwritten, and backup validation
reproduces the exact `sp1...` address before payment. After the sender
transaction is signed, the utility can scan it and create a signed BIP341
key-path recovery transaction to a normal `bc1...` wallet address. Electrum's
own BIP341 implementation verifies the recovery signature under 4.6.0, 4.7.2,
and 4.8.0. This is a diagnostic/recovery tool; the Electrum plugin remains
sender-only and does not monitor incoming payments.

Receiver files and `sp1...`/`tsp1...` addresses made by earlier unpublished
1.0.0 builds remain compatible; the release includes a fixed legacy receiver
file regression test.

The source archive includes mainnet and testnet JSON results for Electrum 4.6.0,
4.7.2, and 4.8.0.

Source/release link: [ADD YOUR LINK]

SHA-256:

    [PASTE SHA256SUMS HERE]
