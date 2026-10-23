# -*- coding: utf-8 -*-
"""The client object users interact with.

Everything here runs on this project's own protocol implementation over
``bleak``; there is no external binary to obtain. Each call opens a link, runs
the negotiation and authentication handshake, performs the request, and closes.

Covered: pairing, device status, storage totals, recording control, voiceprint
capture, and the SystemFT file stream that carries recordings off the device.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import XinliqiuError, NotAuthenticated, Unsupported
from .protocol import commands, connect as connect_proto, provision
from .protocol.auth import Pairing
from .protocol.bleak_backend import BleakBackend, scan as scan_devices
from .protocol.transport import BUSINESS_NOTIFY, Session
from .capabilities import CAPABILITIES, describe as describe_capabilities

# Seconds of silence after at least one file before the stream is called done.
# The device pauses for a long time between files - a captured run waited ~15 s
# before the first offer - so a tick-count would stop the sync early.
def _business_payload(reply: bytes) -> bytes:
    """Strip the ``service || command`` envelope a business reply carries."""
    if len(reply) < 2:
        raise XinliqiuError(f"business reply is only {len(reply)} bytes")
    return reply[2:]


IDLE_SECONDS_AFTER_FILES = 25.0

# If the device acknowledged the request but never offered a file, the
# request may simply have been dropped. One re-send after this many quiet
# seconds is enough to tell the two cases apart.
RESEND_AFTER_SECONDS = 8.0


@dataclass(frozen=True)
class Credentials:
    """Everything needed to talk to one already-paired device.

    ``key_file`` is the JSON written by :func:`protocol.provision.pair`: the
    pairing key, the alias it was registered under, and both identifiers. The
    SDK never generates or persists these; the caller owns the file.
    """

    device_id: str
    peer_id_file: Path
    key_file: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "peer_id_file", Path(self.peer_id_file).expanduser())
        object.__setattr__(self, "key_file", Path(self.key_file).expanduser())

    def require_files(self) -> None:
        if not self.peer_id_file.exists():
            raise NotAuthenticated(f"peer id file not found: {self.peer_id_file}")
        if not self.key_file.exists():
            raise NotAuthenticated(f"pairing file not found: {self.key_file}")

    def pairing(self) -> Pairing:
        self.require_files()
        return provision.load_pairing(self.key_file)

    def peer_id(self) -> str:
        self.require_files()
        return provision.validate_peer_id(
            self.peer_id_file.read_text(encoding="utf-8").strip()
        )


@dataclass(frozen=True)
class RecordStatus:
    recording: bool
    battery_percent: int | None
    error_code: int | None
    device_timestamp_ms: int | None
    raw: dict[str, Any]


class Xinliqiu:
    """Synchronous client for one device."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        agent_public_key: Path | None = None,
        owner_private_key: Path | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.credentials = credentials
        self.agent_public_key = Path(agent_public_key) if agent_public_key else None
        self.owner_private_key = Path(owner_private_key) if owner_private_key else None
        self.timeout = timeout

    # -- plumbing --------------------------------------------------------

    def _with_channel(self, worker, *, sync: bool = False):
        """Open a channel, run ``worker``, and always close the link.

        ``sync`` advertises the third local consumer the device requires before
        it will push recordings; ordinary control calls leave it off.
        """
        pairing = self.credentials.pairing()
        peer_id = self.credentials.peer_id()

        backend = BleakBackend()
        try:
            backend.connect(self.credentials.device_id, self.timeout)
            backend.subscribe(BUSINESS_NOTIFY)
            session = Session(backend, self.credentials.device_id, timeout=self.timeout)
            channel = connect_proto.connect(
                session, pairing, peer_id, timeout=self.timeout, sync=sync
            )
            return worker(channel)
        finally:
            backend.disconnect()

    # -- read-only -------------------------------------------------------

    def status(self) -> RecordStatus:
        reply = self._with_channel(lambda c: c.send_command(0x05, 0x02, b"\xFF"))
        parsed = commands.DeviceStatus.parse(reply)
        return RecordStatus(
            recording=parsed.recording,
            battery_percent=parsed.battery_percent,
            error_code=parsed.error_byte,
            device_timestamp_ms=parsed.device_timestamp_ms,
            raw={
                "recordByte": parsed.record_byte,
                "chargeByte": parsed.charge_byte,
                "batteryByte": parsed.battery_percent,
                "errorByte": parsed.error_byte,
                "timestampMs": parsed.device_timestamp_ms,
            },
        )

    def inventory_totals(
        self, start_ms: int = 0, end_ms: int | None = None
    ) -> dict[str, Any]:
        if end_ms is None:
            end_ms = int(time.time() * 1000)
        payload = commands.range_payload(start_ms, end_ms)
        reply = self._with_channel(
            lambda c: c.send_command(
                commands.SERVICE_STORAGE[0], commands.SERVICE_STORAGE[1], payload
            )
        )
        totals = commands.StorageTotals.parse(reply)
        return {
            "count": totals.item_count,
            "bytes": totals.used_bytes,
            "highlight_count": totals.highlight_count,
            "highlight_bytes": totals.highlight_bytes,
            "has_file_list": False,
            "has_cursor": False,
        }

    def storage_info(
        self, start_ms: int = 0, end_ms: int | None = None
    ) -> dict[str, Any]:
        """Usage plus the firmware thresholds, with unknown capacity left null."""
        if end_ms is None:
            end_ms = int(time.time() * 1000)
        totals = self.inventory_totals(start_ms, end_ms)
        return {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "item_count": totals["count"],
            "used_bytes": totals["bytes"],
            "highlight_count": totals["highlight_count"],
            "highlight_bytes": totals["highlight_bytes"],
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
                "exposed. Sync regularly: at 10 MiB free the device deletes the "
                "oldest whole day of recordings on its own."
            ),
        }

    # -- recording -------------------------------------------------------

    def record_start(self) -> dict[str, Any]:
        self._with_channel(lambda c: c.send_only(0x07, 0x00, b"\x01"))
        return {
            "sent": True,
            "note": "audio appears only when voice activity is detected",
        }

    def record_stop(self) -> dict[str, Any]:
        self._with_channel(lambda c: c.send_only(0x07, 0x00, b"\x00"))
        return {"sent": True}

    def collect_voiceprint(self, seconds: int = 6) -> dict[str, Any]:
        """Ask the device to capture one voiceprint sample.

        The device records it into its own storage; the resulting
        ``.vp.opus`` file arrives through :meth:`sync_range` like any other
        recording. There is no separate download path, so this call only
        starts the capture.
        """
        if not 1 <= seconds <= 127:
            raise ValueError("voiceprint duration must be within 1..127 seconds")
        self._with_channel(lambda c: c.send_only(0x07, 0x02, bytes([seconds])))
        return {
            "sent": True,
            "seconds": seconds,
            "note": (
                "Capture started on the device. Collect the file with "
                "sync_range(); it is named *.vp.opus."
            ),
        }

    # -- deliberately absent ---------------------------------------------

    def sync_range(
        self,
        start_ms: int,
        end_ms: int,
        output_dir: Path | None = None,
        *,
        seconds: int = 300,
    ) -> dict[str, Any]:
        """Fetch the recordings the device holds in a device-clock window.

        The device answers by opening a file service and offering files one at
        a time; each is accepted, streamed in plain chunks, MD5-checked when the
        sender supplied a digest, and completed.
        """
        from .protocol import framing, sync as sync_mod

        if seconds < 1:
            raise ValueError("sync window must be at least one second")
        if end_ms <= start_ms:
            raise ValueError("sync range must end after it starts")

        out = Path(output_dir) if output_dir else Path(
            tempfile.mkdtemp(prefix="xinliqiu-sync-")
        )
        out.mkdir(parents=True, exist_ok=True)

        receiver = None

        def worker(channel):
            nonlocal receiver
            # Adopt the service the connection already opened: the device starts
            # offering files immediately instead of re-announcing its channels.
            receiver = sync_mod.SyncReceiver(
                out,
                control_session=channel.system_session_id,
                message_session=channel.session_id,
            )
            try:
                channel.send_only(
                    commands.SERVICE_SYNC[0],
                    commands.SERVICE_SYNC[1],
                    commands.range_payload(start_ms, end_ms),
                )
                deadline = time.monotonic() + seconds
                last_activity = time.monotonic()
                acknowledged = False
                resent = False

                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        got = channel.read_raw(max(0.1, min(remaining, 5.0)))
                    except Exception:  # noqa: BLE001 - a dropped link looks like idle
                        got = None
                    if got is None:
                        # An idle tick is normal: the device may be encoding.
                        # Stop once something arrived and the stream has gone
                        # quiet for long enough that no further offer is plausible.
                        if (
                            receiver.result.files
                            and time.monotonic() - last_activity
                            >= IDLE_SECONDS_AFTER_FILES
                        ):
                            break
                        # It answered the request but never opened a transfer.
                        # A single re-send recovers the case where the first one
                        # was dropped; more than one would just churn the link.
                        if (
                            acknowledged
                            and not receiver.result.setup_seen
                            and not resent
                            and time.monotonic() - last_activity >= RESEND_AFTER_SECONDS
                        ):
                            channel.send_only(
                                commands.SERVICE_SYNC[0],
                                commands.SERVICE_SYNC[1],
                                commands.range_payload(start_ms, end_ms),
                            )
                            resent = True
                            last_activity = time.monotonic()
                        continue
                    last_activity = time.monotonic()
                    session_id, body = got
                    if not body:
                        continue

                    # The message session carries encrypted business frames; the
                    # file service's sessions carry plain bodies (flag 0x00).
                    # A frame there is the device's answer to the range request,
                    # which is what distinguishes "nothing to send" from
                    # "the exchange never started".
                    if session_id == channel.session_id:
                        acknowledged = True
                        continue

                    reply = receiver.feed(session_id, body)
                    if reply is not None:
                        # Answer on the session the frame arrived on: a service
                        # accept belongs on reserved session 1, a setup accept on
                        # the control session.
                        channel.session.write(
                            connect_proto.BUSINESS_WRITE,
                            framing.encode(session_id, reply),
                            channel.session.timeout,
                        )

                    if receiver.finished and receiver.result.files:
                        break
                    if receiver.error:
                        break

                if receiver.error:
                    raise XinliqiuError(receiver.error)

                # No acknowledgement means the range request never landed, so
                # an empty result would be a guess. An acknowledged window with
                # no offer is a real answer: the device holds nothing there.
                if not acknowledged and receiver.result.setup_seen == 0:
                    raise XinliqiuError(
                        "the device never answered the sync request; the link or "
                        "the exchange failed rather than the range being empty"
                    )

                return {
                    "files": [str(p) for p in receiver.result.files],
                    "count": len(receiver.result.files),
                    "bytes": receiver.result.bytes_received,
                    "offers": receiver.result.setup_seen,
                    "rejected": receiver.result.rejected,
                    "_output_dir": str(out),
                }
            finally:
                # Whatever happened - timeout, dropped link, a raised error - no
                # partial transfer may be left open or on disk under its final
                # name. abandon() only touches in-flight files, never completed ones.
                receiver.abandon()

        return self._with_channel(worker, sync=True)

    def sync_ranges(
        self, ranges, output_dir: Path | None = None, *, seconds: int = 600
    ) -> dict[str, Any]:
        pairs = list(ranges)
        if not 2 <= len(pairs) <= 16:
            raise ValueError("sync_ranges expects 2..16 ranges")
        base = Path(output_dir) if output_dir else Path(
            tempfile.mkdtemp(prefix="xinliqiu-sync-")
        )
        merged: dict[str, Any] = {"files": [], "count": 0, "bytes": 0}
        for index, (start, end) in enumerate(pairs):
            part = self.sync_range(
                start, end, base / f"range-{index}", seconds=seconds
            )
            merged["files"].extend(part["files"])
            merged["count"] += part["count"]
            merged["bytes"] += part["bytes"]
        merged["_output_dir"] = str(base)
        return merged

    def init_device(
        self,
        *,
        owner_public_key: Path,
        signing_key: Path,
        account: str = "local-owner",
        public_key_version: int = 1,
        signing_key_is_java_source: bool = False,
    ) -> dict[str, Any]:
        """Format the device and install an owner public key.

        This is the step that decides who can ever read the recordings: the
        device keeps one owner public key, and every recording is encrypted to
        it. It also rewrites the account record, so anything already on the
        device is gone.

        The signing key is supplied by the caller and is never shipped here. On
        a slot-1 device it is the P-256 key recovered from the official app; on
        a slot-2 device no local key works at all, because the device only
        accepts a token signed by the vendor's fixed key.
        """
        from .protocol import init as init_mod

        if not 1 <= public_key_version <= 255:
            raise ValueError("public key version must be 1..255")
        if not 1 <= len(account.encode("utf-8")) <= 127:
            raise ValueError("account must be 1..127 UTF-8 bytes")

        owner = init_mod.OwnerPublicKey.from_file(owner_public_key)
        signer = (
            init_mod.JavaSourceSigner(signing_key)
            if signing_key_is_java_source
            else init_mod.Pkcs8Signer(signing_key)
        )

        def worker(channel):
            # send_command returns service || command || payload, so the first
            # two bytes are the envelope, not the challenge.
            challenge_raw = channel.send_command(0x02, 0x0D, b"\x01")
            challenge = init_mod.parse_bind_challenge(_business_payload(challenge_raw))
            challenge.require_local_slot_one()

            now_ms = int(time.time() * 1000)
            expiry = init_mod.token_expiry_seconds(now_ms)
            token = init_mod.build_token(
                challenge,
                account=account,
                key_hash=owner.key_hash,
                public_key_version=public_key_version,
                signer=signer,
                expiry_seconds=expiry,
            )
            payload = init_mod.build_init_payload(
                timestamp_ms=now_ms,
                account=account,
                public_key_version=public_key_version,
                public_key=owner.utf8,
                token=token,
            )
            response_raw = channel.send_command_large(0x02, 0x08, payload)
            response = init_mod.parse_init_response(
                _business_payload(response_raw),
                expected_account=account,
                expected_public_key=owner.utf8,
                expected_public_key_version=public_key_version,
                framed=False,
            )
            if not response.accepted:
                raise XinliqiuError(
                    f"the device rejected initialisation (status {response.status})"
                )
            return {
                "accepted": True,
                "account": response.account,
                "device_id": challenge.device_id.hex(),
                "key_slot": challenge.key_slot,
                "public_key_version": public_key_version,
                "fingerprint": owner.fingerprint,
            }

        return self._with_channel(worker)

    # -- capability table ------------------------------------------------

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return describe_capabilities()

    @staticmethod
    def require_capability(name: str) -> None:
        for bucket_name, bucket in describe_capabilities()["statuses"].items():
            for item in bucket:
                if item["name"] == name:
                    if bucket_name == "unavailable":
                        raise Unsupported(
                            f"{name} is not reachable on this hardware: {item['note']}"
                        )
                    return
        raise XinliqiuError(f"unknown capability {name}")


def scan(name: str | None = None, seconds: int = 15) -> list[dict[str, Any]]:
    """Discover nearby devices."""
    return [
        {"id": d["id"], "name": d["name"], "rssi": d.get("rssi"), "connectable": True}
        for d in scan_devices(name, float(seconds))
    ]