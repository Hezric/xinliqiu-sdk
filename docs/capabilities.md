# Capability reference

The canonical source is `python/aipin_sdk/capabilities.py`. This page
explains what each entry means in practice.

## Statuses

| Status | Meaning |
|---|---|
| `supported` | Implemented and confirmed working on a real device |
| `bounded` | Works, but within documented limits |
| `unavailable` | Device behaviour exists internally but no reachable trigger exists |

## Supported

**`auth.reuse_existing_ksc`** — authenticate with the credentials you already
have. No pairing, no re-initialisation, no key installation.

**`record.status`** — one read-only query returning recording state, battery
percentage and an error byte.

**`files.inventory_totals`** — aggregate item count and byte total for a
device-clock range. The device exposes totals only: there is no file list and
no cursor.

**`files.sync_range`** — request a range and receive CMPT containers. The
device is a delivery queue rather than an archive: each container is offered
once, so a second request for the same window is legitimately empty. Point the
output directory at durable storage.

**`crypto.owner_key_decrypt`** — RSA unwrap plus AES-256-GCM verification with
a private key you supply.

**`device.init_owner_key`** — format the device and install an owner public
key. It is the step that decides who can ever read the recordings, so it is
worth understanding before running it:

- it erases everything the device currently holds;
- it needs signing material you supply, taken from your own copy of the
  official app — the SDK never carries it;
- re-running it rebinds whatever key you pass, but each run erases the device;
- a slot-2 unit cannot complete it at all: those units only accept a token
  signed by the vendor's fixed key.

## Bounded

**`record.start_stop`** — toggles recording. A started recording can end with
zero audio files if nothing was said.

**`record.vad_gated_audio`** — files are created only when voice activity is
detected. This is why an apparently successful `record_start` can yield no
audio.

**`record.segmentation`** — speech separated by silence produces separate
files. Ordinary files roll over at 120,000 bytes, about 60 seconds of audio.

**`record.silence_retention`** — trailing silence is kept for a bounded period
rather than being cut at the exact moment speech stops.

**`voiceprint.collect`** — single capture, 1 to 127 seconds per request. This is
a capture endpoint, not a continuous recorder.

**`audio.opustowav`** — plaintext is fixed 40-byte / 20 ms Opus packets. The
container holds no native sample count, so any duration you compute is a packet
count duration.

## Unavailable

These are established negatives, not gaps.

**`mark.add`** — the *标记重点* (mark highlight) action.

The firmware contains a highlight command, but there is no BLE producer for it,
the Android app has no command for it, and neither short nor long presses of
the physical AI key produce it. Do not design around it.

**`note.archive`** — the manual note archive path (`.note.opus`). Not reachable
over BLE and not produced by the key.

**`key.ai_manual`** — the AI key's manual conversation mode.

Observed behaviour instead: a short press starts recording, a second short
press stops it. Holding the key and releasing **never** ends a recording,
regardless of hold duration.

## Using the table programmatically

```python
from aipin_sdk import Xinliqiu, Unsupported

capabilities = Xinliqiu.capabilities()
for name in capabilities["statuses"]["unavailable"]:
    print("not available:", name["name"], "-", name["note"])

try:
    Xinliqiu.require_capability("mark.add")
except Unsupported as exc:
    print("refused:", exc)
```
