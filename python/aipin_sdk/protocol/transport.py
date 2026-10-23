# -*- coding: utf-8 -*-
"""Platform-neutral Bluetooth transport.

Everything above this layer speaks bytes. Everything below it knows how to
reach a device. Keeping the split hard is what lets the protocol run on macOS,
Linux, or a test harness without changing a line of protocol code.

A backend is anything that can:

* connect to a device and disclose the GATT services it offers;
* subscribe to notifications on a characteristic;
* write a characteristic and wait for the acknowledgement.

Nothing here knows what the bytes mean.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from ..errors import XinliqiuError


# The device exposes two service sets: one for the key exchange handshake and
# one for everything afterwards. These UUIDs are advertised publicly and are
# observable in any Bluetooth scanner.
PAIRING_SERVICE = "AFAF"
PAIRING_CHARACTERISTICS = ("0001", "0002", "0003")

# On AA15 the device exposes exactly two characteristics: AA16 delivers
# notifications and accepts no writes, AA17 accepts writes and delivers no
# notifications. Writing to the wrong one produces silence, not an error.
BUSINESS_SERVICE = "AA15"
BUSINESS_NOTIFY = "AA16"
BUSINESS_WRITE = "AA17"


class TransportError(XinliqiuError):
    """A link-level failure: unreachable, disconnected, or timed out."""


@dataclass
class Notification:
    service: str
    characteristic: str
    payload: bytes
    at: float = field(default_factory=time.time)


@runtime_checkable
class Backend(Protocol):
    """What a platform adapter must provide.

    Implementations are expected to be single-use: connect once, use, close.
    The protocol assumes one link at a time.
    """

    def connect(self, device_id: str, timeout: float) -> None:
        """Open a link and wait until services are discovered."""

    def disconnect(self) -> None:
        """Close the link. Safe to call more than once."""

    def write(self, characteristic: str, payload: bytes, timeout: float) -> None:
        """Write and wait for the write acknowledgement."""

    def subscribe(self, characteristic: str) -> None:
        """Begin receiving notifications on a characteristic."""

    def read_notification(self, timeout: float) -> Notification:
        """Block until one notification arrives or the timeout expires."""


class Session:
    """A live link with notification buffering and reassembly helpers.

    The device fragments payloads across notifications. Callers that need one
    logical message call :meth:`read_message`; callers that want raw frames
    call :meth:`read`.
    """

    def __init__(self, backend: Backend, device_id: str, *, timeout: float = 45.0) -> None:
        self.backend = backend
        self.device_id = device_id
        self.timeout = timeout
        self._pending: queue.Queue[Notification] = queue.Queue()
        self._closed = False
        self._thread: threading.Thread | None = None

    def open(self) -> "Session":
        self.backend.connect(self.device_id, self.timeout)
        for uuid in PAIRING_CHARACTERISTICS:
            try:
                self.backend.subscribe(uuid)
            except TransportError:
                # Only the current mode's characteristics are subscribable;
                # the other set is not present yet.
                continue
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.backend.disconnect()
        except Exception:  # noqa: BLE001 - closing must never raise
            pass

    def __enter__(self) -> "Session":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def write(self, characteristic: str, payload: bytes, timeout: float | None = None) -> None:
        self.backend.write(
            characteristic, payload, self.timeout if timeout is None else timeout
        )

    def read(self, timeout: float | None = None) -> Notification:
        return self.backend.read_notification(
            self.timeout if timeout is None else timeout
        )

    def read_message(self, *, timeout: float | None = None, max_bytes: int = 1 << 20) -> bytes:
        """Read notifications until :class:`Framer` reports a complete frame."""
        from .framing import Framer

        framer = Framer()
        # Framer takes the limit as an attribute, not a constructor arg;
        # passing it here raised TypeError and made read_message unusable.
        framer.max_bytes = max_bytes
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            note = self.read(max(0.1, deadline - time.time()))
            complete = framer.feed(note.payload)
            if complete is not None:
                return complete
        raise TransportError("timed out waiting for a complete frame")


class LoopbackBackend:
    """In-memory backend for tests.

    Feed it the notifications a real device would send, assert on what was
    written. This is what makes the protocol testable without hardware.
    """

    def __init__(self, *, responses: Sequence[bytes] | None = None) -> None:
        self.written: list[tuple[str, bytes]] = []
        self.connected = False
        self._queue: queue.Queue[Notification] = queue.Queue()
        self._subscribed: set[str] = set()
        for payload in responses or ():
            self._queue.put(Notification("", "", payload))

    def connect(self, device_id: str, timeout: float) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def write(self, characteristic: str, payload: bytes, timeout: float) -> None:
        if not self.connected:
            raise TransportError("write before connect")
        self.written.append((characteristic, payload))

    def subscribe(self, characteristic: str) -> None:
        self._subscribed.add(characteristic)

    def read_notification(self, timeout: float) -> Notification:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            raise TransportError("no notification") from None

    def push(self, payload: bytes) -> None:
        """Queue a notification a real device would have sent."""
        self._queue.put(Notification("", "", payload))