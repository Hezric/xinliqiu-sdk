# -*- coding: utf-8 -*-
"""Zero-dependency Model Context Protocol server.

Implements the MCP stdio transport directly (JSON-RPC 2.0, newline delimited)
instead of depending on a third-party `mcp` package, so the SDK stays portable
and installs nothing extra.

Supported methods:
    initialize, tools/list, tools/call, ping, shutdown

Run::

    python -m aipin_sdk.mcp_server

See ``docs/mcp-integration.md`` for a client configuration example.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "xinliqiu"
SERVER_VERSION = "0.1.0"


class ToolError(Exception):
    """Raised by a tool to produce a structured MCP error result."""


class McpServer:
    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    def tool(self, name: str, description: str, schema: dict[str, Any]) -> Callable:
        def decorator(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
            self._tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": schema,
                "fn": fn,
            }
            return fn

        return decorator

    # -- protocol --------------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if request_id is None:
            return None  # notification: nothing to respond to

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": spec["name"],
                            "description": spec["description"],
                            "inputSchema": spec["inputSchema"],
                        }
                        for spec in self._tools.values()
                    ]
                }
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                spec = self._tools.get(name)
                if spec is None:
                    return self._error(request_id, -32602, f"unknown tool: {name}")
                try:
                    payload = spec["fn"](**arguments)
                    result = {
                        "content": [
                            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}
                        ],
                        "isError": False,
                    }
                except ToolError as exc:
                    result = {
                        "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(exc)})}],
                        "isError": True,
                    }
            elif method == "shutdown":
                result = {}
            else:
                return self._error(request_id, -32601, f"method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:  # noqa: BLE001
            return self._error(request_id, -32603, f"internal error: {type(exc).__name__}: {exc}")

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
