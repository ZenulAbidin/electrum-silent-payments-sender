## Building and testing

From this repository:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
python3 scripts/check_release.py dist/silent_payments_sender-1.1.0.zip
```

The unit tests are self-contained and run the actual plugin core against the complete upstream BIP352 1.1.2 vector corpus, executing every sender case within the plugin's documented scope with a small pure-Python secp256k1 test shim. Every other official vector must be listed with the reason it is out of scope. The release checker also compiles every Python file, verifies the ZIP layout and manifest, checks that declared QML support ships its files, and rejects a `min_electrum_version` below 4.8.3. See `docs/conformance.md` for the requirement matrix and explicit limits.

### Offline Electrum integration

The CLI integration harness creates a real `Standard_Wallet`, injects a synthetic offline funding transaction, uses Electrum's coin selection and transaction classes, runs the plugin's actual finalize/seal path, reproduces the fee dialog's RBF mutation, independently derives the receiver output key, signs through `wallet.sign_transaction`, and cryptographically verifies the input signatures before round-tripping the final raw transaction. It repeats the flow for P2PKH, nested SegWit, and native SegWit wallets. It never connects to the network.

Run it, the recovery harness, and the packaged Send-tab smoke test in a Python environment where Electrum 4.8.3 or newer is importable (an extracted official AppImage, or the Electrum source tree on `PYTHONPATH`):

```bash
python3 scripts/electrum_cli_harness.py --network testnet \
  --output test-results/electrum-VERSION-testnet-cli-integration.json
python3 scripts/electrum_cli_harness.py --network mainnet \
  --output test-results/electrum-VERSION-mainnet-cli-integration.json
python3 scripts/electrum_recovery_harness.py \
  --output test-results/electrum-VERSION-mainnet-recovery-integration.json
python3 scripts/electrum_sendtab_smoke.py --plugin dist/silent_payments_sender-1.1.0.zip
```

### Regtest GUI harness

`scripts/electrum_regtest_harness.py` drives Electrum's real Qt or QML GUI (offscreen) through a broadcast Silent Payment. It installs and authorizes the release ZIP as an external plugin, opens a funded regtest wallet, enters a `tsp1` address or BIP21 `sp` URI, steps through the plugin's and Electrum's own confirmation and password dialogs, and broadcasts. It then checks the transaction with the independent test receiver, that no RBF is signalled, that the payment record and label were stored, and that the transaction confirms. For QML it also checks that unsigned export is refused and that a fee change keeps the transaction valid.

It needs a regtest `bitcoind` with a funded wallet named `miner`, an Electrum server, and PyQt6 with QtQuick:

```bash
PYTHONPATH=/path/to/electrum python3 scripts/electrum_regtest_harness.py \
  --gui qml --zip dist/silent_payments_sender-1.1.0.zip \
  --workdir /path/to/scratch --server 127.0.0.1:60401:t \
  --bitcoin-cli bitcoin-cli --bitcoin-cli-arg=-regtest \
  --script-type p2wpkh --output result.json
```

Use `--gui qt` for the desktop GUI, `--script-type p2wpkh-p2sh` or `p2pkh` for the other input types, and `--amount-in-uri` to pay a BIP21 URI that carries the amount. The plugin-password key file normally has to be root-owned; the harness points Electrum at a key in the work directory, and everything else is Electrum's normal plugin path. The QML run hands the address to Electrum the way an opened payment link does; the Send dialog's paste and scan paths end in the same resolver, but its camera scanner needs a real device.

Electrum 4.8 requires the `server.features` call. mempool's electrs does not implement it, so put a shim in front of it that answers that call, or use a server that does.

Published results cover Electrum 4.8.3 and 4.8.4: the offline mainnet and testnet lifecycle and recovery files in `test-results/`, and the regtest GUI matrix in `test-results/regtest/`.

Electrum 4.8.3 is the minimum supported version because it is the first release that fixes CVE-2026-117601 (GHSA-6jp4-8v48-cc5v), where a malicious server replaces a just-broadcast transaction and tries to get the user to fee-bump the fake one.
