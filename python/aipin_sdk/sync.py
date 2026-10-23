# -*- coding: utf-8 -*-
"""Device-to-store replication and store-to-client pull.

Two distinct operations, often confused:

* ``push_range``  - ask the device for a time range and upload ciphertext.
* ``pull_changes`` - read what another client already uploaded.

A client never needs a private key to participate in either. Decryption is a
separate, explicit step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .client import Credentials, Xinliqiu
from .container import Container, parse_cmpt_file
from .storage import CloudflareStorage, StoredObject, ingest_container


class RemoteStore(Protocol):
    def put_entry(self, device_id: str, entry) -> dict: ...
    def list(self, device_id: str, start_ms: int, end_ms: int) -> list[StoredObject]: ...
    def get(self, object_key: str) -> bytes: ...


class IncrementalStore(RemoteStore, Protocol):
    def changes(self, device_id: str, since: int = 0, limit: int = 500) -> dict: ...
    def digest(self, device_id: str, start_ms: int = 0, end_ms: int = 9_999_999_999_999) -> dict: ...


def push_range(
    ball: Xinliqiu,
    store: RemoteStore,
    device_id: str,
    start_ms: int,
    end_ms: int,
    *,
    output_dir: Path | None = None,
    seconds: int = 300,
) -> dict:
    """Fetch a device range and upload every CMPT entry as ciphertext."""
    result = ball.sync_range(start_ms, end_ms, output_dir, seconds=seconds)
    out_dir = Path(result["_output_dir"])
    totals = {"stored": 0, "duplicates": 0, "total": 0, "containers": 0}
    skipped = 0
    for path in sorted(out_dir.glob("*.bin")):
        try:
            container: Container = parse_cmpt_file(path)
        except Exception:  # noqa: BLE001 - non-CMPT diagnostic files
            skipped += 1
            continue
        counts = ingest_container(container, store, device_id)
        totals["containers"] += 1
        for key in ("stored", "duplicates", "total"):
            totals[key] += counts[key]
    return {
        "device_sync": {
            "files_completed": result.get("syncFilesCompleted"),
            "files_failed": result.get("syncFilesFailed"),
            "range_outcome": result.get("syncRangeOutcome"),
        },
        "upload": totals,
        "skipped_non_cmpt": skipped,
        "output_dir": str(out_dir),
    }


def pull_changes(
    store: IncrementalStore,
    device_id: str,
    *,
    since: int = 0,
    limit: int = 500,
    download: bool = False,
    local_dir: Path | None = None,
) -> dict:
    """Read what a store holds beyond a cursor.

    With ``download=True`` each object is fetched and optionally written under
    ``local_dir`` as ``<object_key>.bin``. Content stays encrypted.
    """
    page = store.changes(device_id, since=since, limit=limit)
    written = []
    if download and local_dir is not None:
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        for obj in page["objects"]:
            data = store.get(obj.object_key)
            target = local_dir / (obj.object_key.replace("/", "_") + ".bin")
            if not target.exists():
                target.write_bytes(data)
                written.append(str(target))
    return {
        "count": len(page["objects"]),
        "cursor": page["cursor"],
        "has_more": page["has_more"],
        "objects": [
            {
                "object_key": o.object_key,
                "entry_name": o.entry_name,
                "kind": o.kind,
                "size_bytes": o.size_bytes,
                "sha256": o.sha256,
                "start_ms": o.start_ms,
                "end_ms": o.end_ms,
            }
            for o in page["objects"]
        ],
        "written": written,
    }


def compare(store: IncrementalStore, device_id: str, other: dict) -> dict:
    """Compare this store's digest against another client's digest."""
    mine = store.digest(device_id)
    return {
        "in_sync": mine.get("digest") == other.get("digest"),
        "local": mine,
        "remote": other,
    }
