# Security Policy

Silent Payments Sender handles wallet private keys and constructs Bitcoin
transactions. Treat suspected vulnerabilities as sensitive even when no funds
have been lost.

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |
| Earlier unpublished builds | No |

Only the latest published 1.0.x release receives security fixes.

## Report a vulnerability privately

Use GitHub's
[private vulnerability-reporting form](https://github.com/ZenulAbidin/electrum-silent-payments-sender/security/advisories/new).
Do not open a public issue, discussion, or pull request for a suspected
vulnerability.

Include, when safe:

- the affected version and Electrum version;
- the wallet and input type, using only synthetic or testnet material;
- clear reproduction steps or a minimal proof of concept;
- the expected and observed behavior;
- your assessment of impact and any suggested mitigation.

Never send a real seed phrase, private key, receiver secret, wallet file,
password, or identifying details about funds. Reports that require secret
material should instead describe how the maintainer can reproduce the condition
with a newly generated test wallet.

The maintainer aims to acknowledge reports within seven days and will coordinate
validation, remediation, and disclosure through a private GitHub security
advisory. These are best-effort targets, not a service-level guarantee.

## Security-relevant scope

Reports are especially useful when they affect:

- BIP352 address validation or output derivation;
- selected-input ownership, eligibility, or integrity checks;
- private-key lifetime or unintended disclosure;
- transaction mutation between derivation and signing;
- recovery transaction construction or signing;
- a bypass of a documented unsupported-wallet or unsupported-transaction
  rejection.

The documented absence of receiving/scanning support, hardware-wallet support,
Taproot inputs, batching, or RBF is not itself a vulnerability.

## Coordinated disclosure

Please allow time to reproduce and fix a confirmed issue before public
disclosure. Credit will be offered in the advisory unless the reporter prefers
to remain anonymous.
