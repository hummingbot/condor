"""Claude Managed Agents client -- persistent hosted sessions with memory.

Drop-in alternative to ACPClient / PydanticAIClient (start -> prompt_stream
-> stop, yielding the same ACPEvent types) that runs the agent loop on
Anthropic's Managed Agents harness instead of a local subprocess.

Key properties:
  - One persistent hosted session per Condor trading session: each tick is a
    user.message in the same conversation, so the brain natively remembers
    earlier ticks (harness handles compaction + prompt caching).
  - A workspace memory store is mounted at /mnt/memory inside the sandbox and
    survives across sessions -- the agent's self-curated long-term memory.
  - Trading tools are *custom tools*: the hosted agent emits structured
    requests, Condor executes them locally through the MCP bridge and the
    existing permission/risk callback, and posts results back. Credentials
    and execution never leave the machine.

State (agent id, session id, store id) is persisted to
``{agent_dir}/state/managed_agent.json`` so continuity survives restarts.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
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

MANAGED_AGENT_PREFIX = "claude-managed"
DEFAULT_MANAGED_MODEL = "claude-sonnet-4-6"
ENVIRONMENT_NAME = "condor-trading"

# Built-in sandbox tools we keep off: market data comes exclusively from our
# own tools, which keeps inputs deterministic and prompt-injection surface low.
_DISABLED_BUILTIN_TOOLS = ("web_search", "web_fetch")

def _blocked_result_text(permission_result: dict[str, Any] | None = None) -> str:
    from condor.trading_agent.risk import format_block_result

    reason = None
    if permission_result:
        reason = permission_result.get("block_reason")
    return format_block_result(reason)

# Cap on tool-call output stored in tick snapshots (full text goes to the model).
_SNAPSHOT_OUTPUT_CHARS = 4000


def is_managed_agent_key(agent_key: str) -> bool:
    """Check if an agent_key routes to the Managed Agents provider."""
    if not agent_key:
        return False
    return agent_key == MANAGED_AGENT_PREFIX or agent_key.startswith(
        MANAGED_AGENT_PREFIX + ":"
    )


def resolve_managed_model(agent_key: str, config_model: str) -> str:
    """Resolve the model id: inline key > config.model > default."""
    if ":" in agent_key:
        inline = agent_key.split(":", 1)[1].strip()
        if inline:
            return inline
    return config_model or DEFAULT_MANAGED_MODEL


class ManagedAgentClient:
    """Runs the trading brain on the Claude Managed Agents harness."""

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
        memory_instructions: str = "",
        memory_bootstrap: str = "",
        working_dir: str | None = None,
        sdk_client: Any = None,
        bridge: Any = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.slug = slug
        self.agent_dir = Path(agent_dir) if agent_dir else None
        self.permission_callback = permission_callback
        self.persist_session = persist_session
        self.memory_instructions = memory_instructions or (
            "Your self-curated trading memory. Review it before trading; update "
            "it after closed positions and at session end. Keep entries concise "
            "and factual."
        )
        self.memory_bootstrap = memory_bootstrap.strip()
        self._bootstrap_pending = False
        self._sdk = sdk_client
        self._bridge = bridge or McpToolBridge(mcp_servers, working_dir=working_dir)
        self._agent_id: str = ""
        self._session_id: str = ""
        self._started = False

    # ------------------------------------------------------------------
    # State file
    # ------------------------------------------------------------------

    def _state_path(self) -> Path | None:
        if not self.agent_dir:
            return None
        return self.agent_dir / "state" / "managed_agent.json"

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Managed agent state file unreadable: %s", path)
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _fingerprint(self, tool_defs: list[dict[str, Any]]) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "system": self.system_prompt,
                "tools": sorted(t["name"] for t in tool_defs),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the MCP bridge and ensure agent + memory store + session."""
        if self._sdk is None:
            import anthropic

            self._sdk = anthropic.AsyncAnthropic()

        await self._bridge.start()
        try:
            await self._provision()
        except Exception:
            # Close the bridge in THIS task -- a GC-driven close later would
            # trip anyio's cancel-scope task check and leak MCP subprocesses.
            await self.stop()
            raise
        self._started = True

    async def _provision(self) -> None:
        """Ensure agent + environment + memory store + session exist."""
        tool_defs = self._bridge.custom_tool_defs

        state = self._load_state()
        fingerprint = self._fingerprint(tool_defs)

        # Agent (versioned, recreated when model/system/tools change)
        if state.get("agent_id") and state.get("agent_fingerprint") == fingerprint:
            self._agent_id = state["agent_id"]
        else:
            superseded = state.get("agent_id", "")
            self._agent_id = await self._create_agent(tool_defs)
            state["agent_id"] = self._agent_id
            state["agent_fingerprint"] = fingerprint
            state.pop("session_id", None)  # old session belongs to the old agent
            if superseded:
                try:
                    await self._sdk.beta.agents.archive(superseded)
                    log.info("Archived superseded managed agent %s", superseded)
                except Exception:
                    log.warning("Failed to archive superseded agent %s", superseded)

        environment_id = await self._ensure_environment()
        store_id = state.get("memory_store_id") or await self._ensure_memory_store()
        state["memory_store_id"] = store_id

        # Session: reuse the persisted one when it is still usable
        self._session_id = ""
        if self.persist_session and state.get("session_id"):
            self._session_id = await self._check_session(state["session_id"])

        if not self._session_id:
            session = await self._sdk.beta.sessions.create(
                agent=self._agent_id,
                environment_id=environment_id,
                title=f"condor:{self.slug}" + ("" if self.persist_session else " (experiment)"),
                resources=[
                    {
                        "type": "memory_store",
                        "memory_store_id": store_id,
                        "access": "read_write",
                        "instructions": self.memory_instructions,
                    }
                ],
            )
            self._session_id = session.id
            if self.memory_bootstrap:
                self._bootstrap_pending = True

        state["session_id"] = self._session_id if self.persist_session else ""
        self._save_state(state)
        log.info(
            "ManagedAgentClient ready: model=%s agent=%s session=%s tools=%d",
            self.model, self._agent_id, self._session_id, len(tool_defs),
        )

    @staticmethod
    async def rotate_persisted_session(
        agent_dir: Path | str, sdk_client: Any = None
    ) -> str:
        """Drop the persisted session so the next start() provisions a fresh one.

        Recovery path for wedged hosted sessions (e.g. a session that stops
        echoing user messages). The agent and memory store are kept -- only
        the conversation is abandoned. Best-effort deletes the wedged session
        server-side. Returns the dropped session id ("" if none).
        """
        path = Path(agent_dir) / "state" / "managed_agent.json"
        if not path.exists():
            return ""
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        session_id = state.pop("session_id", "") or ""
        if not session_id:
            return ""
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        if sdk_client is None:
            try:
                import anthropic

                sdk_client = anthropic.AsyncAnthropic()
            except Exception:
                return session_id
        try:
            await sdk_client.beta.sessions.delete(session_id)
            log.info("Deleted rotated managed session %s", session_id)
        except Exception:
            log.warning(
                "Could not delete rotated session %s (left idle server-side)",
                session_id,
            )
        return session_id

    async def stop(self) -> None:
        """Close local resources.

        Persistent sessions stay alive on purpose (they carry the loop's
        conversation). Ephemeral experiment sessions are deleted so dry runs
        don't accumulate server-side conversation data.
        """
        await self._bridge.stop()
        if not self.persist_session and self._session_id and self._sdk is not None:
            try:
                await self._sdk.beta.sessions.delete(self._session_id)
                log.info("Deleted ephemeral managed session %s", self._session_id)
            except Exception:
                log.warning(
                    "Failed to delete ephemeral session %s (idle sessions don't bill)",
                    self._session_id,
                )
            self._session_id = ""
        self._started = False

    @property
    def alive(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # Provisioning helpers
    # ------------------------------------------------------------------

    async def _create_agent(self, tool_defs: list[dict[str, Any]]) -> str:
        toolset: dict[str, Any] = {
            "type": "agent_toolset_20260401",
            "configs": [
                {"name": name, "enabled": False} for name in _DISABLED_BUILTIN_TOOLS
            ],
        }
        agent = await self._sdk.beta.agents.create(
            name=self.agent_name,
            model=self.model,
            system=self.system_prompt,
            description=f"Condor trading agent '{self.slug}' (managed provider)",
            tools=[toolset, *tool_defs],
            metadata={"condor_slug": self.slug},
        )
        log.info("Created managed agent %s for %s", agent.id, self.slug)
        return agent.id

    async def _ensure_environment(self) -> str:
        async for env in self._aiter(self._sdk.beta.environments.list()):
            if getattr(env, "name", "") == ENVIRONMENT_NAME and not getattr(
                env, "archived_at", None
            ):
                return env.id
        env = await self._sdk.beta.environments.create(name=ENVIRONMENT_NAME)
        log.info("Created managed environment %s", env.id)
        return env.id

    async def _ensure_memory_store(self) -> str:
        store_name = f"condor-{self.slug}-memory"
        async for store in self._aiter(self._sdk.beta.memory_stores.list()):
            if getattr(store, "name", "") == store_name and not getattr(
                store, "archived_at", None
            ):
                return store.id
        store = await self._sdk.beta.memory_stores.create(
            name=store_name,
            description=f"Long-term trading memory for Condor agent '{self.slug}'.",
        )
        log.info("Created memory store %s for %s", store.id, self.slug)
        return store.id

    async def _check_session(self, session_id: str) -> str:
        """Return session_id if still usable, else empty string."""
        try:
            session = await self._sdk.beta.sessions.retrieve(session_id)
        except Exception:
            log.info("Persisted session %s not retrievable; creating new", session_id)
            return ""
        status = getattr(session, "status", "")
        if status == "terminated" or getattr(session, "archived_at", None):
            log.info("Persisted session %s is %s; creating new", session_id, status)
            return ""
        return session_id

    @staticmethod
    async def _aiter(maybe_stream: Any) -> AsyncIterator[Any]:
        """Iterate sync or async iterables/awaitables uniformly."""
        if inspect.isawaitable(maybe_stream):
            maybe_stream = await maybe_stream
        if hasattr(maybe_stream, "__aiter__"):
            async for item in maybe_stream:
                yield item
        else:
            for item in maybe_stream:
                yield item

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
        """Send one tick message and yield ACPEvents until the turn ends."""
        assert self._started and self._session_id, "Client not started"

        if self._bootstrap_pending and self.memory_bootstrap:
            text = (
                "[LOCAL LEARNINGS BOOTSTRAP — from Condor learnings.md; "
                "also curate /mnt/memory]\n"
                f"{self.memory_bootstrap}\n\n{text}"
            )
            self._bootstrap_pending = False

        events_api = self._sdk.beta.sessions.events

        # Open the stream BEFORE sending so no events are missed; then gate on
        # our user.message event id so replayed history (if any) is skipped.
        stream = events_api.stream(self._session_id)
        if inspect.isawaitable(stream):
            stream = await stream

        queue: asyncio.Queue[Any] = asyncio.Queue()
        _SENTINEL = object()

        async def _pump() -> None:
            try:
                async for ev in stream:
                    queue.put_nowait(ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Managed session stream error: %s", e)
            finally:
                queue.put_nowait(_SENTINEL)

        pump_task = asyncio.create_task(_pump())

        try:
            resp = await events_api.send(
                self._session_id,
                events=[
                    {"type": "user.message", "content": [{"type": "text", "text": text}]}
                ],
            )
            sent_id = ""
            data = getattr(resp, "data", None) or []
            if data:
                sent_id = getattr(data[0], "id", "") or ""

            loop = asyncio.get_event_loop()
            start_time = loop.time()
            seen_sent = not sent_id  # no id -> process everything

            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    elapsed = loop.time() - start_time
                    if not seen_sent and elapsed > 60:
                        # Echo of our user.message never arrived -- stop gating
                        # rather than skipping the whole turn.
                        log.warning("user.message echo not seen after %.0fs; processing all events", elapsed)
                        seen_sent = True
                    yield Heartbeat(elapsed_seconds=elapsed)
                    continue

                if ev is _SENTINEL:
                    yield PromptDone(stop_reason="disconnected")
                    return

                ev_type = getattr(ev, "type", "")
                ev_id = getattr(ev, "id", "")

                if not seen_sent:
                    if ev_id == sent_id:
                        seen_sent = True
                    continue

                done = False
                async for out in self._handle_event(ev, ev_type):
                    yield out
                    if isinstance(out, PromptDone):
                        done = True
                if done:
                    return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass
            close = getattr(stream, "close", None)
            if close:
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Event mapping
    # ------------------------------------------------------------------

    async def _handle_event(self, ev: Any, ev_type: str) -> AsyncIterator[ACPEvent]:
        if ev_type == "agent.message":
            text = self._blocks_text(getattr(ev, "content", None))
            if text:
                yield TextChunk(text=text)

        elif ev_type == "agent.thinking":
            # Thinking events carry no text in the Managed Agents stream;
            # surface a marker so the UI shows activity.
            yield ThoughtChunk(text="(thinking)")

        elif ev_type == "agent.custom_tool_use":
            async for out in self._handle_custom_tool(ev):
                yield out

        elif ev_type == "agent.tool_use":
            # Built-in sandbox tools (bash, read, write -- incl. /mnt/memory)
            yield ToolCallEvent(
                tool_call_id=getattr(ev, "id", ""),
                title=getattr(ev, "name", "tool"),
                status="in_progress",
                kind="other",
                input=getattr(ev, "input", None),
            )

        elif ev_type == "agent.tool_result":
            output = self._blocks_text(getattr(ev, "content", None))
            yield ToolCallUpdate(
                tool_call_id=getattr(ev, "tool_use_id", "") or "",
                status="failed" if getattr(ev, "is_error", False) else "completed",
                output=output[:_SNAPSHOT_OUTPUT_CHARS] if output else None,
            )

        elif ev_type == "session.status_idle":
            stop = getattr(getattr(ev, "stop_reason", None), "type", "end_turn")
            if stop == "requires_action":
                return  # waiting on a custom tool result we already sent
            if stop == "retries_exhausted":
                yield TextChunk(text="(session retries exhausted)")
                yield PromptDone(stop_reason="error")
                return
            yield PromptDone(stop_reason="end_turn")

        elif ev_type == "session.error":
            err = getattr(ev, "error", None)
            msg = getattr(err, "message", None) or getattr(err, "type", "unknown")
            yield TextChunk(text=f"(managed session error: {msg})")
            yield PromptDone(stop_reason="error")

        elif ev_type in ("session.status_terminated", "session.deleted"):
            yield PromptDone(stop_reason="disconnected")

        # All other event types (status_running, spans, threads, compaction,
        # echoes of our own user.* events) are ignored.

    async def _handle_custom_tool(self, ev: Any) -> AsyncIterator[ACPEvent]:
        tool_use_id = getattr(ev, "id", "")
        name = getattr(ev, "name", "")
        tool_input = getattr(ev, "input", None) or {}

        # Same risk gate as the ACP / pydantic-ai paths
        if self.permission_callback:
            tool_call_info = {"tool": name, "title": name, "input": tool_input}
            options = [
                {"optionId": "allow", "kind": "allow_once"},
                {"optionId": "deny", "kind": "deny"},
            ]
            result = await self.permission_callback(tool_call_info, options)
            outcome = result.get("outcome", {})
            if isinstance(outcome, dict) and outcome.get("outcome") == "cancelled":
                yield ToolCallEvent(
                    tool_call_id=tool_use_id,
                    title=name,
                    status="blocked",
                    kind="mcp",
                    input=tool_input,
                )
                await self._send_tool_result(
                    tool_use_id, _blocked_result_text(result), True
                )
                return

        yield ToolCallEvent(
            tool_call_id=tool_use_id,
            title=name,
            status="in_progress",
            kind="mcp",
            input=tool_input,
        )

        output, is_error = await self._bridge.call(name, tool_input)
        await self._send_tool_result(tool_use_id, output, is_error)

        yield ToolCallUpdate(
            tool_call_id=tool_use_id,
            status="failed" if is_error else "completed",
            output=output[:_SNAPSHOT_OUTPUT_CHARS],
        )

    async def _send_tool_result(self, tool_use_id: str, text: str, is_error: bool) -> None:
        try:
            await self._sdk.beta.sessions.events.send(
                self._session_id,
                events=[
                    {
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": text or "(empty result)"}],
                        "is_error": is_error,
                    }
                ],
            )
        except Exception:
            log.exception("Failed to send custom tool result for %s", tool_use_id)

    @staticmethod
    def _blocks_text(blocks: Any) -> str:
        if not blocks:
            return ""
        parts = []
        for block in blocks:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts)
