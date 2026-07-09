#!/usr/bin/env bash
#
# Register Condor's MCP servers with OpenClaw, if OpenClaw is installed.
#
# Run after `uv sync` — `openclaw mcp probe` spawns the servers for real, so
# the Python deps must already be present.
#
# Idempotent: `openclaw mcp set` overwrites an existing entry, unlike `add`
# which fails when the name is taken.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v openclaw &>/dev/null; then
    echo "OpenClaw not found on PATH — skipping MCP registration."
    exit 0
fi

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env"
    set +a
fi

if [ -z "${ADMIN_USER_ID:-}" ] || [ -z "${TELEGRAM_TOKEN:-}" ]; then
    echo "ADMIN_USER_ID or TELEGRAM_TOKEN unset — run setup-environment.sh first."
    echo "Skipping MCP registration."
    exit 0
fi

echo "Registering Condor MCP servers with OpenClaw..."

# Build the JSON with json.dumps rather than interpolating into a heredoc: a bot
# token containing a quote or backslash would otherwise corrupt the config.
#
# `uv run` must locate the condor project. The `cwd` field alone is not enough:
# `openclaw mcp probe` honors it, but the claude CLI ignores it when spawning
# stdio servers from `--mcp-config`, so uv falls back to its managed interpreter
# and the server dies with `No module named 'mcp_servers'`. `--directory` pins
# the project regardless of the spawning process's cwd.
#
# The condor server derives chat_id/user_id per-conversation when Condor spawns
# it. OpenClaw has no conversation to derive from, so pin it to the admin, whose
# Telegram DM chat_id equals their user_id.
condor_json=$(python3 -c '
import json, sys
root, admin, token = sys.argv[1:4]
print(json.dumps({
    "command": "uv",
    "args": ["run", "--directory", root, "python", "-m", "mcp_servers.condor"],
    "cwd": root,
    "env": {
        "CONDOR_CHAT_ID": admin,
        "CONDOR_USER_ID": admin,
        "TELEGRAM_BOT_TOKEN": token,
    },
}))' "$REPO_ROOT" "$ADMIN_USER_ID" "$TELEGRAM_TOKEN")

hb_json=$(python3 -c '
import json, sys
root = sys.argv[1]
print(json.dumps({
    "command": "uv",
    "args": ["run", "--directory", root, "python", "-m", "mcp_servers.hummingbot_api"],
    "cwd": root,
    "env": {},
}))' "$REPO_ROOT")

openclaw mcp set condor "$condor_json"
openclaw mcp set mcp-hummingbot "$hb_json"

# OpenClaw's claude-cli backend spawns `claude -p` with a hardcoded
# `--allowedTools mcp__openclaw__*`. That is an exclusive filter, not a
# permission gate: every mcp__condor__* / mcp__mcp-hummingbot__* tool is
# stripped from the model's toolset, so a correctly registered server looks
# like it simply never connected. Widen the allowlist through the supported
# override at agents.defaults.cliBackends.claude-cli.
#
# Upstream fix (openclaw): derive the allowlist from the servers OpenClaw
# itself bundled into the --mcp-config it generates, instead of the literal.
widen_allowlist() {
    local cfg
    cfg="$(openclaw config file 2>/dev/null || true)"
    cfg="${cfg/#\~/$HOME}"  # `openclaw config file` prints a ~-prefixed path

    if [ -z "$cfg" ] || [ ! -f "$cfg" ]; then
        echo "! Could not locate OpenClaw's config — skipping allowlist widening."
        return 0
    fi

    local patch rc
    patch=$(python3 -c '
import json, os, sys

cfg_path = os.path.expanduser(sys.argv[1])
WANT = ["mcp__condor__*", "mcp__mcp-hummingbot__*"]

# Launch flags OpenClaw ships for the claude-cli backend. Its mergeBackendConfig
# does `args: override.args ?? base.args` -- a full replacement, not a merge --
# so an override has to restate them in full.
BASE_ARGS = [
    "-p", "--output-format", "stream-json", "--include-partial-messages",
    "--verbose", "--setting-sources", "user",
    "--allowedTools", "mcp__openclaw__*",
    "--disallowedTools",
    "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
]
BASE_RESUME_ARGS = BASE_ARGS + ["--resume", "{sessionId}"]

with open(cfg_path) as fh:
    cfg = json.load(fh)

backends = cfg.get("agents", {}).get("defaults", {}).get("cliBackends", {})
override = {}
for key, entry in backends.items():
    if key.lower().replace("_", "-") == "claude-cli" and isinstance(entry, dict):
        override = entry
        break

def widen(args, base):
    """Append the Condor patterns to --allowedTools, preserving everything else."""
    args = list(args) if args else list(base)
    try:
        i = args.index("--allowedTools")
    except ValueError:
        return None, False
    if i + 1 >= len(args):
        return None, False
    patterns = [p for p in args[i + 1].split(",") if p]
    added = [p for p in WANT if p not in patterns]
    if not added:
        return args, False
    args[i + 1] = ",".join(patterns + added)
    return args, True

pinned = not override.get("args")
new_args, changed_a = widen(override.get("args"), BASE_ARGS)
new_resume, changed_r = widen(override.get("resumeArgs"), BASE_RESUME_ARGS)

if new_args is None or new_resume is None:
    sys.exit(2)          # unrecognized override -- do not guess
if not (changed_a or changed_r):
    sys.exit(3)          # already widened

# `command` is required by the cliBackends schema even when only args change.
entry = {"command": override.get("command", "claude"),
         "args": new_args, "resumeArgs": new_resume}
print(json.dumps({"agents": {"defaults": {"cliBackends": {"claude-cli": entry}}}}))

# 4 = we had to write the base args wholesale, pinning them.
sys.exit(4 if pinned else 0)
' "$cfg" 2>/dev/null) && rc=0 || rc=$?

    if [ "$rc" -eq 3 ]; then
        echo "OpenClaw already allows Condor's MCP tools."
        return 0
    fi
    if { [ "$rc" -ne 0 ] && [ "$rc" -ne 4 ]; } || [ -z "$patch" ]; then
        echo "! Could not widen OpenClaw's MCP tool allowlist — Condor's tools may not be callable."
        echo "  Inspect agents.defaults.cliBackends.claude-cli in $cfg"
        return 0
    fi
    if printf '%s' "$patch" | openclaw config patch --stdin >/dev/null 2>&1; then
        echo "Widened OpenClaw's MCP tool allowlist for Condor."
        if [ "$rc" -eq 4 ]; then
            echo "! This pins claude-cli's launch flags — re-run this script after an OpenClaw upgrade."
        fi
    else
        echo "! Could not write OpenClaw's config — Condor's MCP tools may not be callable."
    fi
}

widen_allowlist

if openclaw mcp probe condor && openclaw mcp probe mcp-hummingbot; then
    echo "Registered. Restart the OpenClaw gateway to load the tools."
else
    echo "Registered, but at least one server failed to probe. Check 'openclaw mcp doctor'."
fi
