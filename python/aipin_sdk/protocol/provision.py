# -*- coding: utf-8 -*-
"""Provisioning built on the native protocol stack.

This is the entry point users interact with. It pairs a device, stores the
result, and reports what happened - without shelling out to an external binary.

The key-registration step (`init`) is deliberately absent: it needs signing
material that this project does not and will not derive from a vendor artifact.
Users who need it follow the documented manual path.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..errors import XinliqiuError
from . import pairing as pairing_proto
from .auth import Pairing
from .transport import Session


class ProvisionError(XinliqiuError):
    """Provisioning could not be completed."""


@dataclass
class PairResult:
    pairing: Pairing
    key_file: Path
    peer_id_file: Path
    steps: list[tuple[str, int, int]]


def _write_private(path: Path, data: str) -> None:
    """Write credential material privately and atomically.

    A pairing file must never be left truncated by a crash, and an existing
    file's mode must be narrowed before the first byte rather than after the
    write. The visible path changes only when the complete file is in place.
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = -1
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(str(tmp))
        except FileNotFoundError:
            pass
        raise


PEER_ID_PREFIX = "OAFP_"
PEER_ID_LENGTH = 36


def generate_peer_id() -> str:
    """A stable 36-character identifier for this host.

    The device requires exactly 36 printable ASCII bytes with the OAFP_ prefix,
    and rejects anything else during negotiation. The value appears in the
    negotiation answer; it is an identifier, not a secret.
    """
    import secrets

    tail = PEER_ID_LENGTH - len(PEER_ID_PREFIX)
    return PEER_ID_PREFIX + secrets.token_hex(32)[:tail]


def validate_peer_id(peer_id: str) -> str:
    """Return the peer id unchanged, or explain why the device would reject it."""
    if len(peer_id) != PEER_ID_LENGTH:
        raise ProvisionError(
            f"peer id must be {PEER_ID_LENGTH} characters, got {len(peer_id)}"
        )
    if not peer_id.startswith(PEER_ID_PREFIX):
        raise ProvisionError(f"peer id must start with {PEER_ID_PREFIX}")
    if not all(32 <= ord(c) < 127 for c in peer_id):
        raise ProvisionError("peer id must be printable ASCII")
    return peer_id


def save_pairing(pairing: Pairing, path: Path) -> None:
    """Persist pairing material as JSON with owner-only permissions.

    The field names match the credential shape used by the device tooling
    (``kscAliasHex`` / ``providerDeviceIdHex``), which is also what `doctor`
    validates before calling the file usable.
    """
    payload = {
        "schemaVersion": 1,
        "kscHex": pairing.ksc.hex(),
        "kscAliasHex": pairing.alias.hex(),
        "seekerIdHex": pairing.seeker_id.hex(),
        "providerDeviceIdHex": pairing.provider_id.hex(),
    }
    _write_private(path, json.dumps(payload, indent=2) + "\n")


def load_pairing(path: Path) -> Pairing:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProvisionError(f"no pairing material at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProvisionError(f"pairing material at {path} is not valid JSON") from exc

    if not isinstance(data, dict):
        raise ProvisionError(f"pairing material at {path} is not a JSON object")

    # Older SDK builds wrote ``aliasHex`` / ``providerIdHex``. Keep reading
    # those files so an upgrade does not orphan an existing pairing, but always
    # write the canonical names.
    fields = {
        "kscHex": data.get("kscHex"),
        "kscAliasHex": data.get("kscAliasHex", data.get("aliasHex")),
        "seekerIdHex": data.get("seekerIdHex"),
        "providerDeviceIdHex": data.get(
            "providerDeviceIdHex", data.get("providerIdHex")
        ),
    }
    missing = [
        name for name, value in fields.items()
        if not isinstance(value, str) or not value
    ]
    if missing:
        raise ProvisionError(
            "pairing material is missing " + ", ".join(missing)
        )

    try:
        return Pairing(
            ksc=bytes.fromhex(fields["kscHex"]),
            alias=bytes.fromhex(fields["kscAliasHex"]),
            seeker_id=bytes.fromhex(fields["seekerIdHex"]),
            provider_id=bytes.fromhex(fields["providerDeviceIdHex"]),
        )
    except (TypeError, ValueError) as exc:
        raise ProvisionError(f"pairing material is malformed: {exc}") from exc


def pair(session: Session, *, out_dir: Path, alias: bytes | None = None) -> PairResult:
    """Pair an already-connected device and persist the result.

    The material is written before this returns, so a crash cannot leave the
    device bound to a key nobody recorded.
    """
    out = Path(out_dir)
    key_file = out / "pairing.json"
    peer_file = out / "peer-id.txt"

    if key_file.exists():
        raise ProvisionError(
            f"{key_file} already exists. Move it aside first: overwriting pairing "
            "material orphan the recordings encrypted to the previous key."
        )

    pairing, steps = pairing_proto.pair(session, alias=alias)
    save_pairing(pairing, key_file)

    if not peer_file.exists():
        _write_private(peer_file, generate_peer_id() + "\n")

    return PairResult(
        pairing=pairing,
        key_file=key_file,
        peer_id_file=peer_file,
        steps=[(s.name, s.sent, s.received) for s in steps],
    )