# Contributing

Thanks for taking a look. A few things keep this project healthy.

## Boundary: what must never enter this repository

This project stays lawful by containing only original code and publicly
observable behaviour. Anything that changes that is not a style issue to review
— it is a change of what the project *is*, and it will be removed.

**Do not submit:**

- vendor firmware images, or any extract, patch, or diff of one;
- decompiled, disassembled, or reconstructed vendor code, including
  pseudo-code, decompiler output, and symbol or string tables lifted from a
  vendor artifact;
- key material of any kind extracted from vendor software, firmware, or a
  device — whether a signing key, a wrapped key, a certification anchor, or a
  shared secret;
- reverse-engineering notes, address tables, or analysis artifacts;
- device credentials, pairing material, private keys, account tokens, or real
  device identifiers, including in test fixtures;
- anything that disables, patches, or skips a device's authentication,
  authorisation, or integrity checks.

If a change only works because it embeds something taken from a vendor build,
it does not belong here. Describe the observable behaviour instead and let the
code implement against that.

**Also out of scope by design:** flashing, erasing, formatting, pairing flows
that bypass the official handshake, and key-installation helpers. The SDK does
not ship these and will not start.

## Please do submit

## Please do submit

- Bug fixes, especially around container parsing and timing.
- New `Transport` implementations for other platforms.
- New `Storage` backends implementing the same protocol.
- Documentation improvements, especially clearer wording in `docs/`.
- Tests that run **offline** with synthetic data.

## Ground rules

1. **Every claim about device behaviour needs evidence.** If you say something
   is supported, say how you know. "It worked once" is not enough for a
   capability table entry.
2. **Capability entries are contracts.** If you move something from
   `unavailable` to `supported`, show the reproducible path that made it work.
3. **No silent fallbacks.** If an operation fails, return a structured error.
   Do not return an empty result that looks like success.
4. **Keep secrets out of logs.** Tool arguments may name key paths; do not log
   them.

## Running the tests

```bash
python3 xinliqiu-sdk/tests/test_sdk.py
cd xinliqiu-sdk/ts && npm install && npx tsc -p tsconfig.json --noEmit
node --check xinliqiu-sdk/cloudflare/src/index.js
```

The test suite makes no network calls and touches no hardware.

## Commit messages

Plain and specific: what changed and why. Reference the capability or module
when relevant.
