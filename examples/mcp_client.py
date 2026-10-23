#!/usr/bin/env python3
"""Talk to the xinliqiu MCP server without an MCP client library.

Starts ``mcp/run.sh``, performs the initialize handshake, lists tools and calls
``xinliqiu_capabilities``. Useful as a smoke test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def send(proc: subprocess.Popen, payload: dict) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("server closed the connection")
    return json.loads(line)


def main() -> int:
    proc = subprocess.Popen(
        [str(ROOT / "mcp" / "run.sh")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        init = send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        print("server:", init["result"]["serverInfo"])

        tools = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in tools["result"]["tools"]]
        print("tools:", len(names))
        for name in names:
            print("  -", name)

        caps = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "xinliqiu_capabilities", "arguments": {}},
            },
        )
        payload = json.loads(caps["result"]["content"][0]["text"])
        print("unavailable:", [x["name"] for x in payload["statuses"]["unavailable"]])
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
