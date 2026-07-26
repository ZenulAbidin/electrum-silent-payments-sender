# Governance

Silent Payments Sender for Electrum is currently a maintainer-led open source
project.

## Maintainer

Ali Sherief ([@ZenulAbidin](https://github.com/ZenulAbidin)) is the project
maintainer and release manager.

## Decision making

Discussion is welcome in issues, pull requests, and GitHub Discussions. The
maintainer seeks rough consensus, with priority given to:

1. protecting wallet keys and preventing incorrect transactions;
2. conformance with BIP352 and supported Electrum behavior;
3. preserving the project's explicit, narrow scope;
4. deterministic tests and reproducible evidence;
5. clarity for users and reviewers.

The maintainer makes the final decision when consensus is not reached and may
decline changes that expand risk or cannot be validated across supported
Electrum versions.

## Contributions and releases

All changes are reviewed through pull requests except urgent maintainer
security fixes. User-visible behavior changes require tests and documentation.
Releases are tagged, accompanied by checksums, and published through GitHub
Releases.

Security reports follow [SECURITY.md](SECURITY.md) and may be developed
privately through GitHub security advisories until coordinated disclosure.

## Changes to governance

Material governance changes will be proposed publicly. Additional maintainers
may be added after sustained, trusted contributions and demonstrated care with
security-sensitive review.
