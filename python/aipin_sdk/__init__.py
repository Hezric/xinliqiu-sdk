# -*- coding: utf-8 -*-
"""Xinliqiu SDK - talk to an OPPO AI Xinliqiu (心力球) from your own code.

Quick start::

    from aipin_sdk import Xinliqiu, Credentials

    ball = Xinliqiu(Credentials(
        device_id="<CoreBluetooth UUID>",
        peer_id_file="~/xinliqiu/peer-id.txt",
        key_file="~/xinliqiu/ksc-key.json",
    ))
    print(ball.status())
    print(ball.capabilities())

Only ciphertext leaves the device.  Decryption always requires an explicit
owner private key supplied by the caller.
"""

from .audio import OpusLayout, to_ogg, to_wav
from .capabilities import CAPABILITIES, describe
from .client import Credentials, Xinliqiu, RecordStatus, SubprocessTransport, Transport, scan
from .container import Container, Entry, EnptInfo, decrypt_enpt, parse_cmpt, parse_cmpt_file, parse_enpt
from .errors import (
    ContainerError,
    DecryptError,
    XinliqiuError,
    NotAuthenticated,
    ProbeFailed,
    Unsupported,
)
from .provision import (
    Provisioned,
    default_owner_key_path,
    doctor,
    generate_owner_key,
    generate_peer_id,
    install_owner_key,
    pair,
    verify_owner_key,
)
from .session import SessionState
from .storage import CloudflareStorage, LocalStorage, Storage, StoredObject, ingest_container
from .sync import compare, pull_changes, push_range
from .tasks import Task, TaskRegistry

__version__ = "0.1.0"

__all__ = [
    "verify_owner_key",
    "generate_owner_key",
    "default_owner_key_path",
    "scan",
    "Provisioned",
    "doctor",
    "generate_peer_id",
    "install_owner_key",
    "pair",
    "CAPABILITIES",
    "CloudflareStorage",
    "Container",
    "ContainerError",
    "Credentials",
    "DecryptError",
    "Entry",
    "EnptInfo",
    "Xinliqiu",
    "XinliqiuError",
    "LocalStorage",
    "NotAuthenticated",
    "OpusLayout",
    "ProbeFailed",
    "RecordStatus",
    "SessionState",
    "Storage",
    "StoredObject",
    "SubprocessTransport",
    "Task",
    "TaskRegistry",
    "Transport",
    "Unsupported",
    "decrypt_enpt",
    "compare",
    "describe",
    "ingest_container",
    "parse_cmpt",
    "parse_cmpt_file",
    "parse_enpt",
    "pull_changes",
    "push_range",
    "to_ogg",
    "to_wav",
]
