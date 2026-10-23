# -*- coding: utf-8 -*-
"""Pluggable storage for synced containers.

Hard rule enforced across every backend in this module: **only ciphertext is
stored**.  Entries carry their original enpt payload; nothing here decrypts.
Device credentials never pass through a Storage implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .container import Container, Entry
from .errors import XinliqiuError


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    entry_name: str
    kind: str
    size_bytes: int
    sha256: str
    start_ms: int | None
    end_ms: int | None


def _name_times(name: str) -> tuple[int | None, int | None]:
    stem = name.rsplit(".", 3)[0]
    parts = stem.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]), int(parts[1])
    return None, None


class Storage(Protocol):
    def put_entry(self, device_id: str, entry: Entry) -> dict: ...
    def list(self, device_id: str, start_ms: int, end_ms: int) -> list[StoredObject]: ...
    def get(self, object_key: str) -> bytes: ...


class LocalStorage:
    """Filesystem store: one directory per device, one file per ciphertext blob."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"

    def _digest(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put_entry(self, device_id: str, entry: Entry) -> dict:
        sha = self._digest(entry.payload)
        device_dir = self.root / device_id
        device_dir.mkdir(parents=True, exist_ok=True)
        target = device_dir / f"{sha}.bin"
        if target.exists():
            return {"status": "duplicate", "object_key": f"{device_id}/{sha}"}
        tmp = target.with_suffix(".part")
        tmp.write_bytes(entry.payload)
        os.chmod(tmp, 0o600)
        tmp.rename(target)
        start, end = _name_times(entry.name)
        record = {
            "object_key": f"{device_id}/{sha}",
            "device_id": device_id,
            "entry_name": entry.name,
            "kind": entry.kind,
            "size_bytes": len(entry.payload),
            "sha256": sha,
            "device_start_ms": start,
            "device_end_ms": end,
        }
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"status": "stored", "object_key": record["object_key"], "sha256": sha}

    def _records(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        out = []
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def list(self, device_id: str, start_ms: int, end_ms: int) -> list[StoredObject]:
        rows = []
        for r in self._records():
            if r.get("device_id") != device_id:
                continue
            t = r.get("device_end_ms") or r.get("device_start_ms") or 0
            if start_ms <= t <= end_ms:
                rows.append(
                    StoredObject(
                        object_key=r["object_key"],
                        entry_name=r["entry_name"],
                        kind=r["kind"],
                        size_bytes=r["size_bytes"],
                        sha256=r["sha256"],
                        start_ms=r.get("device_start_ms"),
                        end_ms=r.get("device_end_ms"),
                    )
                )
        # Entries without a device timestamp (diagnostics) sort last.
        rows.sort(key=lambda o: (0, o.end_ms or o.start_ms or 0) if (o.end_ms or o.start_ms) else (1, 0))
        return rows

    def changes(self, device_id: str, since: int = 0, limit: int = 500) -> dict:
        """Incremental pull; the cursor is the 1-based index of the last row."""
        rows = [r for r in self._records() if r.get("device_id") == device_id]
        window = rows[since : since + limit]
        cursor = since + len(window)
        return {
            "objects": [
                StoredObject(
                    object_key=r["object_key"],
                    entry_name=r["entry_name"],
                    kind=r["kind"],
                    size_bytes=r["size_bytes"],
                    sha256=r["sha256"],
                    start_ms=r.get("device_start_ms"),
                    end_ms=r.get("device_end_ms"),
                )
                for r in window
            ],
            "cursor": cursor,
            "has_more": cursor < len(rows),
        }

    def digest(self, device_id: str, start_ms: int = 0, end_ms: int = 9_999_999_999_999) -> dict:
        """Same digest definition as the Cloudflare backend."""
        rows = [r for r in self._records() if r.get("device_id") == device_id]
        rows.sort(key=lambda r: r["object_key"])
        material = "\n".join(f"{r['object_key']}:{r['sha256']}" for r in rows)
        return {
            "device": device_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "count": len(rows),
            "total_bytes": sum(r["size_bytes"] for r in rows),
            "digest": hashlib.sha256(material.encode("utf-8")).hexdigest(),
            "latest_seq": len(rows),
        }

    def get(self, object_key: str) -> bytes:
        device_id, _, sha = object_key.partition("/")
        path = self.root / device_id / f"{sha}.bin"
        if not path.exists():
            raise XinliqiuError(f"object not found: {object_key}")
        return path.read_bytes()


class CloudflareStorage:
    """Remote store backed by the `cloudflare/` Worker in this repository."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base_url + path, data=body, method=method)
        req.add_header("authorization", f"Bearer {self.token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def put_entry(self, device_id: str, entry: Entry) -> dict:
        sha = hashlib.sha256(entry.payload).hexdigest()
        start, end = _name_times(entry.name)
        headers = {
            "content-type": "application/octet-stream",
            "x-xinliqiu-device": device_id,
            "x-xinliqiu-name": entry.name,
            "x-xinliqiu-kind": entry.kind,
            "x-xinliqiu-sha256": sha,
        }
        if start is not None:
            headers["x-xinliqiu-start-ms"] = str(start)
        if end is not None:
            headers["x-xinliqiu-end-ms"] = str(end)
        status, payload = self._request("POST", "/objects", body=entry.payload, headers=headers)
        if status not in (200, 201):
            raise XinliqiuError(
                f"cloudflare put failed ({status}): {payload[:300].decode('utf-8', 'replace')}"
            )
        return json.loads(payload.decode("utf-8"))

    def list(self, device_id: str, start_ms: int, end_ms: int) -> list[StoredObject]:
        query = urllib.parse.urlencode(
            {"device": device_id, "start_ms": start_ms, "end_ms": end_ms}
        )
        status, payload = self._request("GET", f"/objects?{query}")
        if status != 200:
            raise XinliqiuError(f"cloudflare list failed ({status})")
        body = json.loads(payload.decode("utf-8"))
        return [
            StoredObject(
                object_key=o["object_key"],
                entry_name=o["entry_name"],
                kind=o["kind"],
                size_bytes=o["size_bytes"],
                sha256=o["sha256"],
                start_ms=o.get("device_start_ms"),
                end_ms=o.get("device_end_ms"),
            )
            for o in body.get("objects", [])
        ]

    def get(self, object_key: str) -> bytes:
        status, payload = self._request(
            "GET", "/objects/" + urllib.parse.quote(object_key, safe="")
        )
        if status != 200:
            raise XinliqiuError(f"cloudflare get failed ({status})")
        return payload

    def changes(self, device_id: str, since: int = 0, limit: int = 500) -> dict:
        """Incremental pull by storage cursor.

        Returns ``{"objects": [...], "cursor": int, "has_more": bool}``.
        Cursor is the storage-side sequence, not a device timestamp.
        """
        query = urllib.parse.urlencode({"device": device_id, "since": since, "limit": limit})
        status, payload = self._request("GET", f"/objects/changes?{query}")
        if status != 200:
            raise XinliqiuError(f"cloudflare changes failed ({status})")
        body = json.loads(payload.decode("utf-8"))
        return {
            "objects": [
                StoredObject(
                    object_key=o["object_key"],
                    entry_name=o["entry_name"],
                    kind=o["kind"],
                    size_bytes=o["size_bytes"],
                    sha256=o["sha256"],
                    start_ms=o.get("device_start_ms"),
                    end_ms=o.get("device_end_ms"),
                )
                for o in body.get("objects", [])
            ],
            "cursor": int(body.get("cursor") or since),
            "has_more": bool(body.get("has_more")),
        }

    def digest(self, device_id: str, start_ms: int = 0, end_ms: int = 9_999_999_999_999) -> dict:
        """Content digest for a device range; compare two clients cheaply."""
        query = urllib.parse.urlencode({"device": device_id, "start_ms": start_ms, "end_ms": end_ms})
        status, payload = self._request("GET", f"/digest?{query}")
        if status != 200:
            raise XinliqiuError(f"cloudflare digest failed ({status})")
        return json.loads(payload.decode("utf-8"))

    def inventory(self, device_id: str | None = None) -> dict:
        path = "/inventory" + (f"?device={urllib.parse.quote(device_id)}" if device_id else "")
        status, payload = self._request("GET", path)
        if status != 200:
            raise XinliqiuError(f"cloudflare inventory failed ({status})")
        return json.loads(payload.decode("utf-8"))


def ingest_container(container: Container, storage: Storage, device_id: str) -> dict:
    """Push every entry of a synced container into a Storage backend."""
    stored = duplicated = 0
    for entry in container.entries:
        result = storage.put_entry(device_id, entry)
        if result.get("status") == "stored":
            stored += 1
        else:
            duplicated += 1
    return {"stored": stored, "duplicates": duplicated, "total": len(container.entries)}
