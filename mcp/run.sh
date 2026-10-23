#!/usr/bin/env bash
# Launch the xinliqiu MCP server over stdio.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE/../python${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m aipin_sdk.mcp_tools
