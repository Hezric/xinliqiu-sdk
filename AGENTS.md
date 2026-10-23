# AGENTS.md

You are an agent, and you have been asked to help someone use their **心力球**
(OPPO AI Pin, model `CNMABWC301`) from their own software.

This file is the complete brief. Read it top to bottom. It tells you what the
hardware can do, what it cannot, how to integrate, and where the traps are.

If you only read one section, read **[Decide the integration mode](#decide-the-integration-mode)**.

---

## 1. What this hardware is

A small wearable that records audio. Crucially:

> Recordings on the device are encrypted to a key pair **the owner installs on
> the device**. Only the matching private key can read them back.

That is the entire reason an SDK exists. There is no cloud account involved, no
vendor service to call, and no way to read a device you do not own.

---

## 2. Decide the integration mode

Three ways to integrate. Pick one based on what the user is building, not on
what is easiest for you.

| Mode | Choose when | Interface |
|---|---|---|
| **MCP server** | the user wants *their agent* to drive the device, or wants a zero-code integration | `mcp/run.sh` over stdio |
| **Python library** | the user is writing a program, service, or pipeline | `import aipin_sdk` |
| **CLI** | the user wants a manual step, a cron job, or a shell script | `xinliqiu <command>` |
| **TypeScript** | the user's app is Node/Bun | `import ... from "xinliqiu-sdk"` |

All four share one implementation and one capability table. They do not differ
in behaviour, only in ergonomics.

**Default recommendation:** if the user says "I want to use it with an AI",
integrate MCP. If they say "I want it in my app", integrate the library.

---

## 3. What actually works

This is the authoritative capability list. It is the same data the SDK returns
at runtime from `aipin_sdk.Xinliqiu.capabilities()`.

### Supported - build on these freely

| Capability | Contract |
|---|---|
| Authenticate with existing credentials | No pairing needed if already provisioned |
| Read status | `{recording: bool, battery_percent: int, error_code: int}` |
| Inventory totals | `{count, bytes, highlight_count}` for a time range |
| Storage report | `{used_bytes, item_count, free_bytes: None}` - the device never exposes total or free capacity |
| Fetch a range | Returns CMPT containers as files |
| Decrypt | Requires the owner private key; raises on failure |

### Bounded - works, but plan around the limit

| Capability | The limit |
|---|---|
| Start/stop recording | Toggles state. **Does not guarantee a file.** |
| Audio creation | **Only when voice activity is detected.** Silence yields nothing. |
| Segmentation | Silence splits files; ordinary files roll over at ~60 s. |
| Silence retention | Trailing silence kept briefly, not indefinitely. |
| Voiceprint | One capture, 1-127 s. Not a continuous recorder. |
| Opus to WAV | Duration is packet-count based, not native trim. |

### Unavailable - **do not design around these**

| Capability | Why |
|---|---|
| `mark.add` (mark highlight) | No reachable trigger: not BLE, not app, not physical key |
| `note.archive` | Manual archive path not reachable over BLE |
| `key.ai_manual` | AI key toggles recording; release never ends it |

These are **established negatives**. If the user asks for a highlight feature,
tell them it cannot be built on this hardware and show the capability entry.
Do not attempt workarounds and do not silently omit the limitation.

---

## 4. MCP integration

### Server

```bash
./mcp/run.sh          # speaks MCP over stdio, no dependencies beyond Python
```

### Client config

```json
{
  "mcpServers": {
    "xinliqiu": {
      "command": "/abs/path/to/xinliqiu-sdk/mcp/run.sh",
      "env": {
        "XINLIQIU_DEVICE_ID": "<bluetooth id>",
        "XINLIQIU_PEER_ID_FILE": "/abs/peer-id.txt",
        "XINLIQIU_KEY_FILE": "/abs/key.json",
        "XINLIQIU_OWNER_KEY": "/abs/owner.pem"
      }
    }
  }
}
```

### Tools

| Tool | Args | Returns |
|---|---|---|
| `xinliqiu_capabilities` | none | supported / bounded / unavailable |
| `xinliqiu_status` | none | recording, battery, error |
| `xinliqiu_inventory` | `start_ms?`, `end_ms?` | count, bytes |
| `xinliqiu_storage` | `start_ms?`, `end_ms?` | used bytes, item count, low-space thresholds |
| `xinliqiu_record_start` | none | `task_id` |
| `xinliqiu_record_stop` | none | `task_id` |
| `xinliqiu_collect_voiceprint` | `seconds` 1-127 | `task_id` |
| `xinliqiu_sync` | `start_ms`, `end_ms`, `store?` | `task_id` |
| `xinliqiu_task_status` | `task_id` | state + result |
| `xinliqiu_pull_changes` | `since?`, `limit?`, `download?` | metadata |
| `xinliqiu_digest` | `start_ms?`, `end_ms?` | set digest |
| `xinliqiu_fetch_audio` | `container_path`, `owner_key_path?` | base64 audio |

### Three behaviours to expect

1. **Long operations are asynchronous.** `record_start`, `collect_voiceprint`
   and `sync` return `{task_id}` immediately. Poll `xinliqiu_task_status`.
   Do not retry the original call.
2. **Audio is never decrypted implicitly.** Only `xinliqiu_fetch_audio`
   produces plaintext, and it needs an explicit key path.
3. **Errors are structured.** `{ok: false, error: {type, message, retryable}}`.
   Check `retryable` before retrying. Credential errors are not retryable.

---

## 5. Library integration (Python)

```python
from pathlib import Path
from aipin_sdk import Xinliqiu, Credentials

ball = Xinliqiu(Credentials(
    device_id="...",
    peer_id_file=Path("~/.config/xinliqiu/peer-id.txt").expanduser(),
    key_file=Path("~/.config/xinliqiu/key.json").expanduser(),
))

print(ball.capabilities())      # check availability first
print(ball.status())
result = ball.sync_range(start_ms, end_ms)
```

Everything else hangs off these calls. See `docs/getting-started.md` for
provisioning a fresh device and `docs/sync.md` for multi-machine replication.

---

## 6. Provisioning a fresh device

**No OPPO phone, account, or root is required.**

```bash
xinliqiu scan                                         # find device id
xinliqiu pair --device <id> --out-dir ./xinliqiu-creds
xinliqiu doctor --device <id> --peer-id-file ... --key-file ...
```

`pair` remembers the credential paths, so later commands need no flags.

Optional: installing an owner key enables decryption. It may discard existing
recordings, requires a signing input extracted from the user's own copy of the
official app, and `xinliqiu init` refuses to run without
`--acknowledge-risk`. Full procedure: **[AGENT-SETUP.md](AGENT-SETUP.md)**.

---

## 6a. Two facts that decide the user's outcome

### Two KSC slots, and a re-pair does not evict the phone

The firmware holds exactly two KSC slots (`if (1 < slot) return -1` in
`ksc_write`). A phone and this host can therefore coexist, but never three.

What makes re-pairing safe is the alias: the write path compares the incoming
alias against each slot and, on a match, updates that slot in place instead of
falling back to least-recently-used eviction. `pair` and the CLI persist the
alias in the config file and reuse it on every subsequent pairing, so repeated
`pair` runs keep rewriting the same slot and cannot push the user's phone out.

Never tell a user that pairing with the SDK disconnects their phone. It does
not. Only a third distinct host evicts anything.

### Losing the owner key stops future recordings too

It is tempting to describe key loss as "old recordings are unreadable". That is
incomplete and misleading. The device encrypts with the public key installed in
it, and `init` can only run once. So after the private key is lost:

- recordings already on the device cannot be decrypted;
- the device keeps working normally;
- **everything it records from then on is encrypted to the same orphaned public
  key and is equally unreadable.**

There is no re-init and no recovery. Say it that way.

### Recordings made under the phone app cannot be decrypted

When the app provisions the device it installs a public key supplied by OPPO's
`device_security_config` endpoint. The matching private key stays on OPPO's
side. So for a device that was only ever used with the phone app:

| Step | Possible |
|---|---|
| Pair from the computer | yes |
| Fetch the existing ciphertext | yes |
| Decrypt it | **no, never** |
| Decrypt anything recorded after `init` | yes |

Say this plainly. Do not imply a future recovery path. The value of the SDK for
such users is ownership of future recordings, not rescue of old ones.

### How much the device holds

`storage_info()` returns `used_bytes` and `item_count`. `free_bytes` and
`total_bytes` are `None` because the firmware does not report capacity. Do not
invent them.

The thresholds that do matter are fixed constants: at 50 MiB free the device
warns, at 10 MiB free it deletes the oldest whole day of recordings, and below
5 MiB it stops creating segments. Audio runs at 2,000 B/s, so 50 MiB is about
7.3 hours of continuous speech - far longer in practice, since silence produces
no files. Tell the user to sync daily.

## 7. Hard rules

Five things are irreversible. Everything else the SDK handles on its own.

| Rule | Consequence of breaking it |
|---|---|
| Never flash firmware | Can brick the device |
| Never format or erase | Destroys the user's recordings |
| Never run `init` more than once | It may discard existing recordings, and a second run is unsupported |
| Never substitute a vendor APK from a mirror | Modified builds produce wrong signatures and broken results |
| Never claim a capability works without checking the table | The user builds on a false premise |

If a step fails, stop and report where. Do not retry in a loop and do not route
around a blocker; both hide the real failure from the user.

## 8. Reference

| Document | Covers |
|---|---|
| [README.md](README.md) | Human-facing overview |
| [AGENT-SETUP.md](AGENT-SETUP.md) | Step-by-step provisioning, including APK acquisition |
| [docs/capabilities.md](docs/capabilities.md) | Capability semantics in depth |
| [docs/sync.md](docs/sync.md) | Replication and cursors |
| [docs/storage.md](docs/storage.md) | Self-hosted ciphertext store |
| [docs/security.md](docs/security.md) | Guarantees and threat model |
| [LEGAL.md](LEGAL.md) | Scope and trademark notice |
