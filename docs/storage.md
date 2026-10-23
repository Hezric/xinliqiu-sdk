# Self-hosted storage

The `cloudflare/` directory deploys a ciphertext-only object store to your own
Cloudflare account.

## Why Cloudflare

Because the whole store is ~300 lines and runs on free-tier sized resources:
R2 for bytes, D1 for metadata, one Worker for the API. No server to patch.

## Deploy

```bash
cd cloudflare
npm install
./setup.sh        # creates R2 bucket + D1 database, applies schema, mints token
npm run deploy
```

`setup.sh` prints a bearer token. Give it to the client as
`XINLIQIU_STORE_TOKEN`.

## API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/objects` | Store one ciphertext object |
| `GET` | `/objects?device=&start_ms=&end_ms=&kind=` | List metadata in a range |
| `GET` | `/objects/<key>` | Fetch ciphertext bytes |
| `GET` | `/inventory?device=` | Aggregate counts and bytes |
| `GET` | `/healthz` | Liveness (no auth) |

All endpoints except `/healthz` require `Authorization: Bearer <token>`.

`POST /objects` headers:

```
x-xinliqiu-device     device identifier
x-xinliqiu-name       original entry name
x-xinliqiu-kind       audio | end-marker | note-audio | trace-point | unknown
x-xinliqiu-sha256     optional; verified server-side when present
x-xinliqiu-start-ms   optional device start time
x-xinliqiu-end-ms     optional device end time
```

The Worker computes the SHA-256 itself and rejects mismatches with the
declared header. Objects are keyed by `<device>/<sha256>`, so duplicates are
free to retry.

## What is stored

| Location | Content |
|---|---|
| R2 | encrypted `enpt` / CMPT entry bytes |
| D1 | name, kind, size, sha256, device time range, storage timestamp |

No plaintext. No keys. No device credentials.

## Limits

- 2 MiB per object. Ordinary recordings roll over at 120 KB, so this leaves
  ample headroom while capping abuse.
- `limit` on the list endpoint caps at 1000 rows per call.

## Alternatives

`LocalStorage` implements the same `Storage` protocol on the filesystem, which
is handy for tests and for single-machine setups.
