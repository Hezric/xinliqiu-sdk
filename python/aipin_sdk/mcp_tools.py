# -*- coding: utf-8 -*-
"""Tool definitions for the xinliqiu MCP server.

Agent-facing UX rules:

* ``xinliqiu_capabilities`` is meant to be called first. It states clearly
  which capabilities are unavailable so an agent does not attempt them.
* Long operations return ``task_id`` immediately; poll ``xinliqiu_task_status``.
* Audio is never decrypted implicitly; ``xinliqiu_fetch_audio`` requires an
  explicit owner key and is the only tool that yields plaintext.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from .audio import to_wav
from .capabilities import describe
from .client import Credentials, Xinliqiu
from .container import decrypt_enpt, parse_cmpt_file
from .mcp_server import McpServer, ToolError
from .storage import CloudflareStorage, LocalStorage, ingest_container
from .sync import pull_changes as pull_changes_fn
from .tasks import TaskRegistry


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


def credentials_from_env() -> Credentials:
    device = _env("XINLIQIU_DEVICE_ID")
    peer = _env("XINLIQIU_PEER_ID_FILE")
    key = _env("XINLIQIU_KEY_FILE")
    missing = [n for n, v in (
        ("XINLIQIU_DEVICE_ID", device),
        ("XINLIQIU_PEER_ID_FILE", peer),
        ("XINLIQIU_KEY_FILE", key),
    ) if not v]
    if missing:
        raise ToolError("missing environment: " + ", ".join(missing))
    return Credentials(device_id=device, peer_id_file=Path(peer), key_file=Path(key))


def client_from_env() -> Xinliqiu:
    agent_pub = _env("XINLIQIU_AGENT_PUBLIC_KEY")
    owner = _env("XINLIQIU_OWNER_KEY")
    return Xinliqiu(
        credentials_from_env(),
        agent_public_key=Path(agent_pub) if agent_pub else None,
        owner_private_key=Path(owner) if owner else None,
    )


def storage_from_env():
    backend = (_env("XINLIQIU_STORE") or "local").lower()
    if backend == "cloudflare":
        url = _env("XINLIQIU_STORE_URL")
        token = _env("XINLIQIU_STORE_TOKEN")
        if not url or not token:
            raise ToolError("XINLIQIU_STORE_URL and XINLIQIU_STORE_TOKEN are required")
        return CloudflareStorage(url, token), backend
    return LocalStorage(_env("XINLIQIU_STORE_PATH") or "./xinliqiu-store"), backend


def build_server(registry: TaskRegistry | None = None) -> McpServer:
    server = McpServer()
    tasks = registry or TaskRegistry()

    @server.tool(
        "xinliqiu_capabilities",
        "List what this xinliqiu supports. Call this first. Returns supported, bounded and unavailable buckets.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    def capabilities() -> dict[str, Any]:
        return {"ok": True, **describe()}

    @server.tool(
        "xinliqiu_status",
        "Read recording state, battery percentage and error byte from the device.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    def status() -> dict[str, Any]:
        s = client_from_env().status()
        return {
            "ok": True,
            "recording": s.recording,
            "battery_percent": s.battery_percent,
            "error_code": s.error_code,
        }

    @server.tool(
        "xinliqiu_inventory",
        "Aggregate item count and byte total held by the device in a device-clock range. No file list is exposed.",
        {
            "type": "object",
            "properties": {
                "start_ms": {"type": "integer", "minimum": 0},
                "end_ms": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    )
    def inventory(start_ms: int = 0, end_ms: int = 9_999_999_999_999) -> dict[str, Any]:
        return {"ok": True, **client_from_env().inventory_totals(start_ms, end_ms)}

    @server.tool(
        "xinliqiu_storage",
        "Report device usage and the firmware's low-space thresholds. Total and free capacity are not exposed by the device, so those fields are null.",
        {
            "type": "object",
            "properties": {
                "start_ms": {"type": "integer", "minimum": 0},
                "end_ms": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    )
    def storage(start_ms: int = 0, end_ms: int = 0) -> dict[str, Any]:
        return {"ok": True, **client_from_env().storage_info(start_ms, end_ms or None)}

    @server.tool(
        "xinliqiu_record_start",
        "Start recording. Returns a task id immediately. No audio file is created until voice activity is detected.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    def record_start() -> dict[str, Any]:
        client_from_env()  # fail fast on missing credentials instead of reporting a task that will die
        task = tasks.submit("record_start", lambda: {"result": client_from_env().record_start()})
        return {"ok": True, "task_id": task.task_id, "state": task.state}

    @server.tool(
        "xinliqiu_record_stop",
        "Stop recording. Returns a task id immediately.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    def record_stop() -> dict[str, Any]:
        client_from_env()  # fail fast on missing credentials
        task = tasks.submit("record_stop", lambda: {"result": client_from_env().record_stop()})
        return {"ok": True, "task_id": task.task_id, "state": task.state}

    @server.tool(
        "xinliqiu_collect_voiceprint",
        "Record one bounded voiceprint capture (1..127 seconds). Returns a task id.",
        {
            "type": "object",
            "properties": {"seconds": {"type": "integer", "minimum": 1, "maximum": 127, "default": 6}},
            "additionalProperties": False,
        },
    )
    def collect_voiceprint(seconds: int = 6) -> dict[str, Any]:
        if not 1 <= seconds <= 127:
            raise ToolError("seconds must be within 1..127")
        client_from_env()  # fail fast on missing credentials
        task = tasks.submit("collect_voiceprint", lambda: client_from_env().collect_voiceprint(seconds))
        return {"ok": True, "task_id": task.task_id, "state": task.state, "seconds": seconds}

    @server.tool(
        "xinliqiu_sync",
        "Fetch a device-clock range and optionally push ciphertext to storage. Returns a task id. Nothing is decrypted.",
        {
            "type": "object",
            "properties": {
                "start_ms": {"type": "integer", "minimum": 0},
                "end_ms": {"type": "integer", "minimum": 0},
                "store": {"type": "boolean", "default": True},
            },
            "required": ["start_ms", "end_ms"],
            "additionalProperties": False,
        },
    )
    def sync(start_ms: int, end_ms: int, store: bool = True) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            result = client_from_env().sync_range(start_ms, end_ms)
            summary: dict[str, Any] = {
                "output_dir": result.get("_output_dir"),
                "files_completed": result.get("syncFilesCompleted"),
                "files_failed": result.get("syncFilesFailed"),
                "range_outcome": result.get("syncRangeOutcome"),
            }
            if store:
                backend_storage, backend = storage_from_env()
                totals = {"stored": 0, "duplicates": 0, "total": 0}
                for path in sorted(Path(result["_output_dir"]).glob("*.bin")):
                    try:
                        container = parse_cmpt_file(path)
                    except Exception:  # noqa: BLE001 - skip non-CMPT diagnostics
                        continue
                    counts = ingest_container(container, backend_storage, credentials_from_env().device_id)
                    for k in totals:
                        totals[k] += counts[k]
                summary["store"] = {"backend": backend, **totals}
            return summary

        client_from_env()  # fail fast on missing credentials
        if store:
            storage_from_env()  # fail fast on misconfigured storage
        task = tasks.submit("sync", work)
        return {"ok": True, "task_id": task.task_id, "state": task.state}

    @server.tool(
        "xinliqiu_task_status",
        "Poll a long-running task by id.",
        {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
    )
    def task_status(task_id: str) -> dict[str, Any]:
        task = tasks.get(task_id)
        if task is None:
            raise ToolError(f"no such task: {task_id}")
        return {"ok": True, **task.to_dict()}

    @server.tool(
        "xinliqiu_fetch_audio",
        "Decrypt one audio entry and return it base64-encoded. The only tool that produces plaintext; requires an owner key.",
        {
            "type": "object",
            "properties": {
                "container_path": {"type": "string"},
                "owner_key_path": {"type": "string"},
                "entry_index": {"type": "integer", "minimum": 0, "default": 0},
                "format": {"type": "string", "enum": ["wav", "raw"], "default": "wav"},
            },
            "required": ["container_path"],
            "additionalProperties": False,
        },
    )
    def fetch_audio(
        container_path: str,
        owner_key_path: str | None = None,
        entry_index: int = 0,
        format: str = "wav",
    ) -> dict[str, Any]:
        key_path = owner_key_path or _env("XINLIQIU_OWNER_KEY")
        if not key_path:
            raise ToolError("an owner private key is required to decrypt audio")
        container = parse_cmpt_file(Path(container_path))
        audio_entries = container.by_kind("audio")
        if not audio_entries:
            raise ToolError("container has no audio entries")
        if not 0 <= entry_index < len(audio_entries):
            raise ToolError(f"entry_index {entry_index} out of range (0..{len(audio_entries) - 1})")
        entry = audio_entries[entry_index]
        plaintext = decrypt_enpt(entry.payload, Path(key_path))
        payload = plaintext if format == "raw" else to_wav(plaintext)
        return {
            "ok": True,
            "entry_name": entry.name,
            "plaintext_bytes": len(plaintext),
            "format": format,
            "data_base64": base64.b64encode(payload).decode("ascii"),
        }

    @server.tool(
        "xinliqiu_pull_changes",
        "Read what the store already holds beyond a cursor. Returns metadata only; use download=true to also write ciphertext locally.",
        {
            "type": "object",
            "properties": {
                "since": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 500},
                "download": {"type": "boolean", "default": False},
                "local_dir": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )
    def pull_changes(since: int = 0, limit: int = 500, download: bool = False, local_dir: str | None = None) -> dict[str, Any]:
        storage, backend = storage_from_env()
        if not hasattr(storage, "changes"):
            raise ToolError(f"storage backend '{backend}' does not support incremental sync")
        result = pull_changes_fn(
            storage,
            credentials_from_env().device_id,
            since=since,
            limit=limit,
            download=download,
            local_dir=Path(local_dir) if local_dir else None,
        )
        return {"ok": True, "backend": backend, **result}

    @server.tool(
        "xinliqiu_digest",
        "Content digest for a device range. Compare two clients cheaply without transferring objects.",
        {
            "type": "object",
            "properties": {
                "start_ms": {"type": "integer", "minimum": 0, "default": 0},
                "end_ms": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    )
    def digest(start_ms: int = 0, end_ms: int = 9_999_999_999_999) -> dict[str, Any]:
        storage, backend = storage_from_env()
        if not hasattr(storage, "digest"):
            raise ToolError(f"storage backend '{backend}' does not support digests")
        return {"ok": True, "backend": backend, **storage.digest(credentials_from_env().device_id, start_ms, end_ms)}

    return server


def main() -> None:
    build_server().serve()


if __name__ == "__main__":
    main()
