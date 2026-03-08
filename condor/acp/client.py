"""ACP subprocess client -- spawns an agent and speaks JSON-RPC 2.0 over stdio.

Supports two protocol variants:
- "claude" (session-based): initialize -> session/new -> session/prompt, streaming via session/update
- "gemini" (direct): initialize -> sendUserMessage, streaming via reverse-RPC callbacks
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Awaitable

from .jsonrpc import JSONRPCPeer

log = logging.getLogger(__name__)

ACP_COMMANDS: dict[str, str] = {
    "claude-code": "claude-agent-acp",
    "gemini": "gemini --experimental-acp",
}

# Which protocol variant each agent key uses
ACP_PROTOCOL: dict[str, str] = {
    "claude-code": "claude",
    "gemini": "gemini",
}


# --- Event types yielded by prompt_stream ---


@dataclass
class TextChunk:
    text: str


@dataclass
class ThoughtChunk:
    text: str


@dataclass
class ToolCallEvent:
    tool_call_id: str
    title: str
    status: str  # pending, in_progress, completed, failed
    kind: str = "other"


@dataclass
class ToolCallUpdate:
    tool_call_id: str
    status: str | None = None
    title: str | None = None


@dataclass
class PromptDone:
    stop_reason: str


@dataclass
class UsageUpdate:
    used: int  # tokens used in last turn
    size: int  # context window size
    cost_usd: float  # cumulative cost in USD


@dataclass
class Heartbeat:
    elapsed_seconds: float


ACPEvent = TextChunk | ThoughtChunk | ToolCallEvent | ToolCallUpdate | PromptDone | Heartbeat | UsageUpdate


# Type alias for the permission callback
PermissionCallback = Callable[[dict, list[dict]], Awaitable[dict]]


class ACPClient:
    """Manages the lifecycle of an ACP subprocess agent."""

    def __init__(
        self,
        command: str,
        working_dir: str | None = None,
        protocol: str = "claude",
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_callback: PermissionCallback | None = None,
        extra_env: dict[str, str] | None = None,
    ):
        self.command = command
        self.working_dir = working_dir or os.getcwd()
        self.protocol = protocol  # "claude" or "gemini"
        self.mcp_servers: list[dict[str, Any]] = mcp_servers or []
        self.permission_callback = permission_callback
        self.extra_env = extra_env
        self._process: asyncio.subprocess.Process | None = None
        self._peer = JSONRPCPeer()
        self._session_id: str | None = None
        self._read_task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue[ACPEvent | None] = asyncio.Queue()
        self._tool_call_counter = 0
        self._register_handlers()

    def _register_handlers(self) -> None:
        # Claude-style handlers (session-based protocol)
        self._peer.register_handler("session/update", self._on_session_update)
        self._peer.register_handler("session/request_permission", self._on_request_permission)

        # Gemini-style handlers (direct reverse-RPC)
        self._peer.register_handler(
            "streamAssistantMessageChunk", self._on_gemini_message_chunk
        )
        self._peer.register_handler("pushToolCall", self._on_gemini_push_tool_call)
        self._peer.register_handler("updateToolCall", self._on_gemini_update_tool_call)
        self._peer.register_handler(
            "requestToolCallConfirmation", self._on_gemini_request_confirmation
        )

    # --- Lifecycle ---

    async def start(self) -> None:
        """Spawn subprocess, run handshake."""
        env = dict(os.environ)
        if self.extra_env:
            env.update(self.extra_env)

        self._process = await asyncio.create_subprocess_shell(
            self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir,
            env=env,
            limit=10 * 1024 * 1024,
        )
        self._read_task = asyncio.create_task(self._read_loop())

        if self.protocol == "gemini":
            await self._start_gemini()
        else:
            await self._start_claude()

    async def _start_claude(self) -> None:
        """Claude-style handshake: initialize + session/new."""
        await self._peer.send_request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "condor", "version": "0.1.0"},
            },
            self._process.stdin,
        )
        result = await self._peer.send_request(
            "session/new",
            {"cwd": self.working_dir, "mcpServers": self.mcp_servers},
            self._process.stdin,
        )
        self._session_id = result["sessionId"]
        log.info("ACP session started (claude): %s", self._session_id)

    async def _start_gemini(self) -> None:
        """Gemini-style handshake: just initialize."""
        await self._peer.send_request(
            "initialize",
            {"protocolVersion": "0.0.9"},
            self._process.stdin,
        )
        log.info("ACP session started (gemini)")

    async def stop(self) -> None:
        """Terminate the subprocess."""
        self._peer.cancel_all()
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()

    @property
    def alive(self) -> bool:
        """Check if the subprocess is still running."""
        return self._process is not None and self._process.returncode is None

    # --- Read loop ---

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                await self._peer.handle_line(line.decode(), self._process.stdin)
        except asyncio.CancelledError:
            return  # Intentional shutdown via stop() -- skip sentinel
        except Exception:
            log.exception("ACP read loop error")

        # Subprocess died or stream ended -- unblock any consumer waiting on _event_queue
        self._peer.cancel_all()
        self._event_queue.put_nowait(PromptDone(stop_reason="disconnected"))

    # --- Prompt ---

    async def prompt(self, text: str) -> str:
        """One-shot prompt: send text, collect all agent message chunks, return joined."""
        chunks: list[str] = []
        async for event in self.prompt_stream(text):
            if isinstance(event, TextChunk):
                chunks.append(event.text)
        return "".join(chunks)

    async def prompt_stream(self, text: str) -> AsyncIterator[ACPEvent]:
        """Send a prompt and yield ACP events as they arrive."""
        assert self._process

        # Clear the event queue
        while not self._event_queue.empty():
            self._event_queue.get_nowait()

        if self.protocol == "gemini":
            async for event in self._prompt_stream_gemini(text):
                yield event
        else:
            async for event in self._prompt_stream_claude(text):
                yield event

    async def _prompt_stream_claude(self, text: str) -> AsyncIterator[ACPEvent]:
        """Claude-style: session/prompt is a request, session/update are notifications."""
        assert self._session_id

        # Send request without awaiting so read loop can dispatch notifications
        req_id = self._peer._next_id
        self._peer._next_id += 1
        msg = {
            "jsonrpc": "2.0",
            "method": "session/prompt",
            "params": {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": text}],
            },
            "id": req_id,
        }
        self._process.stdin.write((json.dumps(msg) + "\n").encode())
        await self._process.stdin.drain()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._peer._pending[req_id] = future

        def _on_response(fut: asyncio.Future) -> None:
            if fut.cancelled():
                self._event_queue.put_nowait(PromptDone(stop_reason="cancelled"))
            elif fut.exception():
                self._event_queue.put_nowait(PromptDone(stop_reason="error"))
            else:
                result = fut.result()
                reason = (
                    result.get("stopReason", "end_turn")
                    if isinstance(result, dict)
                    else "end_turn"
                )
                # Extract accumulated token usage from prompt response
                if isinstance(result, dict) and "usage" in result:
                    usage = result["usage"]
                    total = usage.get("totalTokens", 0)
                    size = usage.get("contextWindow", 200000)
                    cost = result.get("cost", {}) or {}
                    self._event_queue.put_nowait(
                        UsageUpdate(
                            used=total,
                            size=size,
                            cost_usd=cost.get("amount", 0.0),
                        )
                    )
                self._event_queue.put_nowait(PromptDone(stop_reason=reason))

        future.add_done_callback(_on_response)

        loop = asyncio.get_event_loop()
        start_time = loop.time()
        max_duration = 1860  # 31 min hard ceiling (slightly above session-level timeout)

        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=30)
            except asyncio.TimeoutError:
                elapsed = loop.time() - start_time
                if not self.alive:
                    yield PromptDone(stop_reason="disconnected")
                    break
                if elapsed > max_duration:
                    log.warning("Prompt hard timeout after %.0fs", elapsed)
                    yield PromptDone(stop_reason="timeout")
                    break
                yield Heartbeat(elapsed_seconds=elapsed)
                continue
            if event is None:
                break
            yield event
            if isinstance(event, PromptDone):
                break

    async def _prompt_stream_gemini(self, text: str) -> AsyncIterator[ACPEvent]:
        """Gemini-style: sendUserMessage is a request, streaming via reverse-RPC."""
        # Send request without awaiting
        req_id = self._peer._next_id
        self._peer._next_id += 1
        msg = {
            "jsonrpc": "2.0",
            "method": "sendUserMessage",
            "params": {"chunks": [{"text": text}]},
            "id": req_id,
        }
        self._process.stdin.write((json.dumps(msg) + "\n").encode())
        await self._process.stdin.drain()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._peer._pending[req_id] = future

        def _on_response(fut: asyncio.Future) -> None:
            self._event_queue.put_nowait(PromptDone(stop_reason="end_turn"))

        future.add_done_callback(_on_response)

        loop = asyncio.get_event_loop()
        start_time = loop.time()
        max_duration = 660  # 11 min hard ceiling

        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=30)
            except asyncio.TimeoutError:
                elapsed = loop.time() - start_time
                if not self.alive:
                    yield PromptDone(stop_reason="disconnected")
                    break
                if elapsed > max_duration:
                    log.warning("Prompt hard timeout after %.0fs", elapsed)
                    yield PromptDone(stop_reason="timeout")
                    break
                yield Heartbeat(elapsed_seconds=elapsed)
                continue
            if event is None:
                break
            yield event
            if isinstance(event, PromptDone):
                break

    # --- Claude-style reverse-RPC handlers ---

    def _on_session_update(
        self, sessionId: str, update: dict[str, Any], _meta: dict | None = None, **kw: Any
    ) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content", {})
            text = content.get("text", "")
            if text:
                self._event_queue.put_nowait(TextChunk(text=text))
        elif kind == "agent_thought_chunk":
            content = update.get("content", {})
            text = content.get("text", "")
            if text:
                self._event_queue.put_nowait(ThoughtChunk(text=text))
        elif kind == "tool_call":
            self._event_queue.put_nowait(
                ToolCallEvent(
                    tool_call_id=update.get("toolCallId", ""),
                    title=update.get("title", ""),
                    status=update.get("status", "pending"),
                    kind=update.get("kind", "other"),
                )
            )
        elif kind == "tool_call_update":
            self._event_queue.put_nowait(
                ToolCallUpdate(
                    tool_call_id=update.get("toolCallId", ""),
                    status=update.get("status"),
                    title=update.get("title"),
                )
            )
        elif kind == "usage_update":
            cost = update.get("cost") or {}
            self._event_queue.put_nowait(
                UsageUpdate(
                    used=update.get("used", 0),
                    size=update.get("size", 200000),
                    cost_usd=cost.get("amount", 0.0),
                )
            )

    # --- Gemini-style reverse-RPC handlers ---

    def _on_gemini_message_chunk(self, chunk: dict[str, Any] = None, **kw: Any) -> None:
        if not chunk:
            return
        if "text" in chunk:
            self._event_queue.put_nowait(TextChunk(text=chunk["text"]))
        elif "thought" in chunk:
            self._event_queue.put_nowait(ThoughtChunk(text=chunk["thought"]))

    def _on_gemini_push_tool_call(self, **kw: Any) -> dict[str, str]:
        self._tool_call_counter += 1
        tc_id = f"tc-{self._tool_call_counter}"
        label = kw.get("label", "tool call")
        self._event_queue.put_nowait(
            ToolCallEvent(tool_call_id=tc_id, title=label, status="in_progress")
        )
        return {"id": tc_id}

    def _on_gemini_update_tool_call(self, **kw: Any) -> None:
        tc_id = kw.get("toolCallId", "")
        status = kw.get("status", "")
        # Map gemini statuses to our events
        mapped_status = {"finished": "completed", "error": "failed"}.get(status, status)
        self._event_queue.put_nowait(
            ToolCallUpdate(tool_call_id=tc_id, status=mapped_status)
        )

    async def _on_gemini_request_confirmation(self, **kw: Any) -> dict[str, Any]:
        self._tool_call_counter += 1
        tc_id = f"tc-{self._tool_call_counter}"
        label = kw.get("label", "tool call")
        self._event_queue.put_nowait(
            ToolCallEvent(tool_call_id=tc_id, title=label, status="in_progress")
        )
        if self.permission_callback:
            tool_call = {"title": label, "id": tc_id, **kw}
            options = [
                {"optionId": "allow", "kind": "allow_once", "label": "Allow"},
                {"optionId": "deny", "kind": "deny", "label": "Deny"},
            ]
            result = await self.permission_callback(tool_call, options)
            outcome = result.get("outcome", {})
            if isinstance(outcome, dict) and outcome.get("optionId") == "allow":
                return {"outcome": "allow", "id": tc_id}
            return {"outcome": "deny", "id": tc_id}
        # Auto-approve when no callback
        return {"outcome": "allow", "id": tc_id}

    # --- Permission handler ---

    async def _on_request_permission(
        self,
        sessionId: str = "",
        options: list[dict[str, Any]] | None = None,
        toolCall: dict[str, Any] | None = None,
        _meta: dict | None = None,
        **kw: Any,
    ) -> dict[str, Any]:
        options = options or []

        # If we have a permission callback, delegate to it
        if self.permission_callback:
            return await self.permission_callback(toolCall or {}, options)

        # Default: auto-approve
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
        return {"outcome": {"outcome": "cancelled"}}
