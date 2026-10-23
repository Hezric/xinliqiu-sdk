# -*- coding: utf-8 -*-
"""Pairing: turn a fresh device into one this host can talk to.

The exchange runs over the AFAF service, on characteristics that are only
reached during key establishment:

===== ==================================================================
0001  write: the 12-byte init frame; notify: its 16-byte acknowledgement
1001  write: handshake messages;        notify: the device's replies
1004  write: KSC generation;            notify: the confirmation
===== ==================================================================

Order matters. Sending before the device acknowledges the init frame leaves
the handshake unanswered; writing the handshake to 0001 instead of 1001 does
the same thing silently.

The result is a :class:`~.auth.Pairing`: a shared key, an alias identifying
this host, and both side identifiers. Persist it before the KSC confirmation
is sent - if the process dies between the two, the device has a binding for a
key that was never written down, and the slot is wasted until evicted.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import XinliqiuError
from . import crypto, ukey2
from .auth import Pairing
from .transport import Session, TransportError

CHAR_INIT = "00000001-0000-1000-8000-00805f9b34fb"
CHAR_HANDSHAKE = "00001001-0000-1000-8000-00805f9b34fb"
CHAR_KSC = "00001004-0000-1000-8000-00805f9b34fb"

# Fixed 12 bytes that put the device into pairing mode. The trailing ASCII is
# visible in this frame on the wire.
INIT_FRAME = bytes([0, 0, 0, 0, 0, 0, 5]) + b"WBOAF"

SUBSCRIBE_ORDER = (CHAR_INIT, CHAR_HANDSHAKE, CHAR_KSC)

INIT_ACK_BYTES = 16
PROVIDER_FINISH_BYTES = 38
KSC_CONFIRM_BYTES = 16


class PairingError(XinliqiuError):
    """Pairing failed or the device refused a step."""


@dataclass
class PairStep:
    """Diagnostics for one step, useful when pairing fails."""

    name: str
    sent: int = 0
    received: int = 0


def pair(
    session: Session,
    *,
    alias: bytes | None = None,
    timeout: float = 40.0,
    on_step=None,
) -> tuple[Pairing, list[PairStep]]:
    """Run the full pairing exchange over an open session.

    Returns the pairing material and a step log. The caller is responsible for
    persisting the material before using it.
    """
    if alias is not None and len(alias) != 6:
        raise PairingError("alias must be 6 bytes")

    steps: list[PairStep] = []

    def note(name: str, sent: int = 0, received: int = 0) -> None:
        step = PairStep(name, sent, received)
        steps.append(step)
        if on_step is not None:
            on_step(step)

    # 1. Open the pairing window.
    session.write(CHAR_INIT, INIT_FRAME, timeout)
    ack = session.read(timeout).payload
    if len(ack) != INIT_ACK_BYTES or ack[0] != 1 or ack[5] != 0:
        raise PairingError("device did not accept the pairing init frame")
    note("init_acknowledged", len(INIT_FRAME), len(ack))

    # 2. Key exchange.
    handshake = ukey2.Handshake()
    client_init = handshake.start()
    session.write(CHAR_HANDSHAKE, client_init, timeout)
    server_init = session.read(timeout).payload
    if not server_init or server_init[0] != ukey2.FRAME_SERVER_INIT:
        raise PairingError("device did not answer the key exchange")
    client_finish = handshake.accept_server_init(server_init)
    session.write(CHAR_HANDSHAKE, client_finish, timeout)
    note("handshake", len(client_init) + len(client_finish), len(server_init))

    # 3. The device proves it holds the same key and offers its identity.
    finish = session.read(timeout).payload
    if len(finish) != PROVIDER_FINISH_BYTES:
        raise PairingError("device finish frame has an unexpected length")
    handshake.accept_server_finish(finish)
    provider_id = handshake.provider_id
    if provider_id is None:
        raise PairingError("device finished without an identity")
    note("device_finished", 0, len(finish))

    # 4. Generate the shared key and register it.
    ksc = crypto.random_bytes(16)
    host_alias = alias if alias is not None else crypto.random_bytes(6)
    request = handshake.seal(bytes([0x0C, 0x20]) + ksc + host_alias)
    session.write(CHAR_KSC, request, timeout)

    pairing = Pairing(
        ksc=ksc,
        alias=host_alias,
        seeker_id=handshake.seeker_id,
        provider_id=provider_id,
    )
    note("ksc_sent", len(request), 0)

    # 5. Confirm.
    confirm = session.read(timeout).payload
    if len(confirm) != KSC_CONFIRM_BYTES:
        raise PairingError("key confirmation has an unexpected length")
    plain = handshake.open(confirm)
    if len(plain) < 2 or plain[0] != 0x0D or plain[1] != 0:
        raise PairingError("device rejected the key registration")
    note("ksc_confirmed", 0, len(confirm))

    return pairing, steps