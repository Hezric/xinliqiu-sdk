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

Two ways to integrate. Pick one based on what the user is building, not on
what is easiest for you.

| Mode | Choose when | Interface |
|---|---|---|
| **Python library** | the user is writing a program, service, or pipeline | `import aipin_sdk` |
| **CLI** | the user wants a manual step, a cron job, or a shell script | `xinliqiu <command>` |

Both speak to the same device and report the same capabilities. They do not
differ in behaviour, only in ergonomics.

**Default recommendation:** if the user is writing a program, use the library. If
they want a manual step, a cron job or a shell script, use the CLI.

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
| Decrypt | Requires the owner private key; raises on failure |
| Fetch a range | Request a device-clock window; the file stream receives each container, verifies MD5 when supplied, completes the transaction, and never overwrites an earlier file. |

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

## 4. Library integration (Python)

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

## 5. Provisioning a fresh device

**No OPPO phone, account, or root is required.**

```bash
xinliqiu scan                                         # find device id
xinliqiu pair --device <id> --out-dir ./xinliqiu-creds
xinliqiu doctor --device <id> --peer-id-file ... --key-file ...
```

`pair` remembers the credential paths, so later commands need no flags.

Installing an owner key (so recordings can be decrypted with a key you control)
is `xinliqiu init`. It formats the device, so it requires `--allow-format`.
Re-running it is possible and rebinds a new key, but each run erases whatever
the device holds. The signing material comes from the user's own copy of the
official app and is **not shipped here**; pass it with `--signing-key` (add
`--signing-key-is-java-source` when supplying the extracted Java file). Full
procedure: **[AGENT-SETUP.md](AGENT-SETUP.md)**.

---

## 6. Two facts that decide the user's outcome

### Two KSC slots, and a re-pair does not evict the phone

The device keeps exactly two KSC slots. A phone and this host can therefore
coexist, but never three.

What makes re-pairing safe is the alias: the write path compares the incoming
alias against each slot and, on a match, updates that slot in place instead of
falling back to least-recently-used eviction. `pair` and the CLI persist the
alias in the config file and reuse it on every subsequent pairing, so repeated
`pair` runs keep rewriting the same slot and cannot push the user's phone out.

Never tell a user that pairing with the SDK disconnects their phone. It does
not. Only a third distinct host evicts anything.

### Losing the owner key stops future recordings too

It is tempting to describe key loss as "old recordings are unreadable". That is
incomplete and misleading. The device encrypts with whatever public key was
installed in it. So after the private key is lost:

- recordings already on the device cannot be decrypted;
- the device keeps working normally;
- **everything it records from then on is encrypted to the same orphaned public
  key and is equally unreadable.**

The device itself can be re-initialised (`init --allow-format`), and that does
install a new key - but it erases the device, so it rescues the hardware, not
the recordings. Say it that way: the key is the irreversible part.

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

If the user asks whether formatting would help: on a **slot-1** device, yes -
`xinliqiu init --allow-format` rewrites the account record and installs a new
owner public key, which takes the device back from the app. It erases whatever
the device holds every time it runs, so confirm that before running it.

On a **slot-2** device, no. Those units only accept an init token signed by the
vendor's fixed key, so no locally held signing input can complete the exchange.
Tell the user that plainly instead of suggesting they try anyway.

### How much the device holds

`storage_info()` returns `used_bytes` and `item_count`. `free_bytes` and
`total_bytes` are `None` because the device does not report capacity. Do not
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
| Never format or erase outside `init --allow-format` | Destroys the user's recordings |
| Never run `init` without confirming the data is expendable | It erases everything the device holds |
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
