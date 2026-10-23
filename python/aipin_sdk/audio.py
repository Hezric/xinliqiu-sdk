# -*- coding: utf-8 -*-
"""Opus packet handling and WAV export.

Recordings are stored as fixed-size raw Opus packets, 20 ms each, mono, 16 kHz
source bandwidth.  The container does not carry a native sample count, so the
decoded duration is always ``packet_count * 20 ms``; that is a packet-count
duration, not the original microphone duration.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import oggopus
from .errors import ContainerError

DEFAULT_PACKET_BYTES = 40
DEFAULT_PACKET_MS = 20
DEFAULT_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class OpusLayout:
    packet_bytes: int = DEFAULT_PACKET_BYTES
    packet_ms: int = DEFAULT_PACKET_MS
    sample_rate: int = DEFAULT_SAMPLE_RATE

    def packet_count(self, payload: bytes) -> int:
        if len(payload) % self.packet_bytes:
            raise ContainerError(
                f"payload {len(payload)} bytes is not a multiple of {self.packet_bytes}"
            )
        return len(payload) // self.packet_bytes

    def duration_seconds(self, payload: bytes) -> float:
        return self.packet_count(payload) * self.packet_ms / 1000.0


def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ContainerError("ffmpeg is required for Opus decoding")
    return exe


def to_ogg(payload: bytes, layout: OpusLayout | None = None, *, pre_skip: int = 0) -> bytes:
    """Wrap raw Opus packets into an Ogg/Opus stream."""
    layout = layout or OpusLayout()
    layout.packet_count(payload)  # validates alignment
    return oggopus.encode(
        payload,
        packet_bytes=layout.packet_bytes,
        pre_skip=pre_skip,
        sample_rate=layout.sample_rate,
    )


def to_wav(payload: bytes, layout: OpusLayout | None = None, *, pre_skip: int = 0) -> bytes:
    """Decode raw Opus packets to a 16-bit mono WAV."""
    layout = layout or OpusLayout()
    ffmpeg = _require_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "in.ogg"
        out = Path(tmp) / "out.wav"
        raw.write_bytes(to_ogg(payload, layout, pre_skip=pre_skip))
        proc = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(raw),
                "-c:a", "pcm_s16le", "-ar", str(layout.sample_rate), "-ac", "1",
                str(out),
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0 or not out.exists():
            raise ContainerError(
                "ffmpeg WAV decode failed: " + proc.stderr.decode("utf-8", "replace")[-500:]
            )
        return out.read_bytes()
