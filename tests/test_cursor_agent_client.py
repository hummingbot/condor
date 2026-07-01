"""Tests for CursorAgentClient -- Cursor SDK provider (faked, no network)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from condor.acp.client import PromptDone, TextChunk, ToolCallEvent, ToolCallUpdate
from condor.acp.cursor_agent_client import (
    DEFAULT_CURSOR_MODEL,
    CursorAgentClient,
    is_cursor_agent_key,
    is_cursor_provider_error,
    is_recoverable_cursor_error,
    reset_cursor_bridge,
    resolve_cursor_model,
)


class InternalServerError(Exception):
    """Stand-in for cursor_sdk.errors.InternalServerError in tests."""


class _FakeRun:
    def __init__(self, messages, status="finished", result=""):
        self._messages = list(messages)
        self.status = status
        self.result = result

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def messages(self):
        for m in self._messages:
            yield m

    async def wait(self):
        return SimpleNamespace(status=self.status, result=self.result)

    async def cancel(self):
        self.status = "cancelled"


class _FakeAgent:
    def __init__(self, sdk, fail_first_send: bool = False):
        self.sdk = sdk
        self.agent_id = sdk.next_agent_id()
        self._send_count = 0
        self.fail_first_send = fail_first_send

    async def send(self, text):
        self.sdk.sent.append(text)
        self._send_count += 1
        if "SYSTEM INSTRUCTIONS" in text:
            return _FakeRun([], status="finished", result="READY")
        if self.fail_first_send:
            self.fail_first_send = False
            raise InternalServerError("internal: internal error")
        return _FakeRun(list(self.sdk.stream_messages))

    async def delete(self):
        self.sdk.deleted.append(self.agent_id)


class _FakeAgentsAPI:
    def __init__(self, sdk):
        self.sdk = sdk

    async def create(self, options):
        self.sdk.created.append(options)
        agent = _FakeAgent(self.sdk)
        self.sdk._agents[agent.agent_id] = agent
        return agent

    async def resume(self, agent_id, options):
        self.sdk.resumed.append((agent_id, options))
        agent = self.sdk._agents.get(agent_id) or _FakeAgent(self.sdk)
        agent.agent_id = agent_id
        self.sdk._agents[agent_id] = agent
        return agent


class _FakeSDKClient:
    def __init__(self, stream_messages=None):
        self.stream_messages = stream_messages or []
        self.created: list = []
        self.resumed: list = []
        self.sent: list = []
        self.deleted: list = []
        self._agents: dict = {}
        self._counter = 0
        self.agents_api = _FakeAgentsAPI(self)

    @property
    def agents(self):
        return self.agents_api

    def next_agent_id(self):
        self._counter += 1
        return f"agent-test-{self._counter:03d}"


class _FakeBridge:
    def __init__(self):
        self.started = False
        self.stopped = False
        self._tool_defs = [
            {
                "type": "custom",
                "name": "manage_executors",
                "description": "Manage executors",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    @property
    def custom_tool_defs(self):
        return self._tool_defs

    async def call(self, name, arguments):
        return f"ok:{name}", False


def test_resolve_cursor_model():
    assert resolve_cursor_model("cursor-managed", "") == DEFAULT_CURSOR_MODEL
    assert resolve_cursor_model("cursor-managed:composer-2.5", "") == "composer-2.5"
    assert resolve_cursor_model("cursor-managed", "composer-2.5") == "composer-2.5"


def test_is_cursor_agent_key():
    assert is_cursor_agent_key("cursor-managed")
    assert not is_cursor_agent_key("claude-managed")


def test_prompt_stream_maps_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")

    text_block = SimpleNamespace(type="text", text="FLAT — chop.")
    assistant = SimpleNamespace(
        type="assistant",
        message=SimpleNamespace(content=[text_block]),
    )
    tool_running = SimpleNamespace(
        type="tool_call",
        call_id="tc1",
        name="manage_routines",
        status="running",
        args={"action": "list"},
    )
    tool_done = SimpleNamespace(
        type="tool_call",
        call_id="tc1",
        name="manage_routines",
        status="completed",
        result="ok",
    )

    sdk = _FakeSDKClient([assistant, tool_running, tool_done])
    bridge = _FakeBridge()
    agent_dir = tmp_path / "composer_btc_perp_brain"
    agent_dir.mkdir()
    (agent_dir / "state").mkdir()

    client = CursorAgentClient(
        model="composer-2.5",
        system_prompt="Trade BTC.",
        agent_name="Composer Brain",
        slug="composer_btc_perp_brain",
        agent_dir=agent_dir,
        persist_session=True,
        bridge=bridge,
        sdk_client=sdk,
    )

    async def _run():
        await client.start()
        events = []
        async for ev in client.prompt_stream("tick 1 data"):
            events.append(ev)
        await client.stop()
        return events

    events = asyncio.run(_run())
    types = [type(e).__name__ for e in events]
    assert "TextChunk" in types
    assert "ToolCallEvent" in types
    assert "ToolCallUpdate" in types
    assert types[-1] == "PromptDone"
    assert bridge.started and bridge.stopped
    state = json.loads((agent_dir / "state" / "cursor_agent.json").read_text())
    assert state.get("agent_id", "").startswith("agent-test-")


def test_permission_callback_blocks_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")

    async def deny(tool_call, options):
        return {"outcome": {"outcome": "cancelled"}, "block_reason": "dry-run"}

    bridge = _FakeBridge()
    sdk = _FakeSDKClient([])
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    client = CursorAgentClient(
        model="composer-2.5",
        system_prompt="x",
        agent_name="T",
        slug="t",
        agent_dir=agent_dir,
        permission_callback=deny,
        bridge=bridge,
        sdk_client=sdk,
    )
    tools = client._build_custom_tools()
    assert "manage_executors" in tools

    async def _exec():
        from cursor_sdk import CustomToolContext

        result = await tools["manage_executors"].execute(
            {"action": "create"},
            CustomToolContext(tool_call_id="1"),
        )
        return result

    result = asyncio.run(_exec())
    assert "BLOCKED" in result


def test_rotate_persisted_agent(tmp_path):
    agent_dir = tmp_path / "composer"
    state_dir = agent_dir / "state"
    state_dir.mkdir(parents=True)
    path = state_dir / "cursor_agent.json"
    path.write_text(json.dumps({"agent_id": "agent-old", "agent_fingerprint": "abc"}))

    old = asyncio.run(CursorAgentClient.rotate_persisted_agent(agent_dir))
    assert old == "agent-old"
    state = json.loads(path.read_text())
    assert "agent_id" not in state


def test_is_recoverable_cursor_error():
    assert is_recoverable_cursor_error(InternalServerError("internal: internal error"))
    assert is_cursor_provider_error("Bridge request failed: ConnectError")
    assert not is_recoverable_cursor_error(ValueError("nope"))


def test_send_recovers_from_internal_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")

    text_block = SimpleNamespace(type="text", text="FLAT after recovery.")
    assistant = SimpleNamespace(
        type="assistant",
        message=SimpleNamespace(content=[text_block]),
    )

    sdk = _FakeSDKClient([assistant])
    bridge = _FakeBridge()
    agent_dir = tmp_path / "composer_btc_perp_brain"
    agent_dir.mkdir()
    state_dir = agent_dir / "state"
    state_dir.mkdir()

    client = CursorAgentClient(
        model="composer-2.5",
        system_prompt="Trade BTC.",
        agent_name="Composer Brain",
        slug="composer_btc_perp_brain",
        agent_dir=agent_dir,
        persist_session=True,
        bridge=bridge,
        sdk_client=sdk,
    )
    fingerprint = client._fingerprint([t["name"] for t in bridge.custom_tool_defs])
    (state_dir / "cursor_agent.json").write_text(
        json.dumps(
            {
                "agent_id": "agent-stale",
                "agent_fingerprint": fingerprint,
                "system_bootstrapped": True,
            }
        )
    )

    fail_agent = _FakeAgent(sdk, fail_first_send=True)
    sdk._agents["agent-stale"] = fail_agent

    async def _resume(agent_id, options):
        sdk.resumed.append((agent_id, options))
        agent = sdk._agents[agent_id]
        agent.agent_id = agent_id
        return agent

    sdk.agents_api.resume = _resume

    async def _run():
        await client.start()
        events = []
        async for ev in client.prompt_stream("tick 1 data"):
            events.append(ev)
        await client.stop()
        return events

    events = asyncio.run(_run())
    assert any(isinstance(e, TextChunk) and "FLAT after recovery" in e.text for e in events)
    assert events[-1].stop_reason == "end_turn"
    state = json.loads((agent_dir / "state" / "cursor_agent.json").read_text())
    assert state.get("agent_id", "").startswith("agent-test-")
    assert "agent-stale" not in state.get("agent_id", "")
