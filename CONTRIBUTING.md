# Contributing

Thanks for taking a look. A few things keep this project healthy.

## Please do not submit

- **Vendor material.** No firmware, no decompiled vendor code, no disassembly,
  no extracted strings or symbol tables. PRs containing these will be closed.
- **Credentials.** No device keys, pairing material, private keys, account
  tokens, or real device serial numbers — including in test fixtures.
- **Firmware or state-changing operations.** No flashing, erasing, formatting,
  pairing, or key installation. These are out of scope by design.

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
