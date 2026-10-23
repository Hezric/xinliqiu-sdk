# Security policy

## Reporting a vulnerability

Open a private security advisory on the repository, or email the maintainer
listed in the repository profile. Please do not open a public issue for
security problems.

Expect an acknowledgement within a few days. There is no bug bounty.

## Scope

In scope:

- credential or key leakage through logs, errors, or the MCP surface;
- plaintext audio being written where ciphertext was expected;
- authentication or authorisation flaws in the Cloudflare Worker;
- path traversal or object-key confusion in storage backends.

Out of scope:

- vulnerabilities in the device firmware itself;
- vulnerabilities in OPPO's apps or services;
- attacks requiring physical access to the device;
- the fact that a stolen private key can decrypt past recordings — that is
  inherent to the design, not a vulnerability here.

## Design guarantees this project tries to hold

| Guarantee | Where enforced |
|---|---|
| Storage backends only receive ciphertext | `storage.py`, `cloudflare/` |
| Device credentials are never written by the SDK | `client.py` reads paths, never persists |
| Decryption requires an explicit key | `decrypt_enpt`, `xinliqiu_fetch_audio` |
| The MCP server returns metadata by default | `mcp_tools.py` |
| The Worker verifies content hashes | `cloudflare/src/index.js` |

If you find a path that breaks any of these, that is a security issue.
