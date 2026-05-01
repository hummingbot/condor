"""ACP subprocess client -- spawns an agent and speaks JSON-RPC 2.0 over stdio.

Uses the standard ACP v1 protocol: initialize -> session/new -> session/prompt,
streaming via session/update notifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Awaitable

from .jsonrpc import JSONRPCPeer

log = logging.getLogger(__name__)

ACP_COMMANDS: dict[str, str] = {
    "claude-code": "claude-agent-acp",
    "gemini": "gemini --experimental-acp",
    "copilot": "copilot --acp",
    "codex": "npx @zed-industries/codex-acp"
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
    input: dict | None = None


@dataclass
class ToolCallUpdate:
    tool_call_id: str
    status: str | None = None
    title: str | None = None
    output: str | None = None


@dataclass
class PromptDone:
    stop_reason: str


@dataclass
class Heartbeat:
    elapsed_seconds: float


ACPEvent = TextChunk | ThoughtChunk | ToolCallEvent | ToolCallUpdate | PromptDone | Heartbeat


# Type alias for the permission callback
PermissionCallback = Callable[[dict, list[dict]], Awaitable[dict]]


class ACPClient:
    """Manages the lifecycle of an ACP subprocess agent."""

    def __init__(
        self,
        command: str,
        working_dir: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_callback: PermissionCallback | None = None,
        extra_env: dict[str, str] | None = None,
    ):
        self.command = command
        self.working_dir = working_dir or os.getcwd()
        self.mcp_servers: list[dict[str, Any]] = mcp_servers or []
        self.permission_callback = permission_callback
        self.extra_env = extra_env
        self._process: asyncio.subprocess.Process | None = None
        self._peer = JSONRPCPeer()
        self._session_id: str | None = None
        self._read_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue[ACPEvent | None] = asyncio.Queue()
        self._peer.register_handler("session/update", self._on_session_update)
        self._peer.register_handler("session/request_permission", self._on_request_permission)

    # --- Lifecycle ---

    async def start(self) -> None:
        """Spawn subprocess, run ACP handshake (initialize + session/new)."""
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
            start_new_session=True,  # Own process group so we can kill all children
        )
        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        try:
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
        except Exception:
            # Handshake failed -- kill the subprocess to prevent orphan
            await self.stop()
            raise

        self._session_id = result["sessionId"]
        log.info("ACP session started: %s (cmd=%s)", self._session_id, self.command)

    async def stop(self) -> None:
        """Terminate the subprocess and all its children (MCP servers)."""
        self._peer.cancel_all()
        for task in (self._read_task, self._stderr_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._process and self._process.returncode is None:
            pid = self._process.pid
            try:
                # Kill the entire process group (subprocess + MCP server children)
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    self._process.kill()
                # Always reap after SIGKILL to prevent zombies
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    log.warning("ACP process %d could not be reaped", pid)
            log.debug("ACP process group %d stopped", pid)
        # Clear reference so alive returns False even if reap failed
        self._process = None

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

    async def _drain_stderr(self) -> None:
        """Read and log stderr to prevent pipe buffer from filling up and blocking the subprocess."""
        assert self._process and self._process.stderr
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                if text:
                    log.debug("ACP stderr: %s", text)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("ACP stderr drain error")

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
        assert self._process and self._session_id

        # Clear the event queue
        while not self._event_queue.empty():
            self._event_queue.get_nowait()

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

    # --- Reverse-RPC handlers ---

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
                    input=update.get("input"),
                )
            )
        elif kind == "tool_call_update":
            self._event_queue.put_nowait(
                ToolCallUpdate(
                    tool_call_id=update.get("toolCallId", ""),
                    status=update.get("status"),
                    title=update.get("title"),
                    output=update.get("output"),
                )
            )

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
