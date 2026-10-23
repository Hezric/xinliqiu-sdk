# -*- coding: utf-8 -*-
"""Xinliqiu client.

The transport layer is deliberately pluggable: by default it shells out to the
`ble-probe` binary produced by the reference implementation.  Users on other
platforms can supply their own `Transport` implementation without touching any
protocol code in this package.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .capabilities import CAPABILITIES, describe as describe_capabilities
from .errors import XinliqiuError, NotAuthenticated, ProbeFailed, Unsupported


def _secure_temp_dir(prefix: str) -> Path:
    """mkdtemp with 0700 enforced, which the reference transport requires."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(path, 0o700)
    return path


@dataclass(frozen=True)
class Credentials:
    """Everything needed to talk to one already-paired device.

    The SDK never generates or persists these values.  Callers own them.
    """

    device_id: str
    peer_id_file: Path
    key_file: Path

    def __post_init__(self) -> None:
        # Resolve to absolute paths. The reference transport validates the key
        # file against a recorded absolute path, so a relative path silently
        # fails with "invalid_confirmed_key".
        object.__setattr__(self, "peer_id_file", Path(self.peer_id_file).expanduser().resolve())
        object.__setattr__(self, "key_file", Path(self.key_file).expanduser().resolve())

    def require_files(self) -> None:
        for path in (self.peer_id_file, self.key_file):
            if not path.exists():
                raise NotAuthenticated(f"credential file missing: {path}")


@dataclass
class RecordStatus:
    recording: bool
    battery_percent: int | None
    error_code: int | None
    device_timestamp_ms: int | None
    raw: dict[str, Any] = field(default_factory=dict)


class Transport:
    """Minimal transport contract.

    Implementations return the decoded JSON Lines emitted by the probe as a list
    of dicts.  The first dict carrying an ``event == "finished"`` entry is used
    as the terminal result.
    """

    def run(self, argv: Sequence[str], timeout: float) -> list[dict[str, Any]]:
        raise NotImplementedError


class SubprocessTransport(Transport):
    """Runs the reference `ble-probe` executable."""

    def __init__(self, binary: str | os.PathLike[str] | None = None) -> None:
        if binary is None:
            binary = os.environ.get("XINLIQIU_PROBE") or shutil.which("ble-probe")
        if not binary:
            raise XinliqiuError(
                "ble-probe not found. Set XINLIQIU_PROBE or pass transport=..."
            )
        self.binary = str(binary)

    def run(self, argv: Sequence[str], timeout: float) -> list[dict[str, Any]]:
        proc = subprocess.run(
            [self.binary, *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        events: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        finished = next((e for e in events if e.get("event") == "finished"), None)
        if finished is None:
            raise ProbeFailed(
                "probe produced no terminal event",
                argv=list(argv),
                returncode=proc.returncode,
                stderr=proc.stderr[-2000:],
            )
        if finished.get("exitCode") not in (0, 3):
            raise ProbeFailed(
                f"probe exit {finished.get('exitCode')}: {finished.get('reason')}",
                argv=list(argv),
                returncode=proc.returncode,
                reason=finished.get("reason"),
            )
        return events


def scan(
    name: str | None = None,
    seconds: int = 15,
    *,
    transport: Transport | None = None,
    probe_binary: str | None = None,
) -> list[dict[str, Any]]:
    """Discover nearby devices.

    Returns the advertisement events the transport observed. Each entry carries
    at least ``id`` (the Bluetooth identifier to use as ``device_id``).
    """
    transport = transport or SubprocessTransport(probe_binary)
    argv = ["scan"]
    if name:
        argv += ["--name", name]
    else:
        argv += ["--all"]
    argv += ["--seconds", str(seconds)]
    events = transport.run(argv, float(seconds) + 30)
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "advertisement":
            continue
        ident = event.get("id")
        if not ident:
            continue
        prior = seen.get(ident)
        if prior is None or (event.get("rssi") or -999) > (prior.get("rssi") or -999):
            seen[ident] = {
                "id": ident,
                "name": event.get("cachedName") or event.get("name"),
                "rssi": event.get("rssi"),
                "connectable": bool(event.get("connectable")),
            }
    return sorted(seen.values(), key=lambda d: d.get("rssi") or -999, reverse=True)


class Xinliqiu:
    """Synchronous client for one xinliqiu device."""

    def __init__(
        self,
        credentials: Credentials,
        transport: Transport | None = None,
        *,
        agent_public_key: Path | None = None,
        owner_private_key: Path | None = None,
        default_timeout: float = 60.0,
    ) -> None:
        self.credentials = credentials
        self.transport = transport or SubprocessTransport()
        self.agent_public_key = Path(agent_public_key) if agent_public_key else None
        self.owner_private_key = Path(owner_private_key) if owner_private_key else None
        self.default_timeout = default_timeout

    # -- helpers ---------------------------------------------------------

    def _base(self, op: str, capture: Path) -> list[str]:
        argv = [
            op,
            "--id", self.credentials.device_id,
            "--peer-id-file", str(self.credentials.peer_id_file),
            "--key-file", str(self.credentials.key_file),
            "--capture", str(capture),
        ]
        if self.agent_public_key:
            argv += ["--agent-public-key", str(self.agent_public_key)]
        return argv

    def _run(self, argv: list[str], timeout: float | None = None) -> dict[str, Any]:
        self.credentials.require_files()
        events = self.transport.run(argv, timeout or self.default_timeout)
        return next(e for e in events if e.get("event") == "finished")

    # -- read-only -------------------------------------------------------

    def status(self) -> RecordStatus:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "status.jsonl"
            argv = self._base("record-status", capture) + ["--seconds", "45"]
            result = self._run(argv)
        record_byte = result.get("recordByte")
        return RecordStatus(
            recording=record_byte not in (0, -1, None),
            battery_percent=result.get("batteryByte"),
            error_code=result.get("errorByte"),
            device_timestamp_ms=result.get("timestampMs"),
            raw=result,
        )

    def inventory_totals(self, start_ms: int = 0, end_ms: int = 9_999_999_999_999) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "storage.jsonl"
            argv = self._base("storage-info", capture) + [
                "--start-ms", str(start_ms),
                "--end-ms", str(end_ms),
                "--seconds", "45",
            ]
            result = self._run(argv)
        info = result.get("storageInfo") or {}
        return {
            "count": int(info.get("recordAndVoiceInputCount", 0)),
            "bytes": int(info.get("recordAndVoiceInputBytes", 0)),
            "highlight_count": int(info.get("highlightCount", 0)),
            "highlight_bytes": int(info.get("highlightBytes", 0)),
            "has_file_list": bool(info.get("fileListPresent")),
            "has_cursor": bool(info.get("cursorPresent")),
        }

    # -- recording -------------------------------------------------------

    def record_start(self) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "start.jsonl"
            argv = self._base("record-start", capture) + ["--seconds", "45"]
            return self._run(argv)

    def record_stop(self) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "stop.jsonl"
            argv = self._base("record-stop", capture) + ["--seconds", "45"]
            return self._run(argv)

    # -- capture ---------------------------------------------------------

    def collect_voiceprint(
        self,
        seconds: int = 6,
        host_window: int | None = None,
        output_dir: Path | None = None,
    ) -> dict[str, Any]:
        if not 1 <= seconds <= 127:
            raise ValueError("voiceprint duration must be within 1..127 seconds")
        window = host_window or max(45, seconds + 39)
        out = Path(output_dir) if output_dir else _secure_temp_dir("xinliqiu-vp-")
        self._secure_dir(out)
        if any(out.iterdir()):
            raise XinliqiuError(
                f"voiceprint output directory must be empty: {out}"
            )
        # Keep the capture log outside the output directory, which the
        # transport requires to stay empty until files land there.
        capture = _secure_temp_dir("xinliqiu-cap-") / "capture.jsonl"
        argv = self._base("collect-voiceprint", capture) + [
            "--output-dir", str(out),
            "--duration", str(seconds),
            "--seconds", str(window),
        ]
        result = self._run(argv, timeout=window + 30)
        result["_output_dir"] = str(out)
        return result

    def storage_info(
        self,
        start_ms: int = 0,
        end_ms: int | None = None,
    ) -> dict[str, Any]:
        """Report how much the device holds, plus the thresholds that matter.

        The device reports usage only. It never exposes total or free capacity,
        so ``free_bytes`` and ``total_bytes`` are ``None`` rather than guessed.

        What can be stated concretely are the firmware's own low-space actions,
        which are fixed constants in the reference build:

        - ``<= 50 MiB`` free raises ALMOST_FULL;
        - ``<= 10 MiB`` free starts deleting the oldest whole day;
        - below ``5 MiB`` free it refuses to create new segments.

        Because total capacity is unknown, ``used_bytes`` cannot be placed
        against those thresholds, so ``pressure`` stays ``unknown``.
        """
        if end_ms is None:
            end_ms = int(time.time() * 1000) - 1000
        self._validate_range(start_ms, end_ms)
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "storage.jsonl"
            argv = self._base("storage-info", capture) + [
                "--start-ms", str(start_ms),
                "--end-ms", str(end_ms),
                "--seconds", "45",
            ]
            result = self._run(argv)

        info = result.get("storageInfo") or {}

        def num(key: str) -> int | None:
            value = info.get(key)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        used = num("recordAndVoiceInputBytes")
        return {
            "start_ms": num("startMs"),
            "end_ms": num("endMs"),
            "item_count": num("recordAndVoiceInputCount"),
            "used_bytes": used,
            "highlight_count": num("highlightCount"),
            "highlight_bytes": num("highlightBytes"),
            "free_bytes": None,
            "total_bytes": None,
            "pressure": "unknown",
            "thresholds": {
                "almost_full_below_free_bytes": 50 * 1024 * 1024,
                "delete_oldest_below_free_bytes": 10 * 1024 * 1024,
                "create_refused_below_free_bytes": 5 * 1024 * 1024,
            },
            "note": (
                "The device reports usage only; total and free capacity are not "
                "exposed, so space pressure cannot be computed on this firmware. "
                "Sync regularly: at 10 MiB free the device deletes the oldest whole "
                "day of recordings on its own."
            ),
        }

    @staticmethod
    def _validate_range(start_ms: int, end_ms: int) -> None:
        """Reject ranges the device would silently wait on.

        The device only emits its end-of-range report once the end time has
        passed. A future end time therefore turns into a long silent wait that
        looks like a hang, so it is refused up front.
        """
        if start_ms < 0:
            raise ValueError("start_ms must be >= 0")
        if end_ms <= start_ms:
            raise ValueError(f"end_ms ({end_ms}) must be greater than start_ms ({start_ms})")
        now_ms = int(time.time() * 1000)
        # Allow a small margin for clock skew between host and device.
        if end_ms > now_ms + 60_000:
            raise ValueError(
                f"end_ms ({end_ms}) is in the future (now {now_ms}). "
                "The device only reports range completion after the end time passes, "
                "so this call would appear to hang. Pass a past time."
            )

    @staticmethod
    def _secure_dir(path: Path) -> Path:
        """Create a directory the transport will accept.

        The reference transport refuses output directories that are readable by
        group or other, so every directory the SDK hands it is created 0700.
        """
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _prepare_sync_dir(self, output_dir: Path | None) -> tuple[Path, Path]:
        out = Path(output_dir) if output_dir else _secure_temp_dir("xinliqiu-sync-")
        self._secure_dir(out)
        if any(out.iterdir()):
            raise XinliqiuError(
                f"sync output directory must be empty: {out}. "
                "Use a fresh directory per run, or omit output_dir."
            )
        # The capture log lives outside the output directory, which the
        # transport requires to stay empty until files land there.
        capture = _secure_temp_dir("xinliqiu-cap-") / "capture.jsonl"
        return out, capture

    def sync_range(
        self,
        start_ms: int,
        end_ms: int,
        output_dir: Path | None = None,
        *,
        seconds: int = 300,
    ) -> dict[str, Any]:
        self._validate_range(start_ms, end_ms)
        out, capture = self._prepare_sync_dir(output_dir)
        argv = self._base("sync-recordings", capture) + [
            "--start-ms", str(start_ms),
            "--end-ms", str(end_ms),
            "--output-dir", str(out),
            "--seconds", str(seconds),
        ]
        result = self._run(argv, timeout=seconds + 60)
        result["_output_dir"] = str(out)
        return result

    def sync_ranges(
        self,
        ranges: Iterable[tuple[int, int]],
        output_dir: Path | None = None,
        *,
        seconds: int = 600,
    ) -> dict[str, Any]:
        pairs = list(ranges)
        if not 2 <= len(pairs) <= 16:
            raise ValueError("sync_ranges expects 2..16 ranges")
        out, capture = self._prepare_sync_dir(output_dir)
        spec = ";".join(f"{a}:{b}" for a, b in pairs)
        argv = self._base("sync-recordings", capture) + [
            "--ranges", spec,
            "--output-dir", str(out),
            "--seconds", str(seconds),
        ]
        result = self._run(argv, timeout=seconds + 60)
        result["_output_dir"] = str(out)
        return result

    # -- capabilities ----------------------------------------------------

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return describe_capabilities()

    @staticmethod
    def require_capability(name: str) -> None:
        entry = CAPABILITIES.get(name)
        if entry is None:
            raise Unsupported(f"unknown capability: {name}")
        if entry["status"] == "unavailable":
            raise Unsupported(f"{name}: {entry['note']}")
