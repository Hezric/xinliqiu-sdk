# -*- coding: utf-8 -*-
"""Driving a recording sync: request a range, receive the files it produces.

The device answers a sync request by opening a file service and offering files
one at a time. This module owns the state machine; it never touches Bluetooth
itself, so it can be exercised against recorded frames.

Frame routing, matching the reference implementation:

=====================  =========================================================
reserved session 1     service request (type 1) / accept (type 2); the sender
                       declares its control channel and data channels here
control channel        JSON messages: setup-req, complete-rsp, appalive, cancel
data channel           ``0x00`` plain flag, ``0x01`` chunk header, then bytes
=====================  =========================================================

Everything else is ignored rather than treated as an error: the device emits
events this receiver has no opinion about, and failing on them would abort a
transfer that is going fine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import XinliqiuError
from . import systemft

# Service negotiation on the reserved session.
SERVICE_REQUEST_TYPE = 1
SERVICE_ACCEPT_TYPE = 2

# Channel kinds declared by the sender.
CONTROL_CHANNEL = 100
DATA_CHANNELS_LOW = 101
DATA_CHANNELS_HIGH = 105
MAX_CHANNELS = 6
PROFILE_TERMINATOR = 0x3B
PROFILE_NAME = "system:filetransfer"

# Resource policy: one broken or hostile offer must not fill the output
# directory, pin an unbounded number of handles, or make the receiver spend
# minutes writing something no recording could be. 64 MiB is far above the
# largest container the device produces and still small enough to refuse
# outright.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_INFLIGHT = 4

# How many colliding names are tried before an offer is rejected. Bounded so a
# directory full of planted names cannot spin the receiver.
MAX_NAME_ATTEMPTS = 100

# How many rejections are kept for diagnostics. A device that keeps offering
# unusable files must not grow the result without limit.
MAX_REJECTED_RECORDED = 20

# Suffixes whose meaning lives in the name: ``container.Entry.kind`` classifies
# with ``endswith(".end.enc.opus")`` / ``endswith(".enc.opus")`` / ``.tp``.
# Splitting one of those to insert a counter (``a.end.enc-2.opus``)
# reclassifies the file, so the counter goes before the whole suffix and the
# classification survives.
_RENAME_SUFFIXES = (".end.enc.opus", ".enc.note.opus", ".enc.opus", ".tp")


def _split_rename(name: str) -> tuple[str, str]:
    """Split ``name`` so a ``-N`` counter can go between stem and suffix.

    A known classification suffix is preserved whole: ``a.end.enc.opus``
    becomes ``a-2.end.enc.opus``, which still classifies as an end marker.
    Otherwise the counter goes before the last extension only when the stem has
    no further dot; a compound name such as ``a.b.c`` keeps every extension and
    takes the counter after the whole name (``a.b.c-2``), because inventing a
    split inside it would invent a different extension.
    """
    lowered = name.lower()
    for suffix in _RENAME_SUFFIXES:
        if len(name) > len(suffix) and lowered.endswith(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    stem, suffix = os.path.splitext(name)
    if suffix and "." not in stem.lstrip("."):
        return stem, suffix
    return name, ""


class SyncError(XinliqiuError):
    """A sync frame could not be interpreted."""


@dataclass
class ServiceChannels:
    """What the sender declared: which session carries which channel."""

    sessions: dict[int, int]  # session id -> channel id
    control_session: int
    data_channels: set[int]

    @property
    def data_sessions(self) -> set[int]:
        return {s for s, c in self.sessions.items() if c in self.data_channels}


@dataclass
class SyncResult:
    files: list[Path] = field(default_factory=list)
    bytes_received: int = 0
    setup_seen: int = 0
    rejected: list[tuple[int, int]] = field(default_factory=list)
    rejected_total: int = 0  # every refusal, even past the recorded window
    anomalies: int = 0  # data frames dropped because their owner was ambiguous

    def add(self, path: Path, size: int) -> None:
        self.files.append(path)
        self.bytes_received += size

    def reject(self, trans_id: int, reason: int) -> None:
        """Record a refusal, keeping only the first few for diagnostics."""
        self.rejected_total += 1
        if len(self.rejected) < MAX_REJECTED_RECORDED:
            self.rejected.append((trans_id, reason))


class SyncReceiver:
    """State machine for one sync window.

    Feed frames with :meth:`feed`; it returns the bytes that must be written
    back to the device (control replies), which the caller sends. A return of
    ``None`` means nothing to send.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        control_session: int | None = None,
        message_session: int | None = None,
    ) -> None:
        """``control_session`` seeds a service the connection already opened.

        The device does not always wait for a fresh type-1 handshake: when the
        ``system:filetransfer`` service came up during the connection handshake
        it starts offering files straight away, and a receiver that insisted on
        a type-1 first would ignore every offer.
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.message_session = message_session
        self.channels: ServiceChannels | None = (
            ServiceChannels(sessions={}, control_session=control_session,
                            data_channels=set())
            if control_session is not None
            else None
        )
        # Data sessions are learned: the device opens one per file and never
        # announces which session carries which channel. Once learned the
        # binding is authoritative; re-deriving it per frame is what used to
        # attribute a chunk to the wrong file (O3).
        self.session_channels: dict[int, int] = {}
        self.service_acknowledged = False
        # Bytes the device sent after the fields this parser understands.
        self.trailing_bytes = b""
        self.files: dict[int, systemft.IncomingFile] = {}
        self.completed: dict[int, Path] = {}
        self.result = SyncResult()
        self.finished = False
        self.error: str | None = None

    # -- service negotiation ---------------------------------------------

    def accept_service_request(self, body: bytes) -> bytes | None:
        """Parse a type-1 request and build the type-2 accept.

        Field order is sender-relative and NOT symmetric between the two
        messages (the protocol):

        ====== ==========================================================
        type 1  ``1 | remote u16 | local u16 | profile ; | count u16 |
                sessions u16*  | channels u16* | qos(type u8, class u8)* |
                payload u8*``
        type 2  ``2 | initiator u16 | acceptor u16 | profile ; | 00 |
                count u16 | sessions u16*``
        ====== ==========================================================

        The profile is UTF-8 terminated by ``0x3b`` rather than length
        prefixed, and the accept swaps the two agent fields and carries a
        reserved zero byte before the count.
        """
        if not body or body[0] != SERVICE_REQUEST_TYPE:
            return None

        def u16(offset: int) -> int:
            # Short slices otherwise decode as a smaller number: a truncated
            # body would silently become a plausible channel list.
            if offset < 0 or offset + 2 > len(body):
                raise SyncError(
                    f"service request ends mid-field at offset {offset}"
                )
            return int.from_bytes(body[offset : offset + 2], "big")

        try:
            offset = 1
            remote = u16(offset)
            offset += 2
            local = u16(offset)
            offset += 2

            end_of_profile = body.index(PROFILE_TERMINATOR, offset)
            profile = body[offset:end_of_profile].decode("ascii")
            offset = end_of_profile + 1

            count = u16(offset)
            offset += 2
            if not 1 <= count <= MAX_CHANNELS:
                raise SyncError(f"service request declares {count} channels")

            sessions = [u16(offset + 2 * i) for i in range(count)]
            offset += 2 * count
            channels = [u16(offset + 2 * i) for i in range(count)]
            offset += 2 * count
            offset += 2 * count  # qos type/class pairs
            offset += count  # payload types
            if offset > len(body):
                raise SyncError("service request is shorter than it declares")
            # The device appends bytes this parser does not model (the sender keeps
            # them as `trailingBytes` and proceeds), so extra bytes are recorded
            # rather than treated as a different message. A short frame is still
            # fatal: the channel declaration would be fabricated.
            self.trailing_bytes = body[offset:]
        except (IndexError, ValueError) as exc:
            raise SyncError(f"malformed service request: {exc}") from exc

        session_map = dict(zip(sessions, channels))
        data_channels = {c for c in channels if c != CONTROL_CHANNEL}
        if CONTROL_CHANNEL not in channels:
            raise SyncError("service request declares no control channel")

        if profile != PROFILE_NAME:
            raise SyncError(
                f"service request is for {profile!r}, not {PROFILE_NAME!r}"
            )

        declared_control = sessions[channels.index(CONTROL_CHANNEL)]
        if self.channels is None or not self.channels.sessions:
            self.channels = ServiceChannels(
                sessions=session_map,
                control_session=declared_control,
                data_channels=data_channels,
            )
        else:
            # Merge, but trust the device's declaration over the session this
            # receiver adopted earlier: the device is the one that will send on
            # the control channel, so its choice is the one that must be read.
            merged = dict(self.channels.sessions)
            merged.update(session_map)
            self.channels = ServiceChannels(
                sessions=merged,
                control_session=declared_control,
                data_channels=self.channels.data_channels | data_channels,
            )
        self.service_acknowledged = True

        out = bytearray([SERVICE_ACCEPT_TYPE])
        out += local.to_bytes(2, "big")  # initiator first on the way back
        out += remote.to_bytes(2, "big")  # then acceptor
        out += profile.encode("ascii") + bytes([PROFILE_TERMINATOR])
        out.append(0)  # reserved byte the acceptor always writes
        out += len(sessions).to_bytes(2, "big")
        for session in sessions:
            out += session.to_bytes(2, "big")
        return bytes(out)

    # -- frame intake -----------------------------------------------------

    def feed(self, session_id: int, body: bytes) -> bytes | None:
        """Handle one complete session body; return control bytes to send."""
        if body[:1] == bytes([SERVICE_REQUEST_TYPE]):
            # Always answer a service request, even if this receiver already
            # adopted an open service. The device's initiator does not send
            # anything else until the matching type-2 arrives, so swallowing it
            # stalls the whole sync.
            return self.accept_service_request(body)
        if self.channels is None:
            return None

        if session_id == self.channels.control_session:
            return self._control(body)

        if self.message_session is not None and session_id == self.message_session:
            return None  # business traffic, not this service

        channel = self.channels.sessions.get(session_id)
        if channel is None:
            # A data session the service did not announce. Read the binding we
            # learned earlier before looking for a new one; a stale binding is
            # dropped only when its transaction is gone.
            channel = self.session_channels.get(session_id)
            if channel is not None and not self._channel_in_flight(channel):
                self.session_channels.pop(session_id, None)
                channel = None
        if channel is None:
            if body[:1] != bytes([systemft.PLAIN_CHANNEL_FLAG]):
                return None
            channel = self._learn_channel(session_id)
            if channel is None:
                return None
        if channel == CONTROL_CHANNEL:
            # The declaration maps this session to the control channel. Its
            # frames are JSON, and dropping them as "not a data channel" stalled
            # the whole sync after the service was accepted.
            return self._control(body)
        if self.channels.data_channels and channel not in self.channels.data_channels:
            return None  # a channel this service does not own
        return self._data(channel, body)

    def _channel_in_flight(self, channel: int) -> bool:
        return any(f.request.channel == channel for f in self.files.values())

    def _learn_channel(self, session_id: int) -> int | None:
        """Bind an unannounced data session to the transaction it carries.

        The device opens a fresh session per file but never says which channel
        it carries. It also never interleaves two files' chunks, so exactly one
        transfer in flight makes the owner unambiguous. With two in flight a
        guess would write one file's bytes into the other - silent corruption -
        so the frame is dropped and the anomaly counted instead.
        """
        if len(self.files) != 1:
            if self.files:
                self.result.anomalies += 1
            return None
        channel = next(iter(self.files.values())).request.channel
        self.session_channels[session_id] = channel
        return channel

    def _forget_bindings(self, channel: int) -> None:
        """Forget learned sessions for a channel whose transaction ended.

        The device may reuse the session for a later file with a different
        channel; keeping the stale binding would send those bytes to a
        transaction that no longer exists.
        """
        stale = [s for s, bound in self.session_channels.items() if bound == channel]
        for session in stale:
            self.session_channels.pop(session, None)

    def _control(self, body: bytes) -> bytes | None:
        if not body or body[0] != systemft.PLAIN_CHANNEL_FLAG:
            return None
        payload = body[1:]
        if not payload:
            return None
        try:
            msg = systemft.message_id(payload)
        except systemft.FileTransferError:
            return None

        if msg == systemft.MSG_SETUP_REQ:
            return self._setup(payload)
        if msg == systemft.MSG_COMPLETE_RSP:
            try:
                response = systemft.CompleteResponse.from_json(payload)
            except (ValueError, TypeError, systemft.FileTransferError):
                # Malformed JSON is ignored. Raising here would abort a sync
                # that is otherwise healthy because of one bad control frame.
                return None
            if response.result == 0:
                self.finished = True
            else:
                self.error = (
                    f"device rejected completion of transaction {response.trans_id} "
                    f"(reason {response.reason})"
                )
            return None
        if msg == systemft.MSG_APPALIVE_REQ:
            try:
                trans_id = int(systemft._decode_json(payload).get("transId", 0))
            except (ValueError, TypeError, systemft.FileTransferError):
                return None
            return systemft.encode_appalive_response(trans_id)
        if msg == systemft.MSG_CANCEL_REQ:
            try:
                obj = systemft._decode_json(payload)
                trans_id = int(obj.get("transId", -1))
                reason = int(obj.get("reason", 0))
            except (ValueError, TypeError, systemft.FileTransferError):
                return None
            pending = self.files.pop(trans_id, None)
            if pending is None:
                return None  # a stale cancel for a file we already finished
            pending.abandon()
            self._forget_bindings(pending.request.channel)
            return systemft.encode_cancel_response(trans_id, reason)
        return None  # progress/window/setup-rsp and anything new

    def _setup(self, payload: bytes) -> bytes | None:
        """Validate and admit one offer.

        A malformed offer must be answered, never raised: one bad frame would
        otherwise abort a sync that is otherwise fine. Offers that cannot even
        be attributed to a transaction are dropped.
        """
        try:
            request = systemft.SetupRequest.from_json(payload)
        except (ValueError, TypeError, systemft.FileTransferError):
            return self._reject_unparseable(payload)

        self.result.setup_seen += 1
        try:
            reason = self._admission_reason(request)
        except (ValueError, TypeError, systemft.FileTransferError):
            return self._reject(request, systemft.REASON_SINK_FAILED)
        if reason is not None:
            return self._reject(request, reason)

        try:
            name = Path(request.file_name).name
            if not name or name in {".", ".."}:
                # ``Path`` leaves these intact, and both would resolve to a
                # directory rather than a file.
                name = f"file-{request.trans_id}.bin"
            incoming = self._reserve_file(name, request)
        except (ValueError, TypeError, systemft.FileTransferError, OSError):
            incoming = None
        if incoming is None:
            # No writable name, exhausted candidates, or an unusable sink:
            # refuse this offer so the device moves on.
            return self._reject(request, systemft.REASON_SINK_FAILED)

        self.files[request.trans_id] = incoming
        return systemft.encode_setup_accept(request)

    def _admission_reason(self, request: systemft.SetupRequest) -> int | None:
        """Why this offer must be refused, or ``None`` to accept it."""
        channel = request.channel  # raises FileTransferError when not numeric
        if channel == CONTROL_CHANNEL:
            # The control channel carries JSON; file bytes there would collide
            # with the message stream.
            return systemft.REASON_BAD_CHANNEL
        declared = self.channels.data_channels
        if declared and channel not in declared:
            return systemft.REASON_BAD_CHANNEL
        if request.trans_id in self.files:
            return systemft.REASON_DUPLICATE
        if request.file_size < 0:
            return systemft.REASON_SINK_FAILED
        if request.file_size > MAX_FILE_BYTES or len(self.files) >= MAX_INFLIGHT:
            return systemft.REASON_POLICY
        return None

    def _reject(self, request: systemft.SetupRequest, reason: int) -> bytes:
        self.result.reject(request.trans_id, reason)
        return systemft.encode_setup_reject(request, reason)

    def _reject_unparseable(self, payload: bytes) -> bytes | None:
        """Answer an offer that did not parse, when it still names a transaction.

        There is no :class:`systemft.SetupRequest` to build a reply from, so
        the transaction id and name are read defensively; without a usable id
        there is nothing to address and the frame is dropped.
        """
        try:
            obj = systemft._decode_json(payload)
        except systemft.FileTransferError:
            return None
        trans_id = obj.get("transId")
        if isinstance(trans_id, bool) or not isinstance(trans_id, int):
            return None
        file_name = obj.get("fileName")
        if not isinstance(file_name, str):
            file_name = ""
        else:
            try:
                file_name.encode("utf-8")
            except UnicodeEncodeError:
                file_name = ""
        self.result.reject(trans_id, systemft.REASON_SINK_FAILED)
        return systemft.encode_setup_reject_fields(
            trans_id, systemft.REASON_SINK_FAILED, file_name
        )

    # -- name reservation --------------------------------------------------

    def _candidates(self, name: str):
        """Every name a file may take, bounded by ``MAX_NAME_ATTEMPTS``."""
        yield self.output_dir / name
        stem, suffix = _split_rename(name)
        for index in range(2, MAX_NAME_ATTEMPTS + 1):
            yield self.output_dir / f"{stem}-{index}{suffix}"

    def _unique_path(
        self, name: str, skip: frozenset[Path] = frozenset()
    ) -> Path | None:
        """A name no file, directory or dangling symlink occupies.

        ``Path.exists`` follows links, so a dangling symlink looks free while a
        later ``open`` would create its target outside ``output_dir``.
        ``os.path.lexists`` counts the link itself. A staging file left by an
        interrupted run also blocks the name: the exclusive open would refuse
        it anyway. ``skip`` carries names a lost ``O_EXCL`` race already ruled
        out. ``None`` means every candidate is taken and the caller must reject
        the offer, not raise.
        """
        for candidate in self._candidates(name):
            if candidate in skip:
                continue
            if os.path.lexists(candidate):
                continue
            if os.path.lexists(systemft.part_path_for(candidate)):
                continue
            return candidate
        return None

    def _reserve_file(
        self, name: str, request: systemft.SetupRequest
    ) -> systemft.IncomingFile | None:
        """Claim a candidate name atomically, moving on when it is taken.

        ``_unique_path`` only proposes a name; ``IncomingFile.open`` claims it
        with ``O_EXCL``. Losing that claim (a racing writer) must try the next
        candidate rather than fail the transfer. ``skip`` guarantees a lost
        race moves forward even if the winner's entry has already vanished.
        ``None`` means the names are exhausted or the sink is unusable; the
        caller rejects the setup.
        """
        lost_races: frozenset[Path] = frozenset()
        for _attempt in range(MAX_NAME_ATTEMPTS):
            candidate = self._unique_path(name, lost_races)
            if candidate is None:
                return None
            incoming = systemft.IncomingFile(request=request, path=candidate)
            try:
                incoming.open()
            except FileExistsError:
                # Someone else claimed this name: try the next candidate, even
                # if their entry is already gone again.
                lost_races = lost_races | {candidate}
                continue
            except OSError:
                incoming.abandon()
                return None
            return incoming
        return None

    # -- data --------------------------------------------------------------

    def _owner_for_channel(self, channel: int) -> int | None:
        """The transaction currently receiving on ``channel``.

        A guess is safe only when exactly one file is in flight. With two, an
        unmatched channel is ambiguous, and attributing it to any single file
        corrupts that file, so the frame is dropped and counted as an anomaly.
        """
        owners = [t for t, f in self.files.items() if f.request.channel == channel]
        if len(owners) == 1:
            return owners[0]
        if not owners and len(self.files) == 1:
            return next(iter(self.files))
        if self.files:
            self.result.anomalies += 1
        return None

    def _data(self, channel: int, body: bytes) -> bytes | None:
        if not body or body[0] != systemft.PLAIN_CHANNEL_FLAG:
            return None
        try:
            chunk = systemft.decode_chunk(body[1:])
        except systemft.FileTransferError:
            return None

        owner = self._owner_for_channel(channel)
        if owner is None:
            return None

        incoming = self.files[owner]
        try:
            incoming.write(chunk)
        except (systemft.FileTransferError, OSError) as exc:
            # ENOSPC/EIO included: abandoning closes the handle and removes the
            # staging bytes, so a failed write cannot leak either.
            incoming.abandon()
            self.files.pop(owner, None)
            self._forget_bindings(incoming.request.channel)
            self.error = f"receiving {incoming.request.file_name}: {exc}"
            return None

        if not incoming.complete:
            return None

        try:
            incoming.finish()
        except (systemft.FileTransferError, OSError) as exc:
            incoming.abandon()
            self.files.pop(owner, None)
            self._forget_bindings(incoming.request.channel)
            self.error = str(exc)
            return None

        self.files.pop(owner, None)
        self._forget_bindings(incoming.request.channel)
        self.completed[owner] = incoming.path
        self.result.add(incoming.path, incoming.request.file_size)
        return systemft.encode_complete_request(owner, incoming.request.file_name)

    # -- teardown ---------------------------------------------------------

    def abandon(self) -> None:
        for incoming in self.files.values():
            incoming.abandon()
        self.files.clear()
        self.session_channels.clear()