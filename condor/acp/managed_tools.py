"""MCP-to-custom-tool bridge for Claude Managed Agents.

Spawns the same stdio MCP servers Condor already uses (mcp-hummingbot,
condor), lists their tools, and exposes them as Managed Agents *custom tool*
definitions. When the hosted agent emits a custom tool_use event, the
ManagedAgentClient dispatches it here -- so trade execution, credentials,
and risk gating all stay on the local machine.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any

log = logging.getLogger(__name__)

# Hard cap on tool result text returned to the hosted session.
MAX_RESULT_CHARS = 200_000

# The Managed Agents API caps custom tool descriptions at 1024 chars.
MAX_DESCRIPTION_CHARS = 1024

_EMPTY_SCHEMA = {"type": "object", "properties": {}}


class McpToolBridge:
    """Local MCP servers exposed as Managed Agents custom tools."""

    def __init__(
        self,
        server_configs: list[dict[str, Any]] | None = None,
        working_dir: str | None = None,
    ):
        self.server_configs = server_configs or []
        self.working_dir = working_dir
        self._exit_stack: AsyncExitStack | None = None
        # tool name -> (server_name, session)
        self._routes: dict[str, tuple[str, Any]] = {}
        self._tool_defs: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn stdio MCP servers and discover their tools."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import get_default_environment, stdio_client

        self._exit_stack = AsyncExitStack()

        for cfg in self.server_configs:
            name = cfg.get("name", cfg.get("command", "mcp"))
            env = dict(get_default_environment())
            for entry in cfg.get("env", []):
                if isinstance(entry, dict):
                    env[entry["name"]] = entry["value"]

            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=env,
                cwd=self.working_dir,
            )
            try:
                read, write = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                result = await session.list_tools()
                self._register(name, session, result.tools)
            except Exception:
                log.exception("MCP bridge: failed to start server '%s'", name)

        log.info(
            "MCP bridge ready: %d tools from %d servers",
            len(self._tool_defs), len(self.server_configs),
        )

    async def stop(self) -> None:
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                log.exception("MCP bridge: error closing servers")
            self._exit_stack = None
        self._routes.clear()
        self._tool_defs.clear()

    # ------------------------------------------------------------------
    # Tool registry
    # ------------------------------------------------------------------

    def _register(self, server_name: str, session: Any, tools: list[Any]) -> None:
        """Register a server's tools. First registration wins on name collision."""
        for tool in tools:
            if tool.name in self._routes:
                log.warning(
                    "MCP bridge: tool '%s' from '%s' shadowed by earlier server",
                    tool.name, server_name,
                )
                continue
            schema = getattr(tool, "inputSchema", None) or dict(_EMPTY_SCHEMA)
            description = getattr(tool, "description", None) or ""
            if len(description) > MAX_DESCRIPTION_CHARS:
                description = description[: MAX_DESCRIPTION_CHARS - 2].rstrip() + " …"
            self._routes[tool.name] = (server_name, session)
            self._tool_defs.append(
                {
                    "type": "custom",
                    "name": tool.name,
                    "description": description,
                    "input_schema": schema,
                }
            )

    @property
    def custom_tool_defs(self) -> list[dict[str, Any]]:
        return list(self._tool_defs)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Call a tool on its owning MCP server.

        Returns (output_text, is_error).
        """
        route = self._routes.get(name)
        if not route:
            return f"Unknown tool: {name}", True

        server_name, session = route
        try:
            result = await session.call_tool(name, arguments or {})
        except Exception as e:
            log.exception("MCP bridge: tool '%s' on '%s' failed", name, server_name)
            return f"Tool call failed: {e}", True

        text = self._render_content(result)
        is_error = bool(getattr(result, "isError", False))
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n... (truncated)"
        return text, is_error

    @staticmethod
    def _render_content(result: Any) -> str:
        parts: list[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                try:
                    parts.append(json.dumps(block.model_dump(), default=str))
                except Exception:
                    parts.append(str(block))
        return "\n".join(parts)
