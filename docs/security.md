# Security model

## What the SDK touches

| Item | Where it lives | Does it leave your machine? |
|---|---|---|
| KSC key material | your `key_file` | no |
| Peer id | your `peer_id_file` | no |
| Owner private key | your path / env var | no |
| Device audio | transient, encrypted | only if you push it to a store |
| Plaintext audio | memory only | only if you choose to return it |

The SDK never generates credentials, never writes them anywhere, and never
sends them to a storage backend. The Cloudflare Worker has no endpoint that
would accept them.

## Ciphertext-only storage

Every storage backend in `aipin_sdk.storage` stores the original `enpt`
payload. Nothing decrypts on the way in.

Consequence: if your storage bucket leaks, the attacker obtains encrypted
Opus packets whose AES keys are wrapped with your RSA public key. Without your
private key they are useless.

## Decryption is explicit

`decrypt_enpt()` requires a private key argument. The MCP server never calls it
implicitly:

- `xinliqiu_sync` fetches and stores ciphertext;
- `xinliqiu_fetch_audio` is the only tool producing plaintext and it requires
  `owner_key_path` or `XINLIQIU_OWNER_KEY`.

## Agent boundaries

An agent driving this SDK can:

1. read status and aggregate counters;
2. start and stop recording;
3. capture voiceprints;
4. sync encrypted ranges;
5. decrypt audio **only** if the operator configured an owner key.

It cannot: pair, re-initialise, flash, format, or change device keys. Those
operations do not exist in the SDK at all.

## What is deliberately not implemented

| Operation | Status |
|---|---|
| Pairing | not provided |
| INIT / key installation | not provided |
| Firmware flash / OTA | not provided |
| Format / erase | not provided |

If you need any of these, stop and reconsider: they are outside the SDK's
intended scope and none are required for normal use.

## Recommendations

- Keep `key_file`, `peer_id_file` and the owner key outside your repository.
- Use `0600` permissions.
- Give the Cloudflare store a token you can rotate; it grants only ciphertext reads/writes.
- Do not log tool arguments from `xinliqiu_fetch_audio`; they name key paths.
