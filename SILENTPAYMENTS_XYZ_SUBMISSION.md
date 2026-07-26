# SilentPayments.xyz wallet-listing request

Use this after the public source/release URL is available.

## Suggested issue

Title:

`Add Silent Payments Sender for Electrum to experimental wallet support`

Body:

```text
Please add Silent Payments Sender for Electrum to the
"Experimental & proof-of-concept" section of the wallet-support page.

Project: Silent Payments Sender for Electrum
Author: Ali Sherief (Zenul_Abidin)
Project URL: [PUBLIC PROJECT/RELEASE URL]
Source URL: [PUBLIC SOURCE REPOSITORY URL]
Platform: Electrum 4.6.0+ Qt desktop
Support: Sending = yes; Receiving = no; Privacy-preserving scanning = n/a;
BIP375 = no; BIP376 = no.

It is an open-source MIT-licensed external Electrum plugin implementing
sender-only BIP352. Version 1.0.0 targets BIP352 1.1.1, supports raw Silent
Payment addresses and BIP21 `sp` payment instructions directly in Electrum's
normal Send tab, and supports standard software-wallet P2PKH, P2SH-P2WPKH, and
P2WPKH inputs.

Conformance evidence includes the upstream BIP352 1.1.1 vectors and an offline
Electrum 4.6.0, 4.7.2, and 4.8.0 mainnet and testnet integration harnesses that
construct, sign, verify, serialize, and reparse transactions for all three
supported wallet input types without connecting or broadcasting.

The project is explicitly marked experimental and recommends testnet and small
amounts.
```

Submit at:
https://github.com/sethforprivacy/silentpaymentsxyz/issues/new
