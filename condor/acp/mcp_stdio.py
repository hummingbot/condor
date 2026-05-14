"""Shared MCP stdio config helpers for pydantic-ai, Cursor SDK, and ACP-style dicts."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def stdio_env_for_server(
    srv_config: dict[str, Any],
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge session extra_env with per-server ACP env entries (name/value dicts).

    Mirrors PydanticAIClient MCP startup env handling (see MCPServerStdio).
    """
    env = dict(extra_env or {})
    for env_entry in srv_config.get("env", []):
        if isinstance(env_entry, dict) and "name" in env_entry:
            env[env_entry["name"]] = env_entry["value"]
    return env


def acp_mcp_list_to_cursor_mcp_servers(
    servers: list[dict[str, Any]],
    workspace_root: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Convert Condor/Acp-style MCP configs to @cursor/sdk `mcpServers` Record.

    Cursor stdio configs use `cwd` pointed at repo root so `uv run` resolves.
    Skips malformed entries instead of failing the agent.
    """
    out: dict[str, dict[str, Any]] = {}
    for srv in servers:
        name = srv.get("name")
        command = srv.get("command")
        if not isinstance(name, str) or not name.strip():
            log.warning("Skipping MCP row without usable name: %s", srv)
            continue
        if not isinstance(command, str) or not command.strip():
            log.warning("Skipping MCP row %s without command", name)
            continue
        args = srv.get("args") or []
        if not isinstance(args, list):
            args = []

        merged_env = stdio_env_for_server(srv, extra_env)
        cfg: dict[str, Any] = {
            "type": "stdio",
            "command": command,
            "args": args,
            "cwd": workspace_root,
        }
        if merged_env:
            cfg["env"] = merged_env

        if name in out:
            log.warning("Duplicate MCP name %s; replacing previous entry", name)
        out[name] = cfg

    return out
