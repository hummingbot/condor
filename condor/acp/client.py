"""ACP subprocess client -- spawns an agent and speaks JSON-RPC 2.0 over stdio.

Uses the standard ACP v1 protocol: initialize -> session/new -> session/prompt,
streaming via session/update notifications.
"""

from __future__ import annotations

import asyncio
import hashlib
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


def normalize_tool_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Canonical ``{tool, title, input}`` view of an ACP ``toolCall`` (SEC-093).

    The ACP wire carries a tool call's arguments under ``rawInput``; every
    consumer in this repo reads ``input`` (``is_dangerous_tool_call``,
    ``condor.agents.risk``, ``format_tool_summary``, the transcript recorder).
    Left untranslated, the whole action-gated half of the danger list resolved
    ``action == ""`` and took the auto-approve fast path, so no confirmation
    was ever raised. This is the single seam that translates the wire shape —
    consumers stay on one contract instead of each learning ACP's spelling.

    The arguments are passed through **as they arrive**, without coercing a
    missing or malformed value into ``{}``: the gate has to be able to tell
    "no arguments I can read" from "an empty argument set", and fail closed on
    the former.
    """
    title = payload.get("title") or ""
    normalized = dict(payload)
    normalized["title"] = title
    normalized["tool"] = payload.get("tool") or title
    args = payload.get("rawInput")
    if args is None:
        args = payload.get("input")
    normalized["input"] = args
    return normalized


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


def _ps_rows() -> list[tuple[int, int, str]]:
    """``(pid, ppid, args)`` for every process, from one ``ps`` snapshot."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return []
    rows: list[tuple[int, int, str]] = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def bot_process_marker(token: str) -> str:
    """Non-secret argv marker identifying subprocesses spawned by THIS bot.

    ``ps`` output is world-readable, so the bot token itself must never sit on a
    child's command line (SEC-095). A digest of the token gives the same
    discrimination the raw token gave — one running bot's trees vs. another's,
    vs. an interactive Claude Code session — while revealing nothing: the hash
    is one-way, and the token is a high-entropy secret so it cannot be guessed
    back from it.

    Empty token → empty marker, so a tokenless dev run tags nothing (and the
    reaper, which refuses an empty marker, seeds on nothing).
    """
    if not token:
        return ""
    return "condor-bot-" + hashlib.sha256(token.encode()).hexdigest()[:12]


def reap_stale_acp_trees(token: str, *, wait_s: float = 2.0) -> int:
    """Kill leaked ACP/MCP subprocess trees from a prior crashed run.

    A hard kill (``kill -9``, OOM, power loss) bypasses the graceful shutdown
    path, orphaning the ``claude-agent-acp → claude → MCP`` tree. Call this at
    startup, BEFORE spawning any of our own subprocesses: at that point anything
    carrying this bot's process marker is necessarily a stale leak. We seed on
    those, climb to the owning ``claude-agent-acp`` root, and kill the whole
    tree. Interactive Claude Code sessions are never touched (their MCP servers
    carry no marker, and we explicitly exclude their signatures).

    Returns the number of processes signalled.
    """
    marker = bot_process_marker(token)
    if not marker:
        return 0
    rows = _ps_rows()
    if not rows:
        return 0
    args_of = {pid: args for pid, _, args in rows}
    parent_of = {pid: ppid for pid, ppid, _ in rows}

    def _protected(a: str) -> bool:
        return "dangerously-skip-permissions" in a or "claude-code-acp" in a

    # Seeds: our own MCP servers are launched with --bot-id <marker>.
    seeds = [pid for pid, _, args in rows if marker in args and not _protected(args)]
    if not seeds:
        return 0

    def _acp_ish(a: str) -> bool:
        return (
            "claude-agent-acp" in a
            or a.strip() == "claude"
            or "mcp_servers" in a
            or "uv run" in a
        )

    roots: set[int] = set()
    for seed in seeds:
        cur, root = seed, seed
        while True:
            p = parent_of.get(cur)
            if not p or p == 1 or not _acp_ish(args_of.get(p, "")):
                break
            if _protected(args_of.get(p, "")):
                break
            root = cur = p
        roots.add(root)

    targets: set[int] = set()
    for root in roots:
        targets |= _descendant_pids(root)
        targets.add(root)
    targets = {p for p in targets if not _protected(args_of.get(p, ""))}
    if not targets:
        return 0

    _signal_all(targets, None, signal.SIGTERM)
    time.sleep(wait_s)
    survivors = {p for p in targets if _alive(p)}
    if survivors:
        _signal_all(survivors, None, signal.SIGKILL)
    return len(targets)


ACP_COMMANDS: dict[str, str] = {
    "claude-code": "claude-agent-acp",
    "claude-acp": "claude-agent-acp",  # model-configurable form: claude-acp:<model>
    "gemini": "npx @google/gemini-cli --acp",
    "copilot": "npx @github/copilot --acp --stdio",
    "codex": "npx @agentclientprotocol/codex-acp",
}

# Session markers Claude Code exports into the shells it spawns. If the bot was
# launched from inside a Claude Code session (`uv run python main.py` typed at a
# Claude Code prompt), main.py inherits them and passes them on to every ACP
# subprocess — and the `claude` CLI behind claude-agent-acp then refuses to boot
# with "Claude Code cannot be launched inside another Claude Code session". The
# bridge reports that as a bare `[-32603] Internal error` (data.details:
# "Query closed before response received"), which says nothing about the cause.
# Our ACP children are their own top-level sessions, so we drop the markers.
# Only the session-identity vars go — CLAUDE_CONFIG_DIR, ANTHROPIC_* and other
# real configuration must survive.
_CLAUDE_SESSION_ENV_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
)

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
        system_prompt: str = "",
    ):
        self.command = command
        self.working_dir = working_dir or os.getcwd()
        self.mcp_servers: list[dict[str, Any]] = mcp_servers or []
        self.permission_callback = permission_callback
        self.extra_env = extra_env
        # Text APPENDED to the host's own system prompt (see start()). The only
        # true system-level channel an ACP session has — everything else Condor
        # sends arrives as a user turn and loses the argument (FEAT-025).
        self.system_prompt = system_prompt
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
        # A turn the agent has not settled and that nobody is streaming any
        # more: one that ignored ``session/cancel``, or one whose consumer
        # walked away (a WS drop, a page reload, a cancelled prompt task).
        #
        # It matters because ACP ``session/update`` notifications carry only a
        # sessionId — no request id. One queue, no way to tell whose chunk is
        # whose: opening a second ``session/prompt`` while the first turn is
        # still generating had the tail of the old answer delivered as the
        # opening of the new one. So the next prompt waits for this to clear
        # instead of interleaving two turns on one queue.
        self._unsettled_req: int | None = None
        # Holds the reference to the fire-and-forget cancel a torn-down turn
        # sends, so the loop cannot collect the task before it is written.
        self._cancel_task: asyncio.Task | None = None
        self._peer.register_handler("session/update", self._on_session_update)
        self._peer.register_handler(
            "session/request_permission", self._on_request_permission
        )

    # --- Lifecycle ---

    def _session_new_params(self) -> dict[str, Any]:
        """Params for the ``session/new`` handshake.

        ``claude-agent-acp`` reads ``_meta.systemPrompt`` here and forwards it to
        the agent SDK: a bare string REPLACES the host preset, ``{"append": …}``
        adds to it. We append, so a bound Agent keeps Claude Code's tool
        discipline and only gains its own identity.

        This is the channel that decides *who the model thinks it is*. An MCP
        server's ``instructions`` do reach the model and it follows the routing
        rules in them, but they read as guidance from a tool server, not as
        identity — with them alone a bound Agent still answers "I'm Claude Code"
        (FEAT-025, measured both ways against bridge 0.21.0). A bridge that
        ignores ``_meta`` simply drops it; the handshake is unaffected.
        """
        params: dict[str, Any] = {
            "cwd": self.working_dir,
            "mcpServers": self.mcp_servers,
        }
        if self.system_prompt:
            params["_meta"] = {"systemPrompt": {"append": self.system_prompt}}
        return params

    async def start(self) -> None:
        """Spawn subprocess, run ACP handshake (initialize + session/new)."""
        env = dict(os.environ)
        for var in _CLAUDE_SESSION_ENV_VARS:
            env.pop(var, None)
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
                self._session_new_params(),
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

    def _drain_events(self) -> None:
        """Empty the event queue so stale events don't leak into the next prompt."""
        while True:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _cancel_locally(self, req_id: int) -> None:
        """Fallback cancel: stop relaying the turn, and remember it is unsettled.

        Used when the agent does not honour ``session/cancel``. The screen ends
        here — that is what the terminal event below is for — but the agent may
        well still be generating, so the request is deliberately LEFT pending:
        its settlement is the only signal we get that this turn's notifications
        have stopped arriving. ``_unsettled_req`` is what keeps the next prompt
        from opening a second turn on top of it.

        Only the turn being *streamed* owns the queue. Called for any other
        request — an abandoned turn whose cancel timed out in the background —
        this records the marker and touches nothing else, so a late fallback
        cannot cut short the turn that replaced it.
        """
        live = req_id == self._current_req_id
        if live:
            self._current_req_id = None
        future = self._peer._pending.get(req_id)
        if future is not None and not future.done():
            self._unsettled_req = req_id
        else:
            self._peer._pending.pop(req_id, None)
        if not live:
            return
        self._drain_events()
        # The consumer of prompt_stream is parked on the queue; hand it a
        # terminal event so the turn ends now rather than at the next heartbeat.
        self._event_queue.put_nowait(PromptDone(stop_reason="cancelled"))

    async def _send_cancel(self) -> bool:
        """Ask the agent to stop the current turn. True if the notice went out."""
        try:
            assert self._process and self._process.stdin
            await self._peer.send_notification(
                "session/cancel",
                {"sessionId": self._session_id},
                self._process.stdin,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - a dead pipe means fall back
            log.warning("ACP session/cancel could not be sent (%s)", exc)
            return False

    async def _settle_previous_turn(self) -> None:
        """Wait for a turn nobody is streaming any more to end at the agent.

        The gate that keeps two turns from sharing one event queue. Reached
        with something unsettled in two ways: an agent that ignored
        ``session/cancel`` (:meth:`_cancel_locally`), and a consumer that
        walked away mid-answer, which unwinds ``prompt_stream`` through its own
        cleanup without anyone awaiting the cancel.

        Both leave the subprocess generating into ``_event_queue``. Draining
        the queue does not help — the drain is a moment, the leak is a stream —
        so this asks again and then *waits*. An agent that will not settle
        within ``TIMEOUTS.prompt_settle`` fails this turn out loud, which is the
        honest outcome: the alternative is an answer with someone else's words
        in front of it.
        """
        from condor.runtime.timeouts import TIMEOUTS

        req_id = self._unsettled_req
        if req_id is None:
            req_id = self._current_req_id
        if req_id is None:
            return

        self._current_req_id = None
        future = self._peer._pending.get(req_id)
        if future is None or future.done() or not self.alive:
            # Nothing still generating: a dead subprocess emits nothing, and
            # the read loop cancels every pending future on its way out.
            self._peer._pending.pop(req_id, None)
            self._unsettled_req = None
            return

        self._unsettled_req = req_id
        await self._send_cancel()
        try:
            # Shielded for the same reason abort_prompt shields: the timeout
            # must abandon the wait, never the future that resolves it.
            await asyncio.wait_for(
                asyncio.shield(future), timeout=TIMEOUTS.prompt_settle
            )
        except asyncio.TimeoutError:
            log.warning(
                "Previous turn still generating %ss after cancel; refusing to "
                "overlap it with a new prompt",
                TIMEOUTS.prompt_settle,
            )
            raise RuntimeError(
                "The previous answer is still being written. Press Stop, or "
                "try again in a moment."
            ) from None
        except asyncio.CancelledError:
            # Ours to absorb only when the *request* died under us.
            if not future.cancelled():
                raise
        except Exception:  # noqa: BLE001 - failed is settled too
            pass

        self._unsettled_req = None
        self._peer._pending.pop(req_id, None)

    async def abort_prompt(self) -> None:
        """Cancel the in-flight prompt at the agent, not just locally.

        Sends ACP's ``session/cancel`` and waits for the agent to settle the
        pending ``session/prompt`` with stopReason ``"cancelled"``, so the
        model's context ends where the user's screen ended. Falls back to a
        local cancel+drain when the agent does not answer within
        ``TIMEOUTS.prompt_cancel`` — an agent that ignores the notification
        must not hang the caller.
        """
        # Imported here, not at module scope: condor.runtime.events imports
        # condor.acp, so a top-level import would close the cycle.
        from condor.runtime.timeouts import TIMEOUTS

        req_id = self._current_req_id
        if req_id is None:
            return

        future = self._peer._pending.get(req_id)
        if future is None or future.done() or not self.alive:
            self._cancel_locally(req_id)
            return

        if not await self._send_cancel():
            self._cancel_locally(req_id)
            return

        try:
            # Shielded: the timeout must not cancel the future itself, since
            # _on_response is what turns the agent's reply into a PromptDone.
            await asyncio.wait_for(
                asyncio.shield(future), timeout=TIMEOUTS.prompt_cancel
            )
        except asyncio.TimeoutError:
            log.warning(
                "Agent did not answer session/cancel in %ss — cancelling locally. "
                "It may still be generating.",
                TIMEOUTS.prompt_cancel,
            )
            self._cancel_locally(req_id)
            return
        except asyncio.CancelledError:
            # Only ours to absorb when the *request* died (e.g. the read loop
            # tore down every pending future). A cancellation aimed at this
            # task belongs to the caller.
            if not future.cancelled():
                raise
            self._cancel_locally(req_id)
            return
        except Exception:
            # The agent failed the request instead of cancelling it; _on_response
            # already mapped that onto a PromptDone.
            pass

        if self._current_req_id == req_id:
            self._current_req_id = None
        log.debug("ACP prompt cancelled at the agent")

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

        # No second turn until the previous one has actually ended at the
        # agent. Cancelling the old *future* is not enough: the subprocess does
        # not know about our futures and keeps pushing chunks onto the one
        # queue this turn is about to read from.
        await self._settle_previous_turn()

        # Now — and only now — is the queue drained meaningfully: with nothing
        # generating behind us, everything that arrives after this line was
        # produced by the turn below.
        self._drain_events()

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

        try:
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
        finally:
            # Reached on every way out, including the one that used to leak:
            # the consumer walking away mid-answer (a WS drop, a page reload, a
            # cancelled prompt task) unwinds this generator with the agent
            # still generating and nothing ever telling it to stop.
            if self._current_req_id == req_id:
                self._current_req_id = None
                unfinished = self._peer._pending.get(req_id)
                if unfinished is not None and not unfinished.done():
                    self._unsettled_req = req_id
                    # Not awaited: under GeneratorExit there may be no one left
                    # to await us. This is the ask; the next prompt asks again
                    # and *waits*, which is where correctness actually lives.
                    try:
                        self._cancel_task = asyncio.get_running_loop().create_task(
                            self._send_cancel()
                        )
                    except RuntimeError:  # no running loop: nothing to send on
                        pass

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
                    input=normalize_tool_call(update)["input"],
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

        # If we have a permission callback, delegate to it. The wire shape is
        # translated first (SEC-093) so the gate sees the arguments it decides
        # on, and a callback that blows up denies rather than escaping into the
        # RPC layer, where the error would surface as something other than a
        # refusal.
        if self.permission_callback:
            tool_call = normalize_tool_call(toolCall or {})
            try:
                return await self.permission_callback(tool_call, options)
            except Exception:
                log.exception(
                    "Permission callback failed for %s — denying",
                    tool_call.get("title") or "<unknown tool>",
                )
                return {"outcome": {"outcome": "cancelled"}}

        # Default: auto-approve
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}
