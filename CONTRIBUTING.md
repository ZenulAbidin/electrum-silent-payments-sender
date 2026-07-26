# Contributing

Thank you for considering a contribution to Silent Payments Sender for
Electrum. This project handles wallet keys and transaction construction, so
correctness, narrow scope, and reviewability take priority over feature speed.

## Before opening an issue

- Use GitHub Discussions for setup questions and general support.
- Search existing issues and discussions before opening a new one.
- Do not report vulnerabilities in a public issue. Follow
  [SECURITY.md](SECURITY.md) and use GitHub's private vulnerability-reporting
  form.
- Never include real wallet seeds, private keys, receiver secrets, passwords,
  signed transactions containing sensitive metadata, or details that identify
  funds you control.

## Supported contribution scope

Version 1.0.x intentionally supports one BIP352 recipient and one payment
output from a standard, single-signature deterministic Electrum software
wallet. See [README.md](README.md) and [CONFORMANCE.md](CONFORMANCE.md) for the
complete supported and rejected cases.

Proposals that expand wallet types, input types, recipient count, RBF, hardware
signing, or receiving/scanning need a written safety model and protocol
analysis before implementation. Open a feature request first so the design can
be discussed.

## Development setup

The unit tests are self-contained and use only Python's standard library:

```bash
python3 -m unittest discover -s tests -v
```

Build and validate the deterministic Electrum plugin archive:

```bash
python3 scripts/build_release.py
python3 scripts/check_release.py dist/silent_payments_sender-1.0.0.zip
python3 scripts/electrum_sendtab_smoke.py \
  --plugin dist/silent_payments_sender-1.0.0.zip
```

The Electrum integration and recovery harnesses require an extracted official
Electrum AppImage runtime. Their offline usage is documented in
[README.md](README.md). Never run the harnesses with a real wallet, seed, or
funded transaction.

## Pull requests

1. Fork the repository and create a focused branch.
2. Keep each pull request limited to one coherent change.
3. Add or update tests for every behavior change and regression fix.
4. Update the README, conformance matrix, or changelog when user-visible scope
   or protocol behavior changes.
5. Run the unit, release-checker, and packaged Send-tab smoke tests.
6. Complete the pull request template, including its security-impact section.

Prefer small, explicit changes over broad refactors. Preserve deterministic
release behavior and compatibility with the minimum documented Electrum
version. Test fixtures must contain only clearly identified synthetic secrets
and funds.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE).

## Community conduct

All project participation is subject to the
[Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
