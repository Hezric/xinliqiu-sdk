# Agent setup playbook

You are reading this because a user wants to make their **OPPO AI 心力球**
work from their own computer, and asked an agent to do the setup.

This file is a **complete, self-contained instruction set**. Follow it end to
end. If a step cannot be completed, stop and report exactly which step failed
and why — do not improvise around blockers.

**Project:** https://github.com/kiyoakii/xinliqiu-sdk

**Companion document:** read `AGENTS.md` first for the capability contract and
the choice between MCP, library and CLI. This file covers provisioning only.

---

## Ground rules

1. **The device belongs to the user.** Ask once, at the start, for confirmation
   that they own the hardware. If they do not confirm, stop.
2. **Never flash, format, or erase.** No firmware work of any kind.
3. **Never send credentials anywhere.** They stay on this machine.
4. **Do not guess protocol bytes.** Use only the tooling described here.
5. **Report honestly.** If something is unavailable, say so.

---

## Step 0 - Decide by one question

Everything below hinges on a single question. Ask it before touching the device:

> **"Does this device hold recordings you care about?"**

### If no, or the device is new

Proceed straight through steps 1-5. `init` may discard old content; that is fine
because nobody needs it.

### If yes

Do **not** run `init`. Do this instead, in order:

1. Pair (step 3).
2. Pull everything out to a rescue directory: `xinliqiu sync 0 <now>`.
3. Tell the user this plainly:

> "Those recordings were encrypted with a key that is not available anywhere,
> so they cannot be decrypted now or later. I saved the ciphertext so it is not
> lost, but I cannot turn it into audio. If you want to keep them, do not run
> `init` on this device."

Do not soften this. Do not imply a future recovery path exists. None does.

### If the user asks to start over

There is no factory-reset button and no unpair action in the official manual.
The only reset available is `hold the system key for 16 s`, which forces a
reboot and **does not clear pairing** - verified: existing credentials still
authenticate after such a reboot.

So on a device bound to someone else, whether a new pairing succeeds depends on
whether the previous owner unbound it. **This has not been tested.** If `pair`
fails, stop and report. Do not attempt to force, erase, or recover anything.

---

## Step 1 — Check the environment

```bash
python3 --version          # need 3.10+
command -v ble-probe || echo MISSING
```

Install the SDK:

```bash
pip install "xinliqiu-sdk[crypto]"
```

If `ble-probe` is missing, ask the user for its path and export it:

```bash
export XINLIQIU_PROBE=/path/to/ble-probe
```

---

## Step 2 — Locate the device

```bash
xinliqiu scan
```

Record the Bluetooth identifier. It looks like
`XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`. This is the `device_id`.

If nothing appears: ask the user to wake the device and keep it nearby, then
retry. Do not proceed until a device is visible.

---

## Step 3 — Pair

```bash
xinliqiu pair --device <device_id> --out-dir ./xinliqiu-creds
```

Expected: `key.json` and `peer-id.txt` appear in `./xinliqiu-creds`.

Then verify:

```bash
xinliqiu doctor \
  --device <device_id> \
  --peer-id-file ./xinliqiu-creds/peer-id.txt \
  --key-file ./xinliqiu-creds/key.json
```

Expected: `"ready": true`.

**Stop here if pairing fails.** Report the exact error. Do not retry more than
once — repeated pairing attempts on a device that refuses are not productive.

At this point the SDK can already read status, fetch recordings, and store
ciphertext. If the user only wants that, **the setup is complete**.

---

## Step 4 — Owner key (required for decryption)

Pairing establishes a connection. It does **not** decide who can read the
recordings.

Until an owner key is installed, the device encrypts with the key it shipped
with, and you do not have that private key. Recordings made in that state **can
never be decrypted by the user** - not later, not after `init`.

So this step is not optional if the user wants audio. It is optional only if
they purely want to move ciphertext around.

Two facts to state to the user before running it:

- it can discard recordings already on the device, which is why the command
  requires `--acknowledge-risk`;
- it can be done **once per device**. A second run is not supported.

**Recommended order:** on a factory-fresh device, run `init` first, then start
recording. This way nothing is lost and every recording is decryptable.

### Ask before continuing

The user very likely **does not own an OPPO phone**, so do not send them to a
phone. The official app is distributed through OPPO's public store CDN and can
be fetched directly from a normal computer.

### 4a. Obtain the official app package

**Target identity** (verify after download, do not assume):

| Field | Value |
|---|---|
| Package name | `com.oplusai.moss.phone` |
| Store name | 心力球 |
| OPPO store resource id | `26060509` |
| Expected size | ~50 MB |

**Official download entry:**

```
https://istore.oppomobile.com/download/v1/26060509
```

This returns a `302` redirect to OPPO's CDN (`*.heytapdownload.com`). Fetch with
redirects enabled:

```bash
curl -L -o ./xinliqiu.apk   'https://istore.oppomobile.com/download/v1/26060509'
```

Then confirm it is the right artifact:

```bash
unzip -l ./xinliqiu.apk | head          # should contain classes*.dex
```

**If the id stops working**, the store entry must be looked up again. The
lookup requires an OPPO China store client with `romver: 37` (ColorOS 16). If
you cannot re-derive it, ask the user for an APK they obtained themselves and
continue with that file. Do **not** substitute a package from a third-party
mirror: mirrors frequently ship modified builds, and a wrong build will produce
wrong signatures.

Record the SHA-256 of whatever file you used, and tell the user which source it
came from.

### 4b. Extract the signing input

The file needed is the decompiled body of:

```
com.oplusai.moss.domain.device.auth.K1BindingSigner$sign$2
```

(If that fully-qualified name is obfuscated in the build, search instead for
the class matching the structural description below.)

Install a decompiler locally if missing:

```bash
# macOS
brew install jadx
# or download a release from https://github.com/skylot/jadx/releases
```

Decompile only the class you need:

```bash
jadx --single-class 'com.oplusai.moss.domain.device.auth.K1BindingSigner$sign$2'      -d ./apk-extract ./xinliqiu.apk
```

If that exact name is absent, search the extracted sources:

```bash
grep -rl 'sign\$2' ./apk-extract --include='*.java' | head
```

### 4c. Generate the owner key

```bash
xinliqiu keygen
```

It writes to `~/.config/xinliqiu/owner.pem` (mode `0400`) and the base64 SPKI
to `~/.config/xinliqiu/owner.spki.txt`. Never let the key sit in a working
directory: a key lost to a `cd` or a deleted checkout is lost forever.

#### Stop and make the user back it up

This is not a courtesy warning. **Every recording encrypted to this key becomes
unreadable noise the moment the file is lost.** There is no recovery, no reset,
and no support path - the device holds only the public half.

Do both of these before continuing:

**1. Say it plainly, in the user's own language:**

> "This file is the only key to every recording this device will make from now
> on. If you lose it - disk failure, reinstall, deleted folder - those
> recordings are gone permanently. I cannot recover them for you. Please copy
> `~/.config/xinliqiu/owner.pem` to somewhere outside this machine right now:
> a password manager, an encrypted backup, a USB key in a drawer. Tell me when
> it is done."

**2. Wait for an explicit confirmation.** Not "ok" in passing - a statement that
the copy exists somewhere else.

If the user declines to back it up, **stop**. Do not run `init`. A device with no
key is safer than a device whose key is one disk failure from gone; they can
resume later.

### Ask before continuing

The user very likely **does not own an OPPO phone**, so do not send them to a
phone. The official app is distributed through OPPO's public store CDN and can
be fetched directly from a normal computer.

### 4a. Obtain the official app package

**Target identity** (verify after download, do not assume):

| Field | Value |
|---|---|
| Package name | `com.oplusai.moss.phone` |
| Store name | 心力球 |
| OPPO store resource id | `26060509` |
| Expected size | ~50 MB |

**Official download entry:**

```
https://istore.oppomobile.com/download/v1/26060509
```

This returns a `302` redirect to OPPO's CDN (`*.heytapdownload.com`). Fetch with
redirects enabled:

```bash
curl -L -o ./xinliqiu.apk   'https://istore.oppomobile.com/download/v1/26060509'
```

Then confirm it is the right artifact:

```bash
unzip -l ./xinliqiu.apk | head          # should contain classes*.dex
```

**If the id stops working**, the store entry must be looked up again. The
lookup requires an OPPO China store client with `romver: 37` (ColorOS 16). If
you cannot re-derive it, ask the user for an APK they obtained themselves and
continue with that file. Do **not** substitute a package from a third-party
mirror: mirrors frequently ship modified builds, and a wrong build will produce
wrong signatures.

Record the SHA-256 of whatever file you used, and tell the user which source it
came from.

### 4b. Extract the signing input

The file needed is the decompiled body of:

```
com.oplusai.moss.domain.device.auth.K1BindingSigner$sign$2
```

(If that fully-qualified name is obfuscated in the build, search instead for
the class matching the structural description below.)

Install a decompiler locally if missing:

```bash
# macOS
brew install jadx
# or download a release from https://github.com/skylot/jadx/releases
```

Decompile only the class you need:

```bash
jadx --single-class 'com.oplusai.moss.domain.device.auth.K1BindingSigner$sign$2'      -d ./apk-extract ./xinliqiu.apk
```

If that exact name is absent, search the extracted sources:

```bash
grep -rl 'sign\$2' ./apk-extract --include='*.java' | head
```

### 4c. Generate the owner key

```bash
xinliqiu keygen
```

It writes to `~/.config/xinliqiu/owner.pem` (mode `0400`) and the base64 SPKI
to `~/.config/xinliqiu/owner.spki.txt`. Never let the key sit in a working
directory: a key lost to a `cd` or a deleted checkout is lost forever.

#### Stop and make the user back it up

This is not a courtesy warning. **Every recording encrypted to this key becomes
unreadable noise the moment the file is lost.** There is no recovery, no reset,
and no support path - the device holds only the public half.

Do all three of these before continuing:

**1. Say it plainly, in the user's own language:**

> "This file is the only key to every recording this device will make from now
> on. If you lose it - disk failure, reinstall, deleted folder - those
> recordings are gone permanently. I cannot recover them for you. Please copy
> `~/.config/xinliqiu/owner.pem` to somewhere outside this machine right now:
> a password manager, an encrypted backup, a USB key in a drawer. Tell me when
> it is done."

**2. Wait for an explicit confirmation.** Not "ok" in passing - a statement that
the copy exists somewhere else.

**3. Verify the key is usable:**

```bash
xinliqiu keycheck
```

Expect `"ok": true`. If the user already has recordings, pass `--sample` with a
container from the device; that is the only check proving the key actually
matches those files.

If the user declines to back it up, **stop**. Do not run `init`. A device with no
key is safer than a device whose key is one disk failure from gone; they can
resume later.

### Ask before continuing

The user very likely **does not own an OPPO phone**, so do not send them to a
phone. The official app is distributed through OPPO's public store CDN and can
be fetched directly from a normal computer.

### 4d. Confirm the risk, then install

The device may discard recordings it already holds. Only after an explicit yes:

```bash
xinliqiu init \
  --owner-public-key ~/.config/xinliqiu/owner.spki.txt \
  --signing-input ./apk-extract/K1BindingSigner.java \
  --capture-dir ./captures \
  --acknowledge-risk
```

Success looks like `"init_accepted": true`.

> **Run this at most once per device.** Re-running an initialisation is not a
> supported operation.

## Step 5 — Verify end to end

```bash
xinliqiu status
xinliqiu inventory
xinliqiu sync 0 9999999999999 --out ./captures
```

To decode one recording:

```python
from pathlib import Path
from aipin_sdk import parse_cmpt_file, decrypt_enpt, to_wav

for path in sorted(Path("./captures").glob("*.bin")):
    try:
        container = parse_cmpt_file(path)
    except Exception:
        continue
    for entry in container.by_kind("audio"):
        wav = to_wav(decrypt_enpt(entry.payload, Path("./owner.pem")))
        Path(f"./{entry.name}.wav").write_bytes(wav)
        print("decoded", entry.name)
```

---

## Step 6 — Report back

Summarise for the user:

1. `device_id` used;
2. whether pairing succeeded;
3. whether an owner key was installed;
4. how many recordings were found;
5. any step that was skipped and why.

---

## Known limitations to state plainly

Tell the user these, do not hide them:

| Limitation | Meaning |
|---|---|
| Recording only creates files on speech | A silent session produces no audio |
| No highlight/mark feature | It cannot be triggered from any reachable path |
| AI key toggles recording | One press starts, a second stops; releasing never ends |
| Duration is packet-based | Not the original microphone duration |
| One initialisation per device | `init` must not be repeated |

---

## If something fails

| Failure | Do this |
|---|---|
| No devices found | Ask user to wake the device; retry once |
| Pairing refused | Report and stop. Do not loop |
| `doctor` not ready | Fix the specific failed check it names |
| `init` rejected | Report `ack_status`; do not retry blindly |
| Decryption fails | The private key does not match that recording. Stop. |

Never fabricate success. If a step did not work, say so.
