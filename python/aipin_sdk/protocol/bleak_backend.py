# -*- coding: utf-8 -*-
"""Bluetooth backend built on ``bleak``.

``bleak`` covers macOS (CoreBluetooth), Linux (BlueZ) and Windows (WinRT), so
one adapter serves all three platforms. There is no platform-specific code in
this project as a result.

``bleak`` is asynchronous while the protocol layer is synchronous, so this
adapter owns a private event loop on a background thread and marshals every
call onto it. Notifications are handed back through a plain thread-safe queue,
deliberately not an ``asyncio.Queue``: those bind to a loop at construction and
cannot be created from another thread.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any

from ..errors import XinliqiuError
from .transport import BUSINESS_SERVICE, Notification, TransportError

try:  # pragma: no cover - import guard
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover
    BleakClient = None  # type: ignore[assignment]
    BleakScanner = None  # type: ignore[assignment]


def _require_bleak() -> None:
    if BleakClient is None:
        raise XinliqiuError(
            "the bluetooth backend needs the bleak package: "
            'pip install "xinliqiu-sdk[ble]"'
        )


def _expand(uuid: str) -> str:
    """Accept the short 4-character form or a full 128-bit UUID."""
    text = uuid.strip()
    return text if len(text) > 8 else f"0000{text.lower()}-0000-1000-8000-00805f9b34fb"


class BleakBackend:
    """A synchronous facade over ``bleak``'s asynchronous client."""

    def __init__(self) -> None:
        _require_bleak()
        self._inbox: queue.Queue[bytes] = queue.Queue()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="xinliqiu-ble", daemon=True
        )
        self._thread.start()
        self._client: Any = None
        self._notify_uuid: str | None = None
        self._properties: dict[str, set[str]] = {}
        self._closed = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro: Any) -> Any:
        if self._closed:
            raise TransportError("backend is closed")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    # -- lifecycle -------------------------------------------------------

    def connect(self, device_id: str, timeout: float) -> None:
        self._run(self._connect(device_id, timeout))

    async def _connect(self, device_id: str, timeout: float) -> None:
        client = BleakClient(device_id, timeout=timeout)
        await client.connect()
        self._client = client
        # Remember how each characteristic may be written. The device offers
        # AA17 as write-without-response only and the pairing characteristics as
        # write-with-response only, so a fixed choice fails on one of them with
        # "writing is not permitted".
        for service in client.services:
            for char in service.characteristics:
                self._properties[char.uuid.lower()] = set(char.properties or ())

    def disconnect(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._client.disconnect(), self._loop
                ).result(timeout=5)
            except Exception:  # noqa: BLE001 - closing must not raise
                pass
            self._client = None
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def __enter__(self) -> "BleakBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    # -- io --------------------------------------------------------------

    def write(self, characteristic: str, payload: bytes, timeout: float) -> None:
        if self._client is None:
            raise TransportError("write before connect")
        self._run(self._write(characteristic, payload, timeout))

    async def _write(self, characteristic: str, payload: bytes, timeout: float) -> None:
        target = _expand(characteristic)
        props = self._properties.get(target, set())
        # Prefer an acknowledged write when the characteristic allows one.
        if not props:
            response = True
        else:
            response = "write" in props
            if not response and "write-without-response" not in props:
                raise TransportError(
                    f"{characteristic} accepts no writes (properties: {sorted(props)})"
                )
        await asyncio.wait_for(
            self._client.write_gatt_char(target, payload, response=response),
            timeout,
        )

    def subscribe(self, characteristic: str) -> None:
        if self._client is None:
            raise TransportError("subscribe before connect")
        target = _expand(characteristic)

        def handler(_sender: Any, data: bytearray) -> None:
            # Runs on the loop thread; the queue is thread-safe by design.
            self._inbox.put(bytes(data))

        async def subscribe() -> None:
            await self._client.start_notify(target, handler)

        try:
            self._run(subscribe())
            self._notify_uuid = target
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"cannot subscribe to {characteristic}: {exc}") from exc

    def read_notification(self, timeout: float) -> Notification:
        try:
            payload = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise TransportError("no notification within the timeout") from None
        return Notification(BUSINESS_SERVICE, self._notify_uuid or "", payload)


async def discover(name: str | None = None, seconds: float = 10.0) -> list[dict[str, Any]]:
    """List nearby devices, optionally filtered by advertised name."""
    _require_bleak()
    found = await BleakScanner.discover(timeout=seconds)
    devices = []
    for device in found:
        label = getattr(device, "name", None) or ""
        if name and name.lower() not in label.lower():
            continue
        devices.append(
            {
                "id": getattr(device, "address", ""),
                "name": label,
                "rssi": getattr(device, "rssi", None),
            }
        )
    return devices


def scan(name: str | None = None, seconds: float = 10.0) -> list[dict[str, Any]]:
    """Blocking wrapper around :func:`discover`."""
    return asyncio.run(discover(name, seconds))