# Getting started from a brand-new device

You do **not** need an OPPO phone, an OPPO account, or root access.

A factory-fresh xinliqiu can be paired from a normal computer. This page walks
through it.

---

## What you need

| Item | Required? | Notes |
|---|---|---|
| A xinliqiu | yes | factory-fresh, or reset to a pairable state |
| A computer with Bluetooth | yes | macOS and Linux supported by the reference transport |
| This SDK | yes | `pip install "xinliqiu-sdk[all]"` |
| OPPO phone | **no** | |
| OPPO account | **no** | |
| Root | **no** | |

---

## Step 0 — find the device

```bash
xinliqiu scan          # lists nearby devices
```

Copy the Bluetooth identifier it prints. That is your `device_id`. It is local
to this machine, so it can differ between computers.

---

## Step 1 — pair

```bash
xinliqiu pair --device <device_id> --out-dir ./xinliqiu-creds
```

What happens:

1. the transport performs the pairing handshake with the device;
2. a credential file `key.json` is written with mode `0600`;
3. a stable `peer-id.txt` is generated.

Takes a couple of minutes. **It does not format, erase, or flash anything.**

> If the directory already contains credentials, the command refuses to run.
> Overwriting a key pair would orphan this host from the device permanently.

---

## Step 2 — check your setup

```bash
xinliqiu doctor \
  --device <device_id> \
  --peer-id-file ./xinliqiu-creds/peer-id.txt \
  --key-file ./xinliqiu-creds/key.json
```

You should see `"ready": true`. If not, the report names exactly what is
missing or misconfigured.

---

## Step 3 — use it

```bash
xinliqiu status
xinliqiu sync 0 1800000000000 --out ./captures
```

---

## Optional — owner key install

Without this step you can record, fetch and store encrypted data, but you will
not be able to decrypt recordings with a key you control.

```bash
xinliqiu init \
  --owner-public-key ./owner-spki.txt \
  --signing-key ./signing-input.java --signing-key-is-java-source \
  --account local-owner \
  --allow-format
```

Two things to know before running it:

1. **It discards everything already on the device.** That is why it requires
   `--allow-format` and refuses to run without it.
2. **The signing input is yours to supply.** It comes from your own copy of the
   official app; the repository never carries it. Extraction steps are in
   **[AGENT-SETUP.md](../AGENT-SETUP.md)**. If you do not have it, skip this
   step entirely.

Re-running it is possible and rebinds whatever key you pass, but each run erases
the device. A slot-2 unit cannot complete this at all: it only accepts a token
signed by the vendor's fixed key.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `pair` writes no key file | device refused the handshake — check it is in a pairable state and in range |
| `doctor` reports `peer_id_length` wrong | the peer id file must contain exactly 36 characters |
| `missing credentials` on every command | environment variables not set |
| Status works but sync returns nothing | the device may hold no recordings in that range |
| `record_start` succeeded, no audio later | nothing was said — audio requires detected voice activity |
