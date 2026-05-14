"""Cursor Agent backend: Node subprocess + official `@cursor/sdk` (local runtime).

Passes Condor MCP stdio servers through to Composer as `AgentOptions.mcpServers`
(local Cursor only).

Note: Composer does not invoke Condor's Telegram `PermissionCallback`; risky tool
handlers may behave differently vs ACP / pydantic-ai sessions.

Requires Node 18+, `npm install` under `cursor_bridge/`, and `CURSOR_API_KEY`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from condor.acp.mcp_stdio import acp_mcp_list_to_cursor_mcp_servers

from condor.acp.client import (
    ACPEvent,
    Heartbeat,
    PermissionCallback,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BRIDGE_DIR = Path(__file__).resolve().parent / "cursor_bridge"
BRIDGE_SCRIPT = _BRIDGE_DIR / "bridge.mjs"


def is_cursor_sdk_model(agent_key: str) -> bool:
    prefix = agent_key.split(":", 1)[0] if ":" in agent_key else agent_key
    return prefix == "cursor"


def bridge_script_path() -> Path:
    return BRIDGE_SCRIPT


def cursor_runtime_ready() -> bool:
    """Node binary + packaged bridge script on disk."""
    node = os.environ.get("CONDOR_NODE_BIN", "node")
    return shutil.which(node) is not None and BRIDGE_SCRIPT.is_file()


def cursor_model_id(agent_key: str) -> str:
    """cursor:composer-2 -> composer-2; cursor -> auto (invalid key normalized)."""
    if not is_cursor_sdk_model(agent_key):
        raise ValueError(f"Not a Cursor model key: {agent_key}")
    _, _, rest = agent_key.partition(":")
    return rest.strip() if rest.strip() else "auto"


class CursorSdkClient:
    """Cursor Agent via Node bridge; mirrors ACP/Pydantic `start`/`stop`/`prompt_stream`."""

    def __init__(
        self,
        model: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_callback: PermissionCallback | None = None,
        extra_env: dict[str, str] | None = None,
        cwd: str | None = None,
        api_key: str | None = None,
    ):
        self._model_tag = model
        self._cwd = cwd
        self._api_key = api_key if api_key is not None else os.environ.get("CURSOR_API_KEY")
        # Composer MCP runs without Condor Telegram approval flow (see module docstring).
        self._permission_callback = permission_callback
        self._extra_env = extra_env or {}
        self._acp_mcp_servers: list[dict[str, Any]] = list(mcp_servers or [])
        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        if self._permission_callback is not None:
            log.debug(
                "CursorSdkClient receives permission_callback for API parity; Composer "
                "does not delegate MCP tool approvals to Condor Telegram."
            )

    async def start(self) -> None:
        node_bin = os.environ.get("CONDOR_NODE_BIN", "node")
        if not shutil.which(node_bin):
            raise RuntimeError(
                "Node.js is required for Cursor-backed sessions (Node 18+). "
                "Install Node and ensure it is on PATH, or set CONDOR_NODE_BIN."
            )
        if not BRIDGE_SCRIPT.is_file():
            raise RuntimeError(
                f"Cursor bridge missing at {BRIDGE_SCRIPT}. Run: cd {_BRIDGE_DIR} && npm install"
            )
        if not self._api_key:
            raise RuntimeError(
                "CURSOR_API_KEY is not set. See https://cursor.com/dashboard/cloud-agents "
                " mint a key, add it to .env, restart Condor."
            )

        cwd = self._cwd or os.environ.get("CONDOR_CURSOR_CWD") or str(PROJECT_ROOT)
        mid = cursor_model_id(self._model_tag)
        cursor_mcp = acp_mcp_list_to_cursor_mcp_servers(
            self._acp_mcp_servers,
            cwd,
            extra_env=self._extra_env,
        )

        env = dict(os.environ)
        env.update(self._extra_env)
        env["CURSOR_API_KEY"] = self._api_key

        self._process = await asyncio.create_subprocess_exec(
            node_bin,
            str(BRIDGE_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_BRIDGE_DIR),
            env=env,
            limit=10 * 1024 * 1024,
        )
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        self._drain_queue()
        init_msg: dict[str, Any] = {"op": "init", "cwd": cwd, "modelId": mid}
        if cursor_mcp:
            init_msg["mcpServers"] = cursor_mcp
        await self._write_line(init_msg)

        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                pkt = await asyncio.wait_for(self._queue.get(), timeout=5)
            except asyncio.TimeoutError:
                if not self.alive:
                    raise RuntimeError("Cursor bridge process exited during init") from None
                continue

            if pkt is None:
                raise RuntimeError("Cursor bridge disconnected during init")

            kind = pkt.get("kind")
            if kind == "ready":
                log.info(
                    "Cursor SDK bridge ready (model_id=%s, cwd=%s, mcp_servers=%d)",
                    mid,
                    cwd,
                    len(cursor_mcp),
                )
                return
            if kind == "error" and pkt.get("stage") == "init":
                raise RuntimeError(pkt.get("message", "Cursor bridge init failed"))

        raise RuntimeError("Cursor bridge init timed out")

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _write_line(self, obj: dict[str, Any]) -> None:
        assert self._process is not None
        assert self._process.stdin is not None
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._process is not None
        stdout = self._process.stdout
        assert stdout is not None
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                try:
                    payload = json.loads(raw.decode())
                except json.JSONDecodeError:
                    log.warning("Cursor bridge non-JSON line: %s", raw[:200])
                    continue
                await self._queue.put(payload)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Cursor bridge read loop failed")
        await self._queue.put(None)

    async def _drain_stderr(self) -> None:
        assert self._process is not None
        stderr = self._process.stderr
        assert stderr is not None
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                txt = line.decode(errors="replace").rstrip()
                if txt:
                    log.debug("Cursor bridge stderr: %s", txt)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Cursor bridge stderr drain error")

    async def stop(self) -> None:
        if self._process and self.alive:
            stdin = self._process.stdin
            if stdin:
                try:
                    await self._write_line({"op": "shutdown"})
                except Exception:
                    pass

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=15)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass

        self._process = None
        self._drain_queue()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def prompt(self, text: str) -> str:
        parts: list[str] = []
        async for event in self.prompt_stream(text):
            if isinstance(event, TextChunk):
                parts.append(event.text)
        return "".join(parts)

    async def prompt_stream(self, text: str) -> AsyncIterator[ACPEvent]:
        if not self.alive or self._process is None or self._process.stdin is None:
            raise RuntimeError("CursorSdkClient not started")

        self._drain_queue()
        prompt_id = str(uuid.uuid4())
        await self._write_line({"op": "prompt", "id": prompt_id, "text": text})

        loop = asyncio.get_event_loop()
        start_t = loop.time()

        while True:
            try:
                pkt = await asyncio.wait_for(self._queue.get(), timeout=30)
            except asyncio.TimeoutError:
                elapsed = loop.time() - start_t
                if not self.alive:
                    yield PromptDone(stop_reason="disconnected")
                    break
                yield Heartbeat(elapsed_seconds=elapsed)
                continue

            if pkt is None:
                yield PromptDone(stop_reason="disconnected")
                break

            kind = pkt.get("kind")
            pid = pkt.get("promptId")

            if pid not in (None, prompt_id) and kind != "error":
                continue

            if kind == "text" and pkt.get("text"):
                yield TextChunk(text=str(pkt["text"]))
            elif kind == "thinking" and pkt.get("text"):
                yield ThoughtChunk(text=str(pkt["text"]))
            elif kind == "tool":
                cid = str(pkt.get("call_id", ""))
                name = str(pkt.get("name", "tool"))
                yield ToolCallEvent(
                    tool_call_id=cid or name,
                    title=name,
                    status="in_progress",
                    kind="other",
                    input=pkt.get("input") if isinstance(pkt.get("input"), dict) else {"raw": pkt.get("input")},
                )
            elif kind == "tool_status":
                cid = str(pkt.get("call_id", ""))
                st = pkt.get("status", "completed")
                out_txt = pkt.get("result")
                try:
                    out_str = json.dumps(out_txt, default=str, ensure_ascii=False) if out_txt is not None else None
                except TypeError:
                    out_str = str(out_txt)

                pmap = {"running": "in_progress", "completed": "completed", "error": "failed"}
                status = pmap.get(str(st), "completed")

                yield ToolCallUpdate(
                    tool_call_id=cid or str(pkt.get("name", "")),
                    status=status,
                    title=str(pkt.get("name", "")),
                    output=out_str,
                )
            elif kind == "error":
                msg = pkt.get("message", "Unknown error")
                if pkt.get("promptId") in (prompt_id, None):
                    yield TextChunk(text=f"\n[Cursor] {msg}\n")
            elif kind == "done" and pid == prompt_id:
                yield PromptDone(stop_reason=str(pkt.get("stopReason", "end_turn")))
                break
