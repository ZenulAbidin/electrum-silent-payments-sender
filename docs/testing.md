
## Building and testing

From this repository:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/electrum_cli_harness.py
python3 scripts/build_release.py
python3 scripts/check_release.py dist/silent_payments_sender-1.0.0.zip
```

The tests are self-contained and run the actual plugin core against the complete upstream BIP352 1.1.1 vector corpus, executing every sender case within the plugin's documented scope with a small pure-Python secp256k1 test shim. The release checker also compiles every Python file and verifies the ZIP layout and manifest. See `CONFORMANCE.md` for the requirement matrix and explicit limits.

The CLI integration test suite is separate from the self-contained unit tests. It has been run under the official Electrum 4.6.0, 4.7.2, and 4.8.0 AppImage Python environments on both Bitcoin mainnet and testnet rules. It creates a real `Standard_Wallet`, injects a synthetic offline funding transaction, uses Electrum's coin selection and transaction classes, runs the plugin's actual finalize/seal path, reproduces the fee dialog's RBF mutation, independently derives the receiver output key, signs through `wallet.sign_transaction`, and cryptographically verifies the input signatures before round-tripping the final raw transaction. It repeats the flow for P2PKH, nested SegWit, and native SegWit wallets. It never connects to the network.

With a supported official Electrum AppImage extracted using
`--appimage-extract`, run:

```bash
APPROOT=/path/to/squashfs-root
LD_LIBRARY_PATH="$APPROOT/usr/lib:$APPROOT/usr/lib/x86_64-linux-gnu" \
  "$APPROOT/usr/bin/python3" -s scripts/electrum_cli_harness.py \
  --network testnet \
  --output test-results/electrum-VERSION-testnet-cli-integration.json
```

Run it again with `--network mainnet` to test mainnet address encoding and transaction rules. The harness is fully offline: its funding transactions, seeds, and coins are synthetic, and nothing is broadcast.

The published source archive includes mainnet and testnet results for Electrum 4.6.0, 4.7.2, and 4.8.0.

Electrum 4.6.0 is the minimum supported version. Its desktop binaries were the first to officially support installing third-party ZIP plugins, and its complete testnet transaction lifecycle passes this test suite. Electrum 4.5.8 is not compatible because it does not provide the separate `electrum_ecc` package.
