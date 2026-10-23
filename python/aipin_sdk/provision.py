# -*- coding: utf-8 -*-
"""First-time provisioning: turn a fresh device into one this SDK can talk to.

Two stages, both optional and clearly separated:

Stage 1 - pair()
    Establishes a key (KSC) between this host and the device and writes a
    credential file plus a stable peer id. Safe to run on a factory-fresh
    device. Does not touch recordings.

Stage 2 - install_owner_key()
    Installs the public half of an owner key pair on the device so future
    recordings can be decrypted by the matching private key.

    This stage requires a signing input file that the SDK does not ship. If you
    do not have one, you can still pair and use the device; you simply will not
    be able to decrypt recordings made under an owner key you control.

Neither stage formats, flashes, or erases anything. Stage 2 will be refused
when the caller has not explicitly acknowledged that the device may lose
recordings it already holds.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from .client import Transport, SubprocessTransport
from .errors import XinliqiuError


@dataclass(frozen=True)
class Provisioned:
    device_id: str
    key_file: Path
    peer_id_file: Path
    paired: bool


def _write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def generate_peer_id() -> str:
    """A stable 36-byte printable local identifier.

    The transport expects exactly 36 characters; we generate 36 hex characters
    so it is URL-safe and easy to persist.
    """
    return secrets.token_hex(18)


def pair(
    device_id: str,
    out_dir: Path | str,
    *,
    transport: Transport | None = None,
    probe_binary: str | None = None,
    peer_id: str | None = None,
    ksc_alias: str | None = None,
) -> Provisioned:
    """Stage 1: pair with a fresh device and persist credentials.

    Creates ``<out_dir>/key.json`` and ``<out_dir>/peer-id.txt`` with mode 0600.
    Refuses to overwrite an existing credential file: rotating a key pair is a
    destructive act on the device side and must be an explicit operator choice.

    ``ksc_alias`` pins the 6-byte alias under which this host is stored on the
    device. The firmware holds two KSC slots and evicts by least-recently-used,
    but it updates a slot in place when the alias matches. Reusing the alias a
    host was first paired with therefore keeps re-pairing from consuming the
    second slot, which is usually the user's phone.
    """
    out = Path(out_dir)
    key_file = out / "key.json"
    peer_file = out / "peer-id.txt"
    if key_file.exists() or peer_file.exists():
        raise XinliqiuError(
            f"credentials already exist under {out}. "
            "Refusing to overwrite: deleting them would orphan this host from the device."
        )

    transport = transport or SubprocessTransport(probe_binary)

    # The reference transport performs the two-stage handshake under a single
    # `pair` invocation; `pair-init` remains available for diagnosis.
    argv = ["pair", "--id", device_id, "--key-output", str(key_file)]
    if ksc_alias:
        if len(ksc_alias) != 12 or any(c not in "0123456789abcdef" for c in ksc_alias.lower()):
            raise XinliqiuError("ksc_alias must be 12 hex characters (6 bytes)")
        argv += ["--ksc-alias", ksc_alias.lower()]
    argv += ["--seconds", "40"]
    events = transport.run(argv, 90.0)
    finished = next((e for e in events if e.get("event") == "finished"), None)
    if finished is None:
        raise XinliqiuError("pairing produced no terminal event")
    if not key_file.exists():
        raise XinliqiuError(
            "pairing did not write a credential file; the device likely refused the handshake. "
            f"reason={finished.get('reason')}"
        )
    os.chmod(key_file, 0o600)

    pid = peer_id or generate_peer_id()
    if len(pid) != 36:
        raise XinliqiuError("peer id must be exactly 36 characters")
    _write_private(peer_file, pid)

    return Provisioned(device_id=device_id, key_file=key_file, peer_id_file=peer_file, paired=True)


def install_owner_key(
    device_id: str,
    peer_id_file: Path | str,
    key_file: Path | str,
    owner_public_key: Path | str,
    signing_input: Path | str,
    capture_dir: Path | str,
    *,
    acknowledge_risk: bool = False,
    transport: Transport | None = None,
    probe_binary: str | None = None,
    account_label: str = "xinliqiu-sdk",
) -> dict:
    """Stage 2: install an owner public key on the device.

    Guard rail: the device may discard recordings it already holds. The call is
    refused unless ``acknowledge_risk=True``.

    ``signing_input`` is a file you must supply yourself; the SDK does not
    distribute vendor code. If you do not have it, skip this stage.
    """
    if not acknowledge_risk:
        raise XinliqiuError(
            "install_owner_key can cause the device to discard existing recordings. "
            "Re-run with acknowledge_risk=True to proceed."
        )
    for path in (peer_id_file, key_file, owner_public_key, signing_input):
        if not Path(path).exists():
            raise XinliqiuError(f"required input missing: {path}")

    capture = Path(capture_dir)
    capture.mkdir(parents=True, exist_ok=True)
    transport = transport or SubprocessTransport(probe_binary)

    events = transport.run(
        [
            "init-local",
            "--id", device_id,
            "--peer-id-file", str(peer_id_file),
            "--key-file", str(key_file),
            "--capture", str(capture / "init.jsonl"),
            "--allow-record-format",
            "--account", account_label,
            "--record-public-key", str(owner_public_key),
            "--record-public-key-version", "1",
            "--apk-test-signer-source", str(signing_input),
            "--seconds", "45",
        ],
        120.0,
    )
    finished = next((e for e in events if e.get("event") == "finished"), None)
    if finished is None:
        raise XinliqiuError("init produced no terminal event")
    return {
        "init_accepted": bool(finished.get("initAccepted")),
        "public_key_matched": bool(finished.get("initPublicKeyMatched")),
        "ack_status": finished.get("initAckStatus"),
        "reason": finished.get("reason"),
    }





def default_owner_key_path() -> Path:
    """Where the owner key lives by default.

    A dotfile under the user's home directory, not the working directory: a
    key that is lost with a `cd` or a deleted checkout is a key that takes
    every recording with it.
    """
    override = os.environ.get("XINLIQIU_OWNER_KEY_DIR")
    base = Path(override) if override else Path.home() / ".config" / "xinliqiu"
    return base / "owner.pem"


def generate_owner_key(
    out_dir: Path | str | None = None,
    *,
    bits: int = 2048,
    overwrite: bool = False,
) -> dict:
    """Create an owner key pair and report where the pieces went.

    Refuses to overwrite an existing key unless asked: replacing it makes every
    recording encrypted to the previous key permanently unreadable.
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError as exc:  # pragma: no cover
        raise XinliqiuError(
            "generating a key requires the cryptography package: "
            "pip install xinliqiu-sdk[crypto]"
        ) from exc

    target = Path(out_dir) / "owner.pem" if out_dir else default_owner_key_path()
    spki = target.with_suffix(".spki.txt")

    if target.exists() and not overwrite:
        raise XinliqiuError(
            f"an owner key already exists at {target}.\n"
            "Replacing it would make every recording encrypted to the old key "
            "permanently unreadable. Move it aside first, then rerun."
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    import base64

    _write_private(target, pem)
    target.chmod(0o400)
    spki.write_text(base64.b64encode(der).decode("ascii"), encoding="utf-8")

    return {
        "private_key": str(target),
        "public_key_spki": str(spki),
        "bits": bits,
        "warning": (
            "Back this file up now, somewhere outside this machine. If it is "
            "lost, every recording encrypted to it becomes unreadable forever. "
            "There is no recovery and no reset."
        ),
    }


def verify_owner_key(private_key: Path | str, sample: bytes | None = None) -> dict:
    """Check that a private key is present, readable, and actually usable.

    ``sample`` is an enpt payload; when supplied the key is exercised against
    it, which is the only check that proves the key matches this device.
    """
    path = Path(private_key)
    if not path.exists():
        return {"ok": False, "reason": "missing", "path": str(path)}

    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:  # pragma: no cover
        return {"ok": False, "reason": "cryptography package not installed"}

    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"unreadable: {exc}", "path": str(path)}

    result = {
        "ok": True,
        "path": str(path),
        "bits": getattr(loaded, "key_size", None),
        "mode": oct(path.stat().st_mode & 0o777),
    }
    if sample is not None:
        from .container import decrypt_enpt

        try:
            decrypt_enpt(sample, path)
            result["decrypted_sample"] = True
        except Exception:  # noqa: BLE001
            result["decrypted_sample"] = False
            result["ok"] = False
            result["reason"] = "key does not match this recording"
    return result

def doctor(
    device_id: str,
    peer_id_file: Path | str | None,
    key_file: Path | str | None,
    owner_key: Path | str | None = None,
    *,
    transport: Transport | None = None,
    probe_binary: str | None = None,
) -> dict:
    """Check local prerequisites and report what is missing.

    Never contacts the device beyond a normal authenticated status read, and
    never writes anything.
    """
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    check("device_id", bool(device_id), device_id or "missing")

    for label, path in (
        ("peer_id_file", peer_id_file),
        ("key_file", key_file),
    ):
        if path is None:
            check(label, False, "not provided")
            continue
        p = Path(path)
        exists = p.exists()
        mode = oct(stat.S_IMODE(p.stat().st_mode)) if exists else "-"
        check(label, exists, f"{p} mode={mode}")

    if peer_id_file and Path(peer_id_file).exists():
        content = Path(peer_id_file).read_text(encoding="utf-8").strip()
        check("peer_id_length", len(content) == 36, f"length={len(content)} (expected 36)")

    if key_file and Path(key_file).exists():
        try:
            data = json.loads(Path(key_file).read_text(encoding="utf-8"))
            check("key_file_json", isinstance(data, (dict, list)), "parsed")
        except Exception as exc:  # noqa: BLE001
            check("key_file_json", False, f"not valid JSON: {exc}")

    if owner_key is None:
        check("owner_key", False, "not provided (needed only to decrypt audio)")
    else:
        ok = Path(owner_key).exists()
        check("owner_key", ok, str(owner_key) if ok else "file missing")

    ready = all(c["ok"] for c in checks if c["check"] in {"device_id", "peer_id_file", "key_file", "peer_id_length"})
    return {"ready": ready, "checks": checks}
