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
import subprocess
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from .jsonrpc import JSONRPCPeer

log = logging.getLogger(__name__)


def _descendant_pids(root: int) -> set[int]:
    """Every transitive child PID of ``root``, from a single ``ps`` snapshot.

    Used at teardown to find MCP server subprocesses that ``claude`` spawns in
    their OWN process groups (so ``killpg`` of our group misses them). Must be
    called BEFORE the parent dies — once it exits the children reparent to init
    and the ppid links that identify them are gone.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return set()
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _signal_all(pids: set[int], pgid: int | None, sig: int) -> None:
    """Send ``sig`` to the process group (if known) and every PID directly."""
    if pgid is not None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


ACP_COMMANDS: dict[str, str] = {
    "claude-code": "claude-agent-acp",
    "claude-acp": "claude-agent-acp",  # model-configurable form: claude-acp:<model>
    "gemini": "npx @google/gemini-cli --acp",
    "copilot": "npx @github/copilot --acp --stdio",
    "codex": "npx @zed-industries/codex-acp",
}

# ACP bases whose model can be picked via a suffix (e.g. "claude-acp:opus").
# The suffix is selected at runtime via session/set_model against the agent's
# advertised models (see ACPClient._select_model), which resolves aliases
# ("opus", "sonnet", "haiku") and full ids alike — so no hardcoded ids age here.
# NOTE: claude-agent-acp ignores ANTHROPIC_MODEL; the protocol is the real lever.
_CLAUDE_ACP_BASES = {"claude-code", "claude-acp"}


def resolve_acp(agent_key: str) -> tuple[str, dict[str, str], str]:
    """Resolve an ACP ``agent_key`` to ``(command, env-overrides, model-pref)``.

    Supports an optional model suffix for Claude, e.g. ``"claude-acp:opus"`` or
    ``"claude-acp:claude-opus-4-8"``. A bare key ("claude-code"/"claude-acp") sets
    no preference, so the agent keeps its own default. Non-Claude bases ignore any
    suffix.

    The suffix is returned as ``model-pref`` so the caller can select it over the
    ACP protocol (``session/set_model``) — the ``claude-agent-acp`` bridge does NOT
    read ``ANTHROPIC_MODEL`` (it picks from Claude Code ``settings.model`` or the
    first advertised model), so env is not a reliable channel. We still set
    ``ANTHROPIC_MODEL`` for any non-bridge consumer, but ACPClient drives the model
    via the protocol.
    """
    base, _, model = agent_key.partition(":")
    command = ACP_COMMANDS.get(base, ACP_COMMANDS["claude-code"])
    env: dict[str, str] = {}
    model_pref = ""
    if model and base in _CLAUDE_ACP_BASES:
        env["ANTHROPIC_MODEL"] = model
        model_pref = model
    return command, env, model_pref


def resolve_model_id(preference: str, available_models: list[dict]) -> str | None:
    """Map a model ``preference`` (e.g. "sonnet", "claude-sonnet-4-6") to an exact
    advertised ``modelId`` from the ACP agent's ``availableModels``.

    The ``session/set_model`` request needs an EXACT id — the bridge does not
    fuzzy-match there (unlike its own settings.model handling). We mirror its
    matching: exact id/name, then substring, so a short alias like "sonnet" still
    resolves. Returns ``None`` if nothing matches (caller keeps the default).
    """
    if not preference or not available_models:
        return None
    pref = preference.strip().lower()

    def fields(m: dict) -> tuple[str, str]:
        return (str(m.get("modelId", "")).lower(), str(m.get("name", "")).lower())

    # Exact match on id or display name.
    for m in available_models:
        mid, name = fields(m)
        if pref in (mid, name):
            return m.get("modelId")
    # Substring match either direction (handles "sonnet" ⊂ "claude-sonnet-4-6").
    for m in available_models:
        mid, name = fields(m)
        if (mid and (pref in mid or mid in pref)) or (name and pref in name):
            return m.get("modelId")
    return None


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


ACPEvent = (
    TextChunk | ThoughtChunk | ToolCallEvent | ToolCallUpdate | PromptDone | Heartbeat
)


def fold_tool_call_event(
    tc_map: dict[str, dict], event: ToolCallEvent | ToolCallUpdate
) -> dict | None:
    """Fold a streamed tool-call event into ``tc_map``, keyed by tool_call_id.

    Shared reduction used by ``TickEngine._tick`` and ``delegate._make_event_sink``
    so the create/patch semantics can't drift (ARCH-063). A :class:`ToolCallEvent`
    creates an entry (returned so the caller can append it to its own list) or
    patches ``status``/``name``/``input`` in place; a :class:`ToolCallUpdate`
    patches ``status``/``name``/``output``. Returns the newly created entry, or
    ``None`` when the event patched an existing (or unknown) one.
    """
    if isinstance(event, ToolCallEvent):
        tc = tc_map.get(event.tool_call_id)
        if tc is None:
            tc = {
                "id": event.tool_call_id,
                "name": event.title,
                "status": event.status,
                "kind": event.kind,
            }
            if event.input:
                tc["input"] = event.input
            tc_map[event.tool_call_id] = tc
            return tc
        tc["status"] = event.status
        if event.title:
            tc["name"] = event.title
        if event.input:
            tc["input"] = event.input
    else:  # ToolCallUpdate
        tc = tc_map.get(event.tool_call_id)
        if tc is not None:
            if event.status:
                tc["status"] = event.status
            if event.title:
                tc["name"] = event.title
            if event.output:
                tc["output"] = event.output
    return None


def _extract_tool_output(update: dict) -> str | None:
    """Tool result across ACP payload variants: ``rawOutput`` (spec), legacy
    ``output``, else the text of any ``content`` blocks. Coerced to str so the
    transcript/snapshot writer can persist it uniformly."""
    raw = update.get("rawOutput")
    if raw is None:
        raw = update.get("output")
    if raw is None:
        texts = []
        for block in update.get("content") or []:
            inner = block.get("content") if isinstance(block, dict) else None
            if isinstance(inner, dict) and inner.get("type") == "text":
                texts.append(inner.get("text", ""))
        return "\n".join(texts) if texts else None
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw)
    except (TypeError, ValueError):
        return str(raw)


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
        model: str | None = None,
    ):
        self.command = command
        self.working_dir = working_dir or os.getcwd()
        self.mcp_servers: list[dict[str, Any]] = mcp_servers or []
        self.permission_callback = permission_callback
        self.extra_env = extra_env
        # Requested model preference (e.g. "sonnet"); selected over the ACP
        # protocol after session/new since the bridge ignores ANTHROPIC_MODEL.
        self.model = model
        self.active_model_id: str | None = None  # resolved id actually in effect
        self._process: asyncio.subprocess.Process | None = None
        self._peer = JSONRPCPeer()
        self._session_id: str | None = None
        self._read_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue[ACPEvent | None] = asyncio.Queue()
        self._current_req_id: int | None = None  # tracks in-flight prompt request
        self._peer.register_handler("session/update", self._on_session_update)
        self._peer.register_handler(
            "session/request_permission", self._on_request_permission
        )

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
            # Re-express our MCP servers in the SDK-native shape with
            # ``alwaysLoad: True`` so their tools are EAGERLY included in the
            # prompt instead of being deferred behind ToolSearch. Deferred
            # loading is what made every tick spend 15-25 tool calls just
            # *finding* the agent's tools (and intermittently failing to, then
            # reconstructing schemas from source) — vs ~4 calls a tick when tools
            # are eager. alwaysLoad blocks turn-1 until the server connects (5s
            # cap; ours connect in <1s), which also closes the registration race.
            # It must be passed via _meta because the bridge's top-level
            # mcpServers mapping drops alwaysLoad.
            sdk_mcp_servers: dict[str, dict[str, Any]] = {}
            for s in self.mcp_servers:
                raw_env = s.get("env") or []
                env_dict = (
                    {e["name"]: e["value"] for e in raw_env}
                    if isinstance(raw_env, list)
                    else dict(raw_env)
                )
                cfg: dict[str, Any] = {
                    "type": "stdio",
                    "command": s["command"],
                    "args": s.get("args", []),
                    "alwaysLoad": True,
                }
                if env_dict:
                    cfg["env"] = env_dict
                sdk_mcp_servers[s["name"]] = cfg
            result = await self._peer.send_request(
                "session/new",
                {
                    "cwd": self.working_dir,
                    # Empty here on purpose: the bridge maps this top-level list
                    # into the SDK config WITHOUT alwaysLoad, and (acp-agent.js
                    # ~L1388) it overrides the _meta servers on key collision. So
                    # _meta is the single source of our servers below.
                    "mcpServers": [],
                    "_meta": {
                        "claudeCode": {
                            "options": {
                                # strictMcpConfig: ignore the user's global
                                # file-based MCP servers (resend/vercel/gmail/…).
                                # Without it the bridge merges all of them on top
                                # of ours, ballooning the tool surface and forcing
                                # deferred loading. permissions/skills/hooks from
                                # settingSources are unaffected.
                                "strictMcpConfig": True,
                                # Our server(s), eager-loaded (alwaysLoad above).
                                "mcpServers": sdk_mcp_servers,
                            }
                        }
                    },
                },
                self._process.stdin,
            )
        except Exception:
            # Handshake failed -- kill the subprocess to prevent orphan
            await self.stop()
            raise

        self._session_id = result["sessionId"]
        log.info("ACP session started: %s (cmd=%s)", self._session_id, self.command)

        # Select the requested model over the ACP protocol. The claude-agent-acp
        # bridge does NOT honor ANTHROPIC_MODEL — it defaults to Claude Code's
        # settings.model or the first advertised model — so the only reliable way
        # to pin (e.g.) Sonnet is session/set_model with an exact advertised id.
        await self._select_model(result.get("models") or {})

    async def _select_model(self, model_state: dict) -> None:
        """Resolve ``self.model`` against advertised models and set it via ACP.

        ``model_state`` is the ``session/new`` response's ``models`` block
        (``{availableModels: [...], currentModelId: ...}``). No-op when no model
        was requested or it can't be matched — we log either way so the effective
        model is verifiable from the bot logs rather than the model's self-report.
        """
        available = model_state.get("availableModels") or []
        current = model_state.get("currentModelId")
        self.active_model_id = current
        if not self.model:
            log.info("ACP session %s using default model %s", self._session_id, current)
            return
        target = resolve_model_id(self.model, available)
        if not target:
            log.warning(
                "ACP model %r not found in advertised models %s; keeping default %s",
                self.model,
                [m.get("modelId") for m in available],
                current,
            )
            return
        if target == current:
            log.info(
                "ACP session %s already on requested model %s", self._session_id, target
            )
            return
        try:
            await self._peer.send_request(
                "session/set_model",
                {"sessionId": self._session_id, "modelId": target},
                self._process.stdin,
            )
            self.active_model_id = target
            log.info(
                "ACP session %s model set to %s (requested %r)",
                self._session_id,
                target,
                self.model,
            )
        except Exception:
            log.exception(
                "ACP session/set_model failed for %r; staying on %s",
                self.model,
                current,
            )

    async def stop(self) -> None:
        """Terminate the subprocess and ALL descendants (claude + MCP servers).

        ``claude`` spawns each MCP stdio server in its own process group, so a
        lone ``killpg`` of our group leaks them — and ``claude`` itself ignores
        SIGTERM. We snapshot the full descendant tree *before* killing (after the
        parent dies the children reparent to init and the tree is lost), then
        signal the process group AND every descendant PID directly.
        """
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
            # Snapshot descendants now — reparenting after death destroys the links.
            pids = await asyncio.to_thread(_descendant_pids, pid)
            pids.add(pid)
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError):
                pgid = None

            _signal_all(pids, pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                log.warning("ACP process %d ignored SIGTERM; escalating", pid)

            # Re-scan in case the tree shifted, then SIGKILL anything still alive.
            survivors = {
                p
                for p in (pids | await asyncio.to_thread(_descendant_pids, pid))
                if _alive(p)
            }
            if survivors:
                _signal_all(survivors, pgid, signal.SIGKILL)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    log.warning("ACP process %d could not be reaped", pid)
            log.debug("ACP process tree for %d stopped (%d pids)", pid, len(pids))
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

    def abort_prompt(self) -> None:
        """Cancel the in-flight prompt request and drain the event queue.

        The ACP subprocess will keep running (there's no protocol-level cancel),
        but the next prompt_stream call will start clean.
        """
        if self._current_req_id is not None:
            future = self._peer._pending.pop(self._current_req_id, None)
            if future and not future.done():
                future.cancel()
            self._current_req_id = None
        # Drain the queue so stale events don't leak into the next prompt
        while True:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        log.debug("ACP prompt aborted, queue drained")

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

        # Cancel any previous in-flight prompt (e.g. after abort)
        if self._current_req_id is not None:
            old_future = self._peer._pending.pop(self._current_req_id, None)
            if old_future and not old_future.done():
                old_future.cancel()
            self._current_req_id = None

        # Clear the event queue (stale events from a previous prompt)
        while not self._event_queue.empty():
            self._event_queue.get_nowait()

        # Send request without awaiting so read loop can dispatch notifications
        req_id = self._peer._next_id
        self._peer._next_id += 1
        self._current_req_id = req_id
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
            # Only enqueue PromptDone if this is still the current prompt
            if self._current_req_id != req_id:
                return  # stale response from an aborted prompt — ignore
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
        max_duration = (
            1860  # 31 min hard ceiling (slightly above session-level timeout)
        )

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

        # Clear current request tracking when prompt completes normally
        if self._current_req_id == req_id:
            self._current_req_id = None

    # --- Reverse-RPC handlers ---

    def _on_session_update(
        self,
        sessionId: str,
        update: dict[str, Any],
        _meta: dict | None = None,
        **kw: Any,
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
                    # ACP sends the arguments as rawInput; "input" is a legacy
                    # variant. Without this fallback the audit trail records
                    # tool names with no arguments.
                    input=update.get("rawInput") or update.get("input"),
                )
            )
        elif kind == "tool_call_update":
            self._event_queue.put_nowait(
                ToolCallUpdate(
                    tool_call_id=update.get("toolCallId", ""),
                    status=update.get("status"),
                    title=update.get("title"),
                    output=_extract_tool_output(update),
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
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}
