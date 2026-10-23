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

import base64
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import XinliqiuError
from .protocol.provision import PairResult, load_pairing, pair


@dataclass(frozen=True)
class Provisioned:
    device_id: str
    key_file: Path
    peer_id_file: Path
    paired: bool


# The pairing JSON schema written by protocol.provision.save_pairing. doctor()
# checks these keys instead of merely accepting any JSON value.
_PAIRING_KEYS = (
    "kscHex",
    "kscAliasHex",
    "seekerIdHex",
    "providerDeviceIdHex",
)


def _unlink_quiet(path: Path) -> None:
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        pass


def _stage_file(path: Path, payload: bytes, mode: int) -> Path:
    """Write ``payload`` to ``<path>.tmp`` and return the temporary path.

    The descriptor is made owner-only before any bytes are written, so an
    existing file's wider permissions can never leak through. Nothing visible
    at ``path`` changes until the caller renames the temporary into place.
    """
    tmp = path.with_name(path.name + ".tmp")
    fd = -1
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        return tmp
    except BaseException:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        _unlink_quiet(tmp)
        raise


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _stage_file(path, payload, mode)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink_quiet(tmp)
        raise


def _write_private(path: Path, data: str) -> None:
    """Write ``data`` privately and atomically.

    A crash must never leave a truncated credential behind, and an existing
    file's mode must be narrowed before the first byte (``fchmod``) rather
    than after the write.
    """
    _atomic_write(path, data.encode("utf-8"), 0o600)


def _owner_key_exists(target: Path) -> XinliqiuError:
    return XinliqiuError(
        f"an owner key already exists at {target}.\n"
        "Replacing it would make every recording encrypted to the old key "
        "permanently unreadable. Move it aside first, then rerun."
    )


def generate_peer_id() -> str:
    """Delegate to the protocol implementation.

The device requires exactly 36 characters; an earlier local copy produced 37
and every negotiation was rejected.
"""
    from .protocol.provision import generate_peer_id as _impl

    return _impl()


def pair_device(device_id: str, out_dir: Path, ksc_alias: str | None = None) -> PairResult:
    """Pair a device by identifier and persist the result.

    This is the entry point the CLI uses: it opens the link, runs the pairing
    exchange, writes the credential file, and closes the link.
    """
    from .protocol.bleak_backend import BleakBackend
    from .protocol.pairing import SUBSCRIBE_ORDER
    from .protocol.transport import Session

    # Validate the remembered alias before touching the radio: a corrupted
    # config value should not cost a connection attempt or leak a ValueError.
    alias = _alias_bytes(ksc_alias)
    backend = BleakBackend()
    try:
        backend.connect(device_id, 30.0)
        for characteristic in SUBSCRIBE_ORDER:
            backend.subscribe(characteristic)
        session = Session(backend, device_id)
        return pair(session, out_dir=Path(out_dir).expanduser(), alias=alias)
    finally:
        backend.disconnect()


def _alias_bytes(ksc_alias: str | None) -> bytes | None:
    if not ksc_alias:
        return None
    try:
        return bytes.fromhex(ksc_alias)
    except (TypeError, ValueError) as exc:
        raise XinliqiuError(
            f"saved KSC alias {ksc_alias!r} is not valid hexadecimal; "
            "re-pair to replace it"
        ) from exc


def default_owner_key_path() -> Path:
    """Where the owner key lives by default.

    A dotfile under the user's home directory, not the working directory: a
    key that is lost with a `cd` or a deleted checkout is a key that takes
    every recording with it.
    """
    override = os.environ.get("XINLIQIU_OWNER_KEY_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".config" / "xinliqiu"
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

    target = (
        Path(out_dir).expanduser() / "owner.pem"
        if out_dir
        else default_owner_key_path()
    )
    spki = target.with_suffix(".spki.txt")

    if not overwrite and target.exists():
        raise _owner_key_exists(target)

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
    target.parent.mkdir(parents=True, exist_ok=True)

    old_spki: bytes | None = None
    if spki.exists():
        try:
            old_spki = spki.read_bytes()
        except OSError:
            old_spki = None

    # Claim the destination before writing anything visible. O_EXCL closes the
    # check-then-write race: two concurrent keygens cannot both replace the
    # same key. --force deliberately skips the claim.
    claimed = False
    if not overwrite:
        try:
            fd = os.open(
                str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError as exc:
            raise _owner_key_exists(target) from exc
        try:
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        except BaseException:
            _unlink_quiet(target)
            raise
        claimed = True

    key_tmp: Path | None = None
    spki_tmp: Path | None = None
    spki_replaced = False
    try:
        # The public half goes first: whichever step fails, a private key is
        # never left behind without its matching .spki.txt.
        spki_tmp = _stage_file(spki, base64.b64encode(der), 0o644)
        # Stage the private half already at 0400: once it replaces the
        # destination there is no later chmod that could fail and leave a
        # half-finished pair.
        key_tmp = _stage_file(target, pem.encode("ascii"), 0o400)
        os.replace(spki_tmp, spki)
        spki_tmp = None
        spki_replaced = True
        os.replace(key_tmp, target)
        key_tmp = None
    except BaseException:
        for tmp in (key_tmp, spki_tmp):
            if tmp is not None:
                _unlink_quiet(tmp)
        if spki_replaced:
            if old_spki is None:
                _unlink_quiet(spki)
            else:
                try:
                    _atomic_write(spki, old_spki, 0o644)
                except OSError:
                    pass
        if claimed:
            _unlink_quiet(target)
        raise

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
    path = Path(private_key).expanduser()
    if not path.exists():
        return {"ok": False, "reason": "missing", "path": str(path)}

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        return {"ok": False, "reason": f"unreadable: {exc}", "path": str(path)}

    if mode & 0o077:
        return {
            "ok": False,
            "reason": (
                f"private key permissions are too open ({oct(mode)}); "
                "chmod 600 it"
            ),
            "path": str(path),
            "mode": oct(mode),
        }

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
        "mode": oct(mode),
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
) -> dict:
    """Check local prerequisites and report what is missing.

    Never contacts the device beyond a normal authenticated status read, and
    never writes anything.
    """
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    check("device_id", bool(device_id), device_id or "missing")

    peer_path = Path(peer_id_file).expanduser() if peer_id_file else None
    key_path = Path(key_file).expanduser() if key_file else None
    owner_path = Path(owner_key).expanduser() if owner_key else None

    for label, path in (
        ("peer_id_file", peer_path),
        ("key_file", key_path),
    ):
        if path is None:
            check(label, False, "not provided")
            continue
        exists = path.exists()
        mode = oct(stat.S_IMODE(path.stat().st_mode)) if exists else "-"
        check(label, exists, f"{path} mode={mode}")

    if peer_path and peer_path.exists():
        content = peer_path.read_text(encoding="utf-8").strip()
        check("peer_id_length", len(content) == 36, f"length={len(content)} (expected 36)")

    if key_path and key_path.exists():
        try:
            data = json.loads(key_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            check("key_file_json", False, f"not valid JSON: {exc}")
        else:
            if not isinstance(data, dict):
                check("key_file_json", False, "not a JSON object")
            else:
                missing = [name for name in _PAIRING_KEYS if name not in data]
                if missing:
                    check(
                        "key_file_json",
                        False,
                        "missing keys: " + ", ".join(missing),
                    )
                else:
                    # Presence alone is not enough: the same hex/length checks
                    # that load_pairing applies must pass, or the credential
                    # cannot authenticate and must not report ready.
                    try:
                        load_pairing(key_path)
                    except Exception as exc:  # noqa: BLE001
                        check(
                            "key_file_json",
                            False,
                            f"not a usable pairing file: {exc}",
                        )
                    else:
                        check("key_file_json", True, "parsed")

    if owner_path is None:
        check("owner_key", False, "not provided (needed only to decrypt audio)")
    else:
        ok = owner_path.exists()
        check("owner_key", ok, str(owner_path) if ok else "file missing")

    required = {
        "device_id",
        "peer_id_file",
        "key_file",
        "peer_id_length",
        "key_file_json",
    }
    if owner_path is not None:
        required.add("owner_key")
    ready = all(c["ok"] for c in checks if c["check"] in required)
    return {"ready": ready, "checks": checks}
