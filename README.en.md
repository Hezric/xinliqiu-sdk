<div align="center">

# xinliqiu SDK

**Access an OPPO AI 心力球 from your own code.**

Record · retrieve · decrypt · store · serve to agents

[Quick start](#quick-start) · [What works](#what-works) · [MCP](#for-agents-mcp) · [Storage](#storage) · [中文](README.md) · [Legal](LEGAL.md)

</div>

---

### Copy this into your agent

> Help me take control of my **OPPO AI 心力球** (`CNMABWC301`) using the
> **xinliqiu SDK**.
>
> Project: https://github.com/kiyoakii/xinliqiu-sdk
>
> Please:
> 1. Clone the repository and read `AGENTS.md` and `AGENT-SETUP.md` in full.
> 2. Decide whether MCP, the Python library, TypeScript, or the CLI fits, and tell me why.
> 3. Follow `AGENT-SETUP.md` to pair and verify the device, showing real command output at each step.
> 4. Run `xinliqiu capabilities` and do not design around unavailable items.
>
> The only hard rule: **do not flash firmware, do not format the device, and do
> not run `init` twice** (it can discard recordings already on the device).
> If any step fails, stop and tell me where it failed. Do not retry in a loop.

---

<div align="center">

Read the docs it references: **[AGENTS.md](AGENTS.md)** (integration modes and
capability contract) and **[AGENT-SETUP.md](AGENT-SETUP.md)** (step-by-step
provisioning).

</div>

---

## What this is

The 心力球 is a small wearable that records audio. Its recordings are encrypted
with a key pair **the owner installs on the device** — which means the owner is
the only party who can read them back.

This SDK gives you that read-back path, plus everything around it:

```
   device ──BLE──► your code ──► your storage ──► your agents
                    │                │                 │
              Python / TS       Cloudflare        MCP server
```

Four surfaces, one implementation:

| Surface | Use it for | Jump to |
|---|---|---|
| **Python SDK** | applications, scripts, pipelines | [→](#python) |
| **MCP server** | letting an AI agent drive the device | [→](#for-agents-mcp) |
| **CLI** | doing it by hand in a terminal | [→](#cli) |
| **TypeScript** | Node/Bun apps | [→](#typescript) |

---

## What works

This table is not marketing. It is the same data the SDK hands to your code and
to agents. **Read the third section before you design anything.**

### ✅ Supported

| Capability | What you get |
|---|---|
| **Authenticate** | Reuse your existing credentials. No pairing, no re-initialisation. |
| **Read status** | Recording state, battery percentage, error byte. |
| **Inventory totals** | How many items and bytes are on the device in a time range. |
| **Fetch a range** | Containers for any device-clock window you ask for. |
| **Decrypt** | RSA unwrap + AES-256-GCM, with **your** private key. |

### ⚠️ Works, with limits

| Capability | The limit you need to know |
|---|---|
| **Start/stop recording** | Toggling recording does **not** guarantee an audio file. |
| **Audio creation** | A file appears **only when voice activity is detected**. Say nothing, get nothing. |
| **Segmentation** | Silence splits files. Ordinary files roll over at ~60 s of audio. |
| **Silence retention** | Trailing silence is kept for a bounded period, not indefinitely. |
| **Voiceprint capture** | One capture, 1–127 seconds. Not a continuous recorder. |
| **Opus → WAV** | Duration is packet-count based. The container carries no native trim. |

### ❌ Unavailable — do not build on these

| Capability | Why it does not work |
|---|---|
| **Mark highlight** (`标记重点`) | No reachable trigger exists: not over Bluetooth, not through the app, not from the physical key. |
| **Note archive** (`.note.opus`) | The manual archive path is not reachable over Bluetooth. |
| **AI-key conversation mode** | The key is a recording toggle. Releasing it never ends a recording, however long you hold. |

These are **established negatives**, not missing features. They were checked
from several directions and found unreachable. The SDK exposes them in the
capability table so your code and your agent can see the truth:

```python
>>> Xinliqiu.capabilities()["statuses"]["unavailable"]
[{'name': 'mark.add', 'note': 'Not reachable: no BLE producer, no app command, no physical-key trigger.'}, ...]
```

---

## Requirements

**No OPPO phone. No OPPO account. No root.**

| What you need | Notes |
|---|---|
| A xinliqiu | factory-fresh or reset to a pairable state |
| A computer with Bluetooth | macOS and Linux via the reference transport |
| The transports binary | `ble-probe` on `PATH`, or set `XINLIQIU_PROBE` |

A fresh device can be paired entirely from your computer. See
**[docs/getting-started.md](docs/getting-started.md)**, or hand the job to an agent
with **[AGENT-SETUP.md](AGENT-SETUP.md)**.

## Install

```bash
pip install "xinliqiu-sdk[crypto]"
```

---

## Quick start

### 1. Pair and check your setup

```bash
xinliqiu scan                                  # find the device, note its id
xinliqiu pair --device <id> --out-dir ./creds  # creates key.json + peer-id.txt
xinliqiu doctor --device <id> \
  --peer-id-file ./creds/peer-id.txt \
  --key-file ./creds/key.json                   # should print "ready": true
```

Pairing takes a couple of minutes and **does not** format, erase, or flash
anything. If credentials already exist, `pair` refuses to overwrite them.

### 2. Check what the device can do

```bash
xinliqiu capabilities
```

Do this first. Ten seconds now saves an afternoon later.

### 3. Talk to it

```python
from pathlib import Path
from aipin_sdk import Xinliqiu, Credentials

ball = Xinliqiu(Credentials(
    device_id="<your device id>",
    peer_id_file=Path("~/xinliqiu/peer-id.txt").expanduser(),
    key_file=Path("~/xinliqiu/key.json").expanduser(),
))

print(ball.status())            # dead simple: is it recording?
print(ball.inventory_totals())  # how much is on the device?
```

### 4. Pull recordings out

```python
from aipin_sdk import parse_cmpt_file, decrypt_enpt, to_wav

result = ball.sync_range(0, 1_800_000_000_000)

for path in sorted(Path(result["_output_dir"]).glob("*.bin")):
    for entry in parse_cmpt_file(path).by_kind("audio"):
        wav = to_wav(decrypt_enpt(entry.payload, Path("~/owner.pem").expanduser()))
        Path(f"{entry.name}.wav").write_bytes(wav)
```

That is the whole loop: identify → fetch → decrypt. Everything else in this
README is convenience.

---

<a id="python"></a>
## Python

### The object model

```python
ball.status()                         # RecordStatus(recording, battery, error)
ball.inventory_totals(start, end)     # {'count': int, 'bytes': int, ...}
ball.sync_range(start_ms, end_ms)     # fetch containers for a window
ball.collect_voiceprint(seconds=30)   # one bounded capture
ball.record_start() / ball.record_stop()
ball.capabilities()                   # ask before you build
```

### Containers

A synced range arrives as **CMPT containers**. Each entry is one of:

| Kind | Filename | Meaning |
|---|---|---|
| `audio` | `*.enc.opus` | encrypted Opus packets |
| `end-marker` | `*.end.enc.opus` | the recording ended here (40 bytes, not audio) |
| `trace-point` | `*.tp` | device diagnostics, JSON lines |

```python
container = parse_cmpt_file(path)
for entry in container.by_kind("audio"):
    ...
```

### Decryption

```python
plaintext = decrypt_enpt(entry.payload, Path("owner.pem"))
```

Wrong key → `DecryptError`. There is no silent fallback: GCM authentication
either passes or the call fails.

### Audio

Plaintext is **40-byte / 20 ms Opus packets**, mono, 16 kHz.

```python
from aipin_sdk import OpusLayout, to_ogg, to_wav

layout = OpusLayout()
layout.packet_count(plaintext)        # 943 packets
layout.duration_seconds(plaintext)    # 18.86 s

to_ogg(plaintext)                     # spec-compliant Ogg/Opus bytes
to_wav(plaintext)                     # 16-bit PCM WAV bytes
```

> **On duration:** the container has no native sample count. Any duration you
> calculate is a packet count, not the original microphone duration. Do not
> present it as the latter.

### Transports

Bluetooth is platform-specific, so it lives behind an interface:

```python
from aipin_sdk import Transport, Xinliqiu

class MyTransport(Transport):
    def run(self, argv, timeout):
        return [...]  # decoded JSON events from your own BLE stack

ball = Xinliqiu(credentials, transport=MyTransport())
```

No protocol code changes needed. The default `SubprocessTransport` shells out to
the reference binary.

---

<a id="cli"></a>
## CLI

```bash
xinliqiu capabilities              # what works
xinliqiu status                    # is it recording?
xinliqiu inventory                 # how much is stored
xinliqiu record start
xinliqiu record stop
xinliqiu voiceprint 30 --out ./captures
xinliqiu sync 0 1800000000000 --out ./captures
xinliqiu sync 0 1800000000000 --store cloudflare
```

Credentials come from flags or environment variables (`XINLIQIU_DEVICE_ID`,
`XINLIQIU_PEER_ID_FILE`, `XINLIQIU_KEY_FILE`).

---

<a id="for-agents-mcp"></a>
## For agents (MCP)

```bash
./mcp/run.sh      # speaks MCP over stdio
```

```json
{
  "mcpServers": {
    "xinliqiu": {
      "command": "/absolute/path/to/xinliqiu-sdk/mcp/run.sh",
      "env": {
        "XINLIQIU_DEVICE_ID": "...",
        "XINLIQIU_PEER_ID_FILE": "/abs/peer-id.txt",
        "XINLIQIU_KEY_FILE": "/abs/key.json",
        "XINLIQIU_OWNER_KEY": "/abs/owner.pem"
      }
    }
  }
}
```

### The eleven tools

| Tool | Returns |
|---|---|
| `xinliqiu_capabilities` | supported / bounded / unavailable — **call this first** |
| `xinliqiu_status` | recording, battery, error |
| `xinliqiu_inventory` | counts and bytes |
| `xinliqiu_record_start` / `_stop` | `task_id` |
| `xinliqiu_collect_voiceprint` | `task_id` |
| `xinliqiu_sync` | `task_id` |
| `xinliqiu_task_status` | task state and result |
| `xinliqiu_pull_changes` | new objects from storage |
| `xinliqiu_digest` | set digest for comparison |
| `xinliqiu_fetch_audio` | **base64 audio — the only tool yielding plaintext** |

### Three rules baked into the server

**1. Unavailable is stated, not hidden.**
An agent that calls `xinliqiu_capabilities` learns that `mark.add` cannot work
and stops trying. This is cheaper than failing repeatedly.

**2. Long operations return a task.**
Recording and syncing take longer than a typical tool timeout, so they return a
`task_id` immediately:

```
xinliqiu_sync(start_ms=..., end_ms=...)  →  {"task_id": "a1b2c3", "state": "running"}
xinliqiu_task_status("a1b2c3")           →  {"state": "succeeded", "result": {...}}
```

**3. Audio is never decrypted implicitly.**
`xinliqiu_sync` fetches and stores ciphertext. Only `xinliqiu_fetch_audio`,
with an explicit key path, produces plaintext. An agent cannot accidentally
pull your recordings into a transcription pipeline.

Errors are structured, never stack traces:

```json
{"ok": false, "error": {"type": "RuntimeError", "message": "...", "retryable": false}}
```

---

<a id="storage"></a>
## Storage

Storage is **optional and separate**. Skip this section entirely if you only
work on one machine.

### The core idea: ciphertext only

Every backend stores the **original encrypted bytes**. Nothing decrypts on the
way in.

```
       ┌─────────────┐
       │  your data  │
       └──────┬──────┘
              │  still encrypted
              ▼
   ┌──────────────────────┐
   │  Worker  (API only)  │
   └────┬────────────┬────┘
        │            │
        ▼            ▼
   ┌────────┐   ┌─────────┐
   │   R2   │   │   D1    │
   │ bytes  │   │ index   │
   └────────┘   └─────────┘
```

If the bucket leaks, the attacker gets encrypted packets whose keys are wrapped
with **your** public key. Without your private key they are noise.

### Deploy your own

```bash
cd cloudflare
./setup.sh        # creates bucket + database, applies schema, mints a token
npm run deploy
```

The script prints a token. Configure your client:

```bash
export XINLIQIU_STORE=cloudflare
export XINLIQIU_STORE_URL=https://xinliqiu-store.<you>.workers.dev
export XINLIQIU_STORE_TOKEN=<printed token>
```

Full details: [docs/storage.md](docs/storage.md).

### Sync between machines

Three operations, frequently conflated:

| Operation | Direction | Key needed | API |
|---|---|---|---|
| Fetch a range | device → you | no | `sync_range` |
| Upload ciphertext | you → store | no | `push_range` |
| Read new objects | store → you | no | `pull_changes` |

```python
from aipin_sdk import push_range, pull_changes, CloudflareStorage

store = CloudflareStorage(url, token)
push_range(ball, store, device_id, start_ms, end_ms)

page = pull_changes(store, device_id, since=cursor)   # cursor-driven
cursor = page["cursor"]                               # persist this
```

> **Why the cursor is not a timestamp:** device clocks can repeat, overlap, or
> regress. A time-based cursor would skip or duplicate objects. The store
> assigns a monotonic sequence at insertion, and cursors reference that.

To check whether two machines agree without transferring anything:

```python
store.digest(device_id)   # {'count': n, 'total_bytes': b, 'digest': '...'}
```

Full details: [docs/sync.md](docs/sync.md).

---

<a id="typescript"></a>
## TypeScript

```typescript
import { Xinliqiu, parseCmpt, entriesByKind, CloudflareStore } from "xinliqiu-sdk";

const ball = new Xinliqiu({
  deviceId: "...",
  peerIdFile: "/abs/peer-id.txt",
  keyFile: "/abs/key.json",
});

console.log(await ball.status());
console.log(Xinliqiu.capabilities());
```

A thin typed wrapper over the same transport. Container parsing and the storage
client are native TypeScript — no Python process required for those.

---

## Data format

```
CMPT container
  "CMPT" | u16 count | u16 data_offset | u32 declared_length
  count × 68-byte records: 64-byte name + u32 payload_length
  payloads, contiguous, in table order
```

Audio entries hold an `enpt` envelope:

```
"enpt" | u16 key_len | u16 nonce_len
       | RSA-2048 PKCS#1 v1.5 wrapped 32-byte AES key
       | 12-byte nonce | u16 0x0118 | u8 key_version | u8 reserved
       | ciphertext
       | 16-byte GCM tag | u16 16 | u16 24 | "enpt"
```

The trailer carries no sample count. That is why durations are packet-derived.

---

## Security

- Credentials and private keys are supplied by you and never leave the process.
- Storage backends receive ciphertext only.
- Decryption is always explicit.
- The MCP server returns metadata by default.
- No telemetry, no analytics, no phone-home.

Full model: [docs/security.md](docs/security.md).

---

## FAQ

**Why not just use the official app?**
The app is the intended path. This exists for people who want their recordings
in their own pipeline, on their own machine, without a cloud round trip.

**Can it read someone else's device?**
No. You need that device's credentials and the matching private key. There is
no bypass, and there will not be one.

**Why is `mark.add` listed as unavailable?**
Because on the hardware we tested it cannot be triggered from any reachable
path. Listing it honestly is more useful than pretending it might work.

**Does this flash or modify my device?**
No. The SDK contains no flashing, formatting, pairing, or key-installation
operations. Their absence is deliberate.

**What if I lose my private key?**
Past recordings stay unreadable. That is the encryption working as intended.
There is no recovery path and no reset.

---

## Project layout

```
xinliqiu-sdk/
├── AGENT-SETUP.md          full setup playbook for an agent
├── python/aipin_sdk/      core SDK, CLI, MCP server
├── ts/                     TypeScript wrapper
├── cloudflare/             Worker + R2 + D1 storage
├── mcp/run.sh              MCP launcher
├── docs/                   capabilities, security, storage, sync
├── examples/               quickstart and MCP smoke test
└── tests/                  offline suite, no hardware
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Two rules matter most:

1. **No vendor material.** No firmware, decompiled code, or disassembly.
2. **No credentials**, not even in test fixtures.

Security issues: [SECURITY.md](SECURITY.md).

---

## Legal

Unofficial. Not affiliated with or endorsed by OPPO. "OPPO" and "心力球" are
trademarks of their owners, used descriptively only.

This project does not bypass authentication, does not break encryption, and
does not include vendor binaries or reverse-engineering material.

**Use it on hardware you own, with credentials and keys that are yours.**

Read the full notice: **[LEGAL.md](LEGAL.md)**

---

<div align="center">

MIT licensed · [LEGAL](LEGAL.md) · [SECURITY](SECURITY.md) · [CONTRIBUTING](CONTRIBUTING.md)

</div>
