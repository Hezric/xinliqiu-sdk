# -*- coding: utf-8 -*-
"""CMPT container parsing and enpt payload decryption.

CMPT layout (little-endian):

    0x00  "CMPT"
    0x04  u16 entry_count
    0x06  u16 data_offset
    0x08  u32 declared_length
    0x0C  entry table, entry_count records of 68 bytes:
              64 bytes NUL-padded UTF-8 name, then u32 payload_length

Entry payloads follow the table contiguously in table order.

enpt envelope:

    0x000  "enpt"
    0x004  u16 wrapped_key_length   (256 for RSA-2048)
    0x006  u16 nonce_length         (12)
    0x008  RSA-2048 PKCS#1 v1.5 wrapped 32-byte AES key
    0x108  12-byte GCM nonce
    0x114  u16 0x0118
    0x116  u8  public key version
    0x117  u8  reserved
    0x118  ciphertext ...
    end-24 16-byte GCM tag | u16 16 | u16 24 | "enpt"
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import ContainerError, DecryptError

CMPT_MAGIC = b"CMPT"
ENPT_MAGIC = b"enpt"
ENTRY_RECORD = 68
ENPT_HEADER = 0x118
ENPT_TRAILER = 24
ENPT_WRAPPED_KEY = 256
ENPT_NONCE = 12


@dataclass(frozen=True)
class Entry:
    index: int
    name: str
    size: int
    payload: bytes

    @property
    def kind(self) -> str:
        n = self.name.lower()
        if n.endswith(".end.enc.opus"):
            return "end-marker"
        if n.endswith(".enc.opus"):
            return "audio"
        if n.endswith(".enc.note.opus") or ".enc.note." in n:
            return "note-audio"
        if n.endswith(".tp"):
            return "trace-point"
        return "unknown"


@dataclass(frozen=True)
class Container:
    path: Path | None
    declared_length: int
    entries: list[Entry]

    def by_kind(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]


@dataclass(frozen=True)
class EnptInfo:
    wrapped_key_length: int
    nonce_length: int
    public_key_version: int
    ciphertext: bytes
    tag: bytes
    nonce: bytes
    wrapped_key: bytes

    @property
    def plaintext_length(self) -> int:
        return len(self.ciphertext)


def parse_cmpt(data: bytes, path: Path | None = None) -> Container:
    if len(data) < 12 or data[:4] != CMPT_MAGIC:
        raise ContainerError(f"not a CMPT container: {path or '<bytes>'}")
    count, data_off, declared = struct.unpack_from("<HHI", data, 4)
    if declared != len(data):
        raise ContainerError(
            f"declared length {declared} does not match actual length {len(data)}"
        )
    if 12 + count * ENTRY_RECORD > len(data):
        raise ContainerError("entry table exceeds buffer")
    table_end = 12 + count * ENTRY_RECORD
    if not table_end <= data_off <= len(data):
        raise ContainerError(
            f"data_offset {data_off} outside valid range {table_end}..{len(data)}"
        )
    entries: list[Entry] = []
    offset = data_off
    for i in range(count):
        rec = 12 + i * ENTRY_RECORD
        raw_name = data[rec : rec + 64].split(b"\x00", 1)[0]
        name = raw_name.decode("utf-8", "replace")
        (size,) = struct.unpack_from("<I", data, rec + 64)
        payload = data[offset : offset + size]
        if len(payload) != size:
            raise ContainerError(f"entry {i} truncated: want {size}, got {len(payload)}")
        entries.append(Entry(index=i, name=name, size=size, payload=payload))
        offset += size
    return Container(path=path, declared_length=declared, entries=entries)


def parse_cmpt_file(path: Path) -> Container:
    return parse_cmpt(Path(path).read_bytes(), Path(path))


def parse_enpt(data: bytes) -> EnptInfo:
    if len(data) < ENPT_HEADER + ENPT_TRAILER:
        raise ContainerError("enpt payload too short")
    if data[:4] != ENPT_MAGIC:
        raise ContainerError("not an enpt payload")
    key_len, nonce_len = struct.unpack_from("<HH", data, 4)
    if key_len != ENPT_WRAPPED_KEY or nonce_len != ENPT_NONCE:
        raise ContainerError(
            f"unexpected enpt wrapped key/nonce length {key_len}/{nonce_len} "
            f"(expected {ENPT_WRAPPED_KEY}/{ENPT_NONCE})"
        )
    cursor = 8
    wrapped = data[cursor : cursor + key_len]
    cursor += key_len
    nonce = data[cursor : cursor + nonce_len]
    cursor += nonce_len
    cursor += 2  # u16 0x0118
    if cursor + 2 > len(data):
        raise ContainerError("enpt header truncated before the public key version")
    version = data[cursor]
    cursor += 2  # version + reserved
    if cursor != ENPT_HEADER:
        raise ContainerError(f"unexpected enpt header length {cursor}")
    trailer = data[-ENPT_TRAILER:]
    if trailer[20:24] != ENPT_MAGIC:
        raise ContainerError("enpt trailer magic mismatch")
    tag = trailer[:16]
    ciphertext = data[ENPT_HEADER:-ENPT_TRAILER]
    return EnptInfo(
        wrapped_key_length=key_len,
        nonce_length=nonce_len,
        public_key_version=version,
        ciphertext=ciphertext,
        tag=tag,
        nonce=nonce,
        wrapped_key=wrapped,
    )


def decrypt_enpt(data: bytes, private_key_pem: bytes | Path | str) -> bytes:
    """Unwrap the AES key with RSA-OAEP-less PKCS#1 v1.5 and verify GCM.

    Raises ContainerError for a malformed enpt envelope and DecryptError when
    the key cannot be loaded, does not match, or the tag fails.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError as exc:  # pragma: no cover
        raise ContainerError(
            "decryption requires the 'cryptography' package: pip install xinliqiu-sdk[crypto]"
        ) from exc

    info = parse_enpt(data)
    try:
        pem = private_key_pem
        if isinstance(pem, Path):
            pem = pem.read_bytes()
        elif isinstance(pem, str):
            pem = pem.encode("utf-8")
        key = load_pem_private_key(pem, password=None)
    except Exception as exc:  # noqa: BLE001 - a bad/unsupported key is a decrypt failure
        raise DecryptError(
            "could not load the owner private key; expected an unencrypted PEM key"
        ) from exc
    try:
        aes_key = key.decrypt(info.wrapped_key, padding.PKCS1v15())
    except Exception as exc:  # noqa: BLE001 - surface any RSA failure uniformly
        raise DecryptError("RSA unwrap failed: private key does not match this payload") from exc
    try:
        return AESGCM(aes_key).decrypt(info.nonce, info.ciphertext + info.tag, None)
    except Exception as exc:  # noqa: BLE001
        raise DecryptError("AES-GCM authentication failed") from exc
