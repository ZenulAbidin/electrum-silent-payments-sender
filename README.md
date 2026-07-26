# Silent Payments Sender for Electrum

[![Tests](https://github.com/ZenulAbidin/electrum-silent-payments-sender/actions/workflows/tests.yml/badge.svg)](https://github.com/ZenulAbidin/electrum-silent-payments-sender/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/v/release/ZenulAbidin/electrum-silent-payments-sender)](https://github.com/ZenulAbidin/electrum-silent-payments-sender/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A sender implementation of [BIP352 Silent Payments](https://bips.dev/352/)
for Electrum's Qt desktop wallet.

It integrates directly into Electrum's existing **Send** tab. Paste a Silent
Payment address into **Pay to**, enter the amount, and press **Pay…** as usual.
The plugin detects the address and builds a normal Bitcoin transaction whose
recipient output is derived from the wallet's selected inputs. No SeedSigner or
other physical device is required.

## Safety status

This is an independent plugin, not an official Electrum feature, and has not
received a professional security audit. Review the source, verify the release
hash, test on testnet, and use a small amount for any first mainnet payment.

Version 1.0.0 targets BIP352 1.1.1 and intentionally supports only:

- Electrum 4.6.0 or newer, Qt desktop GUI
- standard, single-signature deterministic software wallets
- compressed-key P2PKH, nested SegWit, and native SegWit inputs
- one BIP352 recipient and one payment output per transaction
- `sp1...` on mainnet and `tsp1...` on testnet, signet, or regtest
- raw addresses and BIP21 `sp` payment instructions
- forward-compatible Silent Payment address versions 1 through 30

Not supported yet:

- hardware, watch-only, imported-key, multisig, and 2FA wallets
- Taproot inputs, transaction batching, payjoin, and swaps
- unsigned preview/export and later signing
- multiple recipients or multiple silent outputs

The transaction is made non-RBF, although with the mempoolfullrbf protocol flag,
it can signal RBF support anyway. The plugin derives the final output only after
Electrum selects the inputs, verifies the inputs and output again after the fee
confirmation dialog, and then signs immediately. These restrictions keep the
input-dependent BIP352 derivation bound to the transaction that is signed.

## Install

1. Download the `silent_payments_sender-*.zip` installable asset from the
   [latest release](https://github.com/ZenulAbidin/electrum-silent-payments-sender/releases/latest)
   and verify its SHA-256 against the release's `SHA256SUMS-*` asset.
2. In Electrum, open **Tools → Plugins**.
3. Click **Add plugin**, select the ZIP, review Electrum's external-plugin
   warning, and enable **Silent Payments Sender**.
4. Restart/reload the wallet if Electrum asks, then open the normal **Send** tab.

Electrum external plugins are executable Python code. Install only a ZIP whose
source and hash you trust.

## Use

Paste a BIP352 address or a BIP21 URI containing the `sp` parameter into the
normal **Pay to** field, enter the amount in Electrum's normal **Amount** field,
and press **Pay…**. The existing Description field is retained. If the URI
contains an amount, the plugin fills the amount when the field is empty. The fee
dialog may calculate the transaction more than once; the plugin re-derives the
silent output from the final selected inputs each time.

In Electrum's **New Transaction** send preview, the payment output uses the
compact single-line form
`first-8…last-8 (bc1p-first-8…last-8)`. In final transaction details, the
Outputs area wraps the complete reusable `sp1...`/`tsp1...` recipient and the
complete one-time derived `bc1p...`/`tb1p...` output into aligned
42-character lines. The output's right-click **Copy Address** action continues
to copy the derived on-chain address.

After signing, the plugin stores the two public addresses in wallet-local
plugin metadata keyed by transaction ID. This lets it reconstruct the
multiline output after restarting Electrum without putting either address in
the Description. The normal History description remains the user's text, or
`Silent Payment` when no description was entered.

Coin control is supported through Electrum's normal Coins tab selection. If an
extremely unlikely invalid scalar is encountered, select a different set of
coins and retry.

## End-to-end receiver and recovery test

The source archive includes a self-contained Python receiver utility, so you do
not need to already own a Silent Payments receiver to test the sender. Testnet
is the default. Mainnet receiver generation requires an explicit risk flag and
should be used only with a small amount.

On Windows, macOS, or Linux, unpack the source archive and run:

```bash
python scripts/silent_payment_test_receiver.py generate --output silent-payment-test-receiver.json
```

For a mainnet test, generate an `sp1...` address with:

```bash
python scripts/silent_payment_test_receiver.py generate --network mainnet --i-understand-mainnet-risk --output silent-payment-mainnet-receiver.json
```

The JSON file contains the receiver's private scan and spend secrets. Anyone
with it can derive and spend every payment sent to that Silent Payment address.
Generation refuses to overwrite an existing receiver file.

Receiver JSON files and `sp1...`/`tsp1...` addresses generated by earlier
unpublished 1.0.0 builds remain compatible. Their format, private keys, address
encoding, and derivation have not changed; a regression test loads the earlier
file format and reproduces the exact address.

Before sending anything, make at least two offline copies and verify each copy:

```bash
python scripts/silent_payment_test_receiver.py verify-backup --receiver silent-payment-mainnet-receiver.json
```

Both copies must report `"status": "valid"` and reproduce the exact same
`sp1...` address. Do not proceed if they do not.

1. For a testnet test, start Electrum with `--testnet` and use only testnet
   coins. For the explicitly enabled mainnet flow, start normal Electrum and
   use only a small amount.
2. Paste the generated `tsp1...` or `sp1...` address into **Pay to**.
3. Enter an amount, press **Pay…**, sign, and either broadcast or save the
   complete signed raw transaction.
4. In Electrum's transaction dialog, use **Copy → Raw transaction**, save the
   hex in `receiving-transaction.txt`, then run:

```bash
python scripts/silent_payment_test_receiver.py scan --receiver silent-payment-mainnet-receiver.json --raw-transaction-file receiving-transaction.txt
```

`"status": "match"` proves that the receiver's scan key finds the derived
output and reports its amount and output index. Private output keys are not
printed.

To reclaim the matched output, copy a normal native-SegWit `bc1...` receiving
address from your regular wallet and choose an explicit fee in satoshis:

```bash
python scripts/silent_payment_test_receiver.py recover --receiver silent-payment-mainnet-receiver.json --receiving-transaction-file receiving-transaction.txt --destination bc1qYOUR_NORMAL_WALLET_ADDRESS --fee-sat 500 --output signed-recovery-transaction.txt
```

Check `input_amount_sat`, `fee_sat`, `output_amount_sat`, and `destination` in
the result. Load `signed-recovery-transaction.txt` into Electrum using
**Tools → Load transaction → From text**, inspect it again, and broadcast it.
The recovery transaction is RBF-enabled.

Keep the receiver JSON even after recovery. The receiving transaction is public
once broadcast and can be retrieved again by txid, but the two private secrets
in the JSON cannot be reconstructed if every backup is lost.

Electrum's ordinary private-key import/sweep dialog cannot recover this output:
it does not support a raw Silent Payment Taproot output key. The dedicated
recovery command signs the direct BIP341 key-path spend. Its signatures are
independently checked against Electrum's BIP341 implementation by
`scripts/electrum_recovery_harness.py` under Electrum 4.6.0, 4.7.2, and 4.8.0.

## Build and test

From this repository:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/electrum_cli_harness.py
python3 scripts/build_release.py
python3 scripts/check_release.py dist/silent_payments_sender-1.0.0.zip
```

The tests are self-contained and run the actual plugin core against the complete
upstream BIP352 1.1.1 vector corpus, executing every sender case within the
plugin's documented scope with a small pure-Python secp256k1 test shim. The
release checker also compiles every Python file and verifies the ZIP layout and
manifest. See `CONFORMANCE.md` for the requirement matrix and explicit limits.

The CLI integration test suite is separate from the self-contained unit tests. It
has been run under the official Electrum 4.6.0, 4.7.2, and 4.8.0 AppImage Python
environments on both Bitcoin mainnet and testnet rules. It creates a real
`Standard_Wallet`, injects a synthetic offline funding transaction, uses
Electrum's coin selection and transaction classes, runs the plugin's actual
finalize/seal path, reproduces the fee dialog's RBF mutation, independently
derives the receiver output key, signs through `wallet.sign_transaction`, and
cryptographically verifies the input signatures before round-tripping the final
raw transaction. It repeats the flow for P2PKH, nested SegWit, and native
SegWit wallets. It never connects to the network.

With a supported official Electrum AppImage extracted using
`--appimage-extract`, run:

```bash
APPROOT=/path/to/squashfs-root
LD_LIBRARY_PATH="$APPROOT/usr/lib:$APPROOT/usr/lib/x86_64-linux-gnu" \
  "$APPROOT/usr/bin/python3" -s scripts/electrum_cli_harness.py \
  --network testnet \
  --output test-results/electrum-VERSION-testnet-cli-integration.json
```

Run it again with `--network mainnet` to test mainnet address encoding and
transaction rules. The harness is fully offline: its funding transactions,
seeds, and coins are synthetic, and nothing is broadcast.

The published source archive includes mainnet and testnet results for Electrum
4.6.0, 4.7.2, and 4.8.0.

Electrum 4.6.0 is the minimum supported version. Its desktop binaries were the
first to officially support installing third-party ZIP plugins, and its complete
testnet transaction lifecycle passes this test suite. Electrum 4.5.8 is not
compatible because it does not provide the separate `electrum_ecc` package.

`BTT_POST.md` is a ready-to-edit Bitcointalk announcement template.
`SILENTPAYMENTS_XYZ_SUBMISSION.md` contains the website-listing request to use
once the source and release have public URLs.

## Community and support

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.
- Use [GitHub Discussions](https://github.com/ZenulAbidin/electrum-silent-payments-sender/discussions)
  for usage questions and [GitHub Issues](https://github.com/ZenulAbidin/electrum-silent-payments-sender/issues)
  for reproducible non-security bugs.
- Read [SECURITY.md](SECURITY.md) and use
  [private vulnerability reporting](https://github.com/ZenulAbidin/electrum-silent-payments-sender/security/advisories/new)
  for security-sensitive reports. Never post wallet seeds, private keys, or
  other secrets.
- Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Design notes

Electrum's normal recipient parser does not currently understand BIP352
addresses. The plugin recognizes them at the Send-tab boundary and routes only
those payments through its BIP352 transaction builder; ordinary Bitcoin and
Lightning recipients continue through Electrum unchanged. It uses a 34-byte
P2TR placeholder during fee/coin selection. After inputs are known, it replaces
that placeholder with the BIP352-derived P2TR script of exactly the same
serialized size.

The plugin never sends private keys anywhere. It derives each selected input's
private key inside Electrum, uses the sum only to derive the recipient output,
and lets the wallet's normal signer sign the transaction.

## License and attribution

MIT. Author: Ali Sherief (Zenul_Abidin). See `LICENSE` and `NOTICE`. The BIP352
derivation is adapted from Electrum PR #9900 by MorenoProg and Electrum
contributors. The packaged plugin includes the SilentPayments.xyz SVG mark as
its Electrum plugin-manager icon.
