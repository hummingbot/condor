#!/usr/bin/env bash
# Register the one Condor MCP server with OpenClaw when available.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v openclaw >/dev/null 2>&1; then
    echo "OpenClaw not found on PATH — skipping MCP registration."
    exit 0
fi

condor_json=$(python3 -c '
import json, sys
root = sys.argv[1]
print(json.dumps({
    "command": "uv",
    "args": ["run", "--directory", root, "python", "-m", "mcp_servers.condor"],
    "cwd": root,
    "env": {},
}))' "$REPO_ROOT")

openclaw mcp set condor "$condor_json"
echo "Registered Condor MCP server with OpenClaw."
