# -*- coding: utf-8 -*-
"""Minimal Ogg/Opus muxer for fixed-size raw Opus packets.

Produces a spec-compliant Ogg page stream so that any decoder (ffmpeg, libopus,
browsers) can consume it.  No external dependencies.
"""

from __future__ import annotations

import struct

OPUS_HEAD_MAGIC = b"OpusHead"
OPUS_TAGS_MAGIC = b"OpusTags"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_PRE_SKIP = 0
MAX_SEGMENTS = 255


def _crc32(data: bytes) -> int:
    """Ogg uses CRC-32 with polynomial 0x04C11DB7, no reflection, init 0."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def _page(segments: list[bytes], *, serial: int, seq: int, granule: int, header_type: int, continued: bool, final: bool) -> bytes:
    lacing = []
    body = bytearray()
    for seg in segments:
        remaining = len(seg)
        while remaining >= 255:
            lacing.append(255)
            remaining -= 255
        lacing.append(remaining)
        body.extend(seg)
    if continued:
        header_type |= 0x01
    if final:
        header_type |= 0x04
    if not lacing:
        lacing = [0]
    # Ogg page header: magic, version, type, granule, serial, sequence, crc, segments, table
    header = struct.pack(
        "<4sBBQIIIB",
        b"OggS",
        0,
        header_type,
        granule & 0xFFFFFFFFFFFFFFFF,
        serial & 0xFFFFFFFF,
        seq & 0xFFFFFFFF,
        0,
        len(lacing),
    )
    page = header + bytes(lacing) + bytes(body)
    crc = _crc32(page)
    return page[:22] + struct.pack("<I", crc) + page[26:]


def _opus_head(channels: int, pre_skip: int, sample_rate: int, output_gain: int, mapping: bytes, serial: int) -> bytes:
    return struct.pack(
        "<8sBBHIhB",
        OPUS_HEAD_MAGIC,
        1,
        channels,
        pre_skip,
        sample_rate,
        output_gain,
        0,
    ) + mapping


def encode(payload: bytes, *, packet_bytes: int = 40, pre_skip: int = 0, serial: int = 0x48425401, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """Mux fixed-size Opus packets into an Ogg stream (mono)."""
    if packet_bytes <= 0:
        raise ValueError("packet_bytes must be positive")
    if len(payload) % packet_bytes:
        raise ValueError(f"payload {len(payload)} not a multiple of {packet_bytes}")
    packets = [payload[i : i + packet_bytes] for i in range(0, len(payload), packet_bytes)]
    if not packets:
        raise ValueError("empty payload")

    out = bytearray()
    seq = 0
    out += _page([_opus_head(1, pre_skip, sample_rate, 0, b"\x00", serial)], serial=serial, seq=seq, granule=0, header_type=0x02, continued=False, final=False)
    seq += 1
    out += _page([OPUS_TAGS_MAGIC + struct.pack("<I", 0)], serial=serial, seq=seq, granule=0, header_type=0, continued=False, final=False)
    seq += 1

    granule = 0
    index = 0
    while index < len(packets):
        chunk = packets[index : index + MAX_SEGMENTS]
        granule += len(chunk) * (sample_rate * 20 // 1000)
        is_last = index + len(chunk) >= len(packets)
        out += _page(
            chunk,
            serial=serial,
            seq=seq,
            granule=granule,
            header_type=0,
            continued=False,
            final=is_last,
        )
        seq += 1
        index += len(chunk)
    return bytes(out)
