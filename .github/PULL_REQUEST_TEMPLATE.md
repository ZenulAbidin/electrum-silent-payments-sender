## Summary

Describe the change and the user or developer impact.

## Motivation

Explain the problem, linked issue, or protocol requirement.

## Security and privacy impact

Describe any effect on key access, input selection, output derivation,
transaction integrity, signing, recovery, logging, or supported wallet scope.
Write "None" only after considering each area.

## Validation

- [ ] `python3 -m unittest discover -s tests -v`
- [ ] `python3 scripts/build_release.py`
- [ ] `python3 scripts/check_release.py dist/silent_payments_sender-1.0.0.zip`
- [ ] Packaged Send-tab smoke test in a supported Electrum runtime
- [ ] Relevant Electrum integration harnesses, or an explanation of why they
      are not required

## Checklist

- [ ] The change is focused and follows `CONTRIBUTING.md`.
- [ ] Behavior changes include tests.
- [ ] User-visible or conformance changes include documentation.
- [ ] Test data contains no real wallet secrets, funds, or identifying data.
- [ ] Release output remains deterministic.
