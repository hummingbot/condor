"""Cursor SDK client -- persistent local agents with custom trading tools.

Drop-in alternative to ManagedAgentClient (start -> prompt_stream -> stop,
yielding the same ACPEvent types) that runs the agent loop via the Cursor SDK
local runtime instead of Anthropic's Managed Agents harness.

Key properties:
  - One persistent local Cursor agent per Condor trading session: each tick is
    a follow-up message in the same conversation.
  - Trading tools are exposed as SDK custom_tools wrapping McpToolBridge, gated
    by the same permission_callback as other providers.
  - State (agent id, fingerprint) persists in ``{agent_dir}/state/cursor_agent.json``.

Requires ``cursor-sdk`` and ``CURSOR_API_KEY``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

from .client import (
    ACPEvent,
    Heartbeat,
    PermissionCallback,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from .managed_tools import McpToolBridge

log = logging.getLogger(__name__)

CURSOR_AGENT_PREFIX = "cursor-managed"
DEFAULT_CURSOR_MODEL = "composer-2.5"

_SNAPSHOT_OUTPUT_CHARS = 4000

# Shared bridge across per-tick client instances (one workspace).
_bridge_lock = asyncio.Lock()
_bridge_client: Any = None
_bridge_workspace: str = ""

# Substrings that indicate a wedged Cursor SDK bridge or remote agent.
_RECOVERABLE_ERROR_MARKERS = (
    "bridge request failed",
    "internal: internal error",
    "internalservererror",
    "connecterror",
    "remoteprotocolerror",
)


async def reset_cursor_bridge() -> None:
    """Drop the process-global Cursor SDK bridge so the next tick relaunches it."""
    global _bridge_client, _bridge_workspace
    async with _bridge_lock:
        _bridge_client = None
        _bridge_workspace = ""


def is_recoverable_cursor_error(exc: BaseException) -> bool:
    """True when resetting the bridge and rotating the remote agent may help."""
    try:
        from cursor_sdk import errors as cursor_errors

        if isinstance(
            exc,
            (
                cursor_errors.InternalServerError,
                getattr(cursor_errors, "ConnectError", type(None)),
            ),
        ):
            return True
    except ImportError:
        pass

    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass

    lowered = str(exc).lower()
    return any(marker in lowered for marker in _RECOVERABLE_ERROR_MARKERS)


def is_cursor_provider_error(exc_or_text: Any) -> bool:
    """Detect Cursor SDK / bridge failures from an exception or journal text."""
    if isinstance(exc_or_text, BaseException):
        return is_recoverable_cursor_error(exc_or_text)
    lowered = str(exc_or_text).lower()
    return any(marker in lowered for marker in _RECOVERABLE_ERROR_MARKERS)


def _blocked_result_text(permission_result: dict[str, Any] | None = None) -> str:
    from condor.trading_agent.risk import format_block_result

    reason = None
    if permission_result:
        reason = permission_result.get("block_reason")
    return format_block_result(reason)


def is_cursor_agent_key(agent_key: str) -> bool:
    """Check if an agent_key routes to the Cursor SDK provider."""
    if not agent_key:
        return False
    return agent_key == CURSOR_AGENT_PREFIX or agent_key.startswith(
        CURSOR_AGENT_PREFIX + ":"
    )


def resolve_cursor_model(agent_key: str, config_model: str) -> str:
    """Resolve the model id: inline key > config.model > default."""
    if ":" in agent_key:
        inline = agent_key.split(":", 1)[1].strip()
        if inline:
            return inline
    return config_model or DEFAULT_CURSOR_MODEL


def is_persistent_provider_key(agent_key: str) -> bool:
    """Managed or Cursor agents use lean per-tick prompts + system prompt."""
    from .managed_agent_client import is_managed_agent_key

    return is_cursor_agent_key(agent_key) or is_managed_agent_key(agent_key)


class CursorAgentClient:
    """Runs the trading brain on the Cursor SDK local agent runtime."""

    def __init__(
        self,
        model: str,
        system_prompt: str,
        agent_name: str,
        slug: str,
        agent_dir: Path | str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_callback: PermissionCallback | None = None,
        persist_session: bool = True,
        memory_bootstrap: str = "",
        working_dir: str | None = None,
        bridge: Any = None,
        sdk_client: Any = None,
        async_agent: Any = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.slug = slug
        self.agent_dir = Path(agent_dir) if agent_dir else None
        self.permission_callback = permission_callback
        self.persist_session = persist_session
        self.memory_bootstrap = memory_bootstrap.strip()
        self._bootstrap_pending = bool(self.memory_bootstrap)
        self.working_dir = working_dir or os.getcwd()
        self._bridge = bridge or McpToolBridge(mcp_servers, working_dir=self.working_dir)
        self._injected_sdk = sdk_client
        self._sdk_client = sdk_client
        self._agent = async_agent
        self._agent_id = ""
        self._started = False
        self._custom_tools: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # State file
    # ------------------------------------------------------------------

    def _state_path(self) -> Path | None:
        if not self.agent_dir:
            return None
        return self.agent_dir / "state" / "cursor_agent.json"

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Cursor agent state file unreadable: %s", path)
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _fingerprint(self, tool_names: list[str]) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "system": self.system_prompt,
                "tools": sorted(tool_names),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Bridge singleton
    # ------------------------------------------------------------------

    async def _ensure_sdk_client(self, force_relaunch: bool = False) -> Any:
        global _bridge_client, _bridge_workspace
        if self._injected_sdk is not None:
            self._sdk_client = self._injected_sdk
            return self._sdk_client
        if self._sdk_client is not None and not force_relaunch:
            return self._sdk_client
        async with _bridge_lock:
            if force_relaunch:
                _bridge_client = None
                _bridge_workspace = ""
                self._sdk_client = None
            if _bridge_client is None or _bridge_workspace != self.working_dir:
                from cursor_sdk import AsyncClient

                _bridge_client = await AsyncClient.launch_bridge(
                    workspace=self.working_dir
                )
                _bridge_workspace = self.working_dir
            self._sdk_client = _bridge_client
            return self._sdk_client

    async def _recover_cursor_provider(self) -> None:
        """Reset bridge + drop persisted agent, then provision a fresh conversation."""
        await reset_cursor_bridge()
        self._sdk_client = None
        self._agent = None
        self._agent_id = ""
        if self.agent_dir:
            await self.rotate_persisted_agent(self.agent_dir)
        await self._ensure_sdk_client(force_relaunch=True)
        await self._provision()

    async def _send_with_recovery(self, text: str) -> Any:
        """Send a user message, recovering once from bridge/agent failures."""
        assert self._agent is not None, "Cursor agent not provisioned"
        try:
            return await self._agent.send(text)
        except Exception as e:
            if not is_recoverable_cursor_error(e):
                raise
            log.warning(
                "Cursor send failed for %s (%s); resetting bridge and agent",
                self.slug,
                e,
            )
            await self._recover_cursor_provider()
            assert self._agent is not None
            return await self._agent.send(text)

    def _api_key(self) -> str:
        key = os.environ.get("CURSOR_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "CURSOR_API_KEY is required for cursor-managed agents"
            )
        return key

    # ------------------------------------------------------------------
    # Custom tools
    # ------------------------------------------------------------------

    def _build_custom_tools(self) -> dict[str, Any]:
        from cursor_sdk import CustomTool

        client = self
        tools: dict[str, Any] = {}

        for tool_def in self._bridge.custom_tool_defs:
            name = tool_def["name"]
            schema = tool_def.get("input_schema") or {"type": "object", "properties": {}}
            description = tool_def.get("description") or ""

            async def _execute(
                args: dict[str, Any],
                context: Any,
                *,
                _name: str = name,
            ) -> str:
                tool_input = dict(args or {})
                tool_use_id = getattr(context, "tool_call_id", None) or _name

                if client.permission_callback:
                    tool_call_info = {
                        "tool": _name,
                        "title": _name,
                        "input": tool_input,
                    }
                    options = [
                        {"optionId": "allow", "kind": "allow_once"},
                        {"optionId": "deny", "kind": "deny"},
                    ]
                    result = await client.permission_callback(tool_call_info, options)
                    outcome = result.get("outcome", {})
                    if isinstance(outcome, dict) and outcome.get("outcome") == "cancelled":
                        return _blocked_result_text(result)

                output, is_error = await client._bridge.call(_name, tool_input)
                if is_error:
                    return output
                return output

            tools[name] = CustomTool(
                description=description,
                input_schema=schema,
                execute=_execute,
            )
        return tools

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start MCP bridge and ensure Cursor agent exists."""
        await self._bridge.start()
        try:
            await self._provision()
        except Exception:
            await self.stop()
            raise
        self._started = True

    async def _provision(self) -> None:
        from cursor_sdk import AgentOptions, LocalAgentOptions

        client = await self._ensure_sdk_client()
        tool_names = [t["name"] for t in self._bridge.custom_tool_defs]
        fingerprint = self._fingerprint(tool_names)
        self._custom_tools = self._build_custom_tools()

        state = self._load_state()
        api_key = self._api_key()
        local_opts = LocalAgentOptions(
            cwd=self.working_dir,
            setting_sources=[],
            custom_tools=self._custom_tools,
        )

        reuse_id = ""
        if (
            self.persist_session
            and state.get("agent_id")
            and state.get("agent_fingerprint") == fingerprint
        ):
            reuse_id = state["agent_id"]

        if reuse_id:
            self._agent = await client.agents.resume(
                reuse_id,
                AgentOptions(
                    api_key=api_key,
                    model=self.model,
                    local=local_opts,
                ),
            )
            self._agent_id = reuse_id
            log.info("Resumed Cursor agent %s for %s", self._agent_id, self.slug)
        else:
            if state.get("agent_id") and state.get("agent_fingerprint") != fingerprint:
                log.info(
                    "Cursor agent fingerprint changed for %s; provisioning new agent",
                    self.slug,
                )
            self._agent = await client.agents.create(
                AgentOptions(
                    api_key=api_key,
                    model=self.model,
                    name=self.agent_name,
                    local=local_opts,
                ),
            )
            self._agent_id = self._agent.agent_id
            await self._bootstrap_system_prompt()
            state["system_bootstrapped"] = True

        state["agent_id"] = self._agent_id if self.persist_session else ""
        state["agent_fingerprint"] = fingerprint if self.persist_session else ""
        self._save_state(state)
        log.info(
            "CursorAgentClient ready: model=%s agent=%s tools=%d",
            self.model,
            self._agent_id,
            len(self._custom_tools),
        )

    async def _bootstrap_system_prompt(self) -> None:
        """Seed the persistent conversation with static system instructions."""
        assert self._agent is not None
        bootstrap = (
            "[SYSTEM INSTRUCTIONS — apply on every tick; do not repeat them]\n"
            f"{self.system_prompt}\n\n"
            "Acknowledge with exactly one word: READY"
        )
        run = await self._send_with_recovery(bootstrap)
        await run.wait()

    @staticmethod
    async def rotate_persisted_agent(agent_dir: Path | str) -> str:
        """Drop persisted agent id so the next start() creates a fresh conversation."""
        path = Path(agent_dir) / "state" / "cursor_agent.json"
        if not path.exists():
            return ""
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        old_id = state.pop("agent_id", "") or ""
        state.pop("system_bootstrapped", None)
        if old_id:
            path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            log.info("Rotated Cursor agent %s (dropped from state)", old_id)
        return old_id

    async def stop(self) -> None:
        """Close local MCP bridge; keep Cursor agent conversation alive."""
        await self._bridge.stop()
        if not self.persist_session and self._agent_id and self._sdk_client is not None:
            try:
                await self._agent.delete()
                log.info("Deleted ephemeral Cursor agent %s", self._agent_id)
            except Exception:
                log.warning("Failed to delete ephemeral Cursor agent %s", self._agent_id)
            self._agent_id = ""
            self._agent = None
        self._started = False

    @property
    def alive(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    async def prompt(self, text: str) -> str:
        chunks: list[str] = []
        async for event in self.prompt_stream(text):
            if isinstance(event, TextChunk):
                chunks.append(event.text)
        return "".join(chunks)

    async def prompt_stream(self, text: str) -> AsyncIterator[ACPEvent]:
        assert self._started and self._agent is not None, "Client not started"

        if self._bootstrap_pending and self.memory_bootstrap:
            text = (
                "[LOCAL LEARNINGS BOOTSTRAP — from Condor learnings.md; "
                "also curate memory/ under your agent directory]\n"
                f"{self.memory_bootstrap}\n\n{text}"
            )
            self._bootstrap_pending = False

        try:
            run = await self._send_with_recovery(text)
        except Exception as e:
            log.warning("Cursor agent send error: %s", e)
            yield TextChunk(text=f"(cursor session error: {e})")
            yield PromptDone(stop_reason="error")
            return

        loop = asyncio.get_event_loop()
        start_time = loop.time()
        seen_tools: dict[str, str] = {}

        try:
            async for message in run.messages():
                elapsed = loop.time() - start_time
                msg_type = getattr(message, "type", "")

                if msg_type == "assistant":
                    for block in getattr(message.message, "content", []) or []:
                        if getattr(block, "type", "") == "text":
                            chunk = getattr(block, "text", "")
                            if chunk:
                                yield TextChunk(text=chunk)

                elif msg_type == "thinking":
                    thought = getattr(message, "text", "") or "(thinking)"
                    yield ThoughtChunk(text=thought)

                elif msg_type == "tool_call":
                    call_id = getattr(message, "call_id", "") or getattr(
                        message, "callId", ""
                    )
                    name = getattr(message, "name", "tool")
                    status = getattr(message, "status", "in_progress")
                    args = getattr(message, "args", None)
                    result = getattr(message, "result", None)

                    if status == "running":
                        seen_tools[call_id] = name
                        yield ToolCallEvent(
                            tool_call_id=call_id,
                            title=name,
                            status="in_progress",
                            kind="mcp",
                            input=args if isinstance(args, dict) else None,
                        )
                    else:
                        is_error = status == "error"
                        output = _format_tool_result(result)
                        yield ToolCallUpdate(
                            tool_call_id=call_id,
                            status="failed" if is_error else "completed",
                            output=output[:_SNAPSHOT_OUTPUT_CHARS] if output else None,
                        )

                elif msg_type == "status":
                    status = getattr(message, "status", "")
                    if status in ("error", "failed"):
                        yield TextChunk(text=f"(cursor run status: {status})")

                if elapsed > 285:
                    yield Heartbeat(elapsed_seconds=elapsed)

            result = await run.wait()
            stop_reason = "error" if result.status == "error" else "end_turn"
            if result.status == "cancelled":
                stop_reason = "cancelled"
            if result.status == "error" and result.result:
                yield TextChunk(text=f"(cursor run error: {result.result[:500]})")
            yield PromptDone(stop_reason=stop_reason)

        except asyncio.CancelledError:
            try:
                await run.cancel()
            except Exception:
                pass
            raise
        except Exception as e:
            log.warning("Cursor agent stream error: %s", e)
            yield TextChunk(text=f"(cursor session error: {e})")
            yield PromptDone(stop_reason="error")


def _format_tool_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]))
            return "\n".join(parts)
        return json.dumps(result, default=str)
    return str(result)
