"""Tests for the MCP-to-custom-tool bridge used by ManagedAgentClient."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from condor.acp.managed_tools import McpToolBridge


def _tool(name="manage_routines", description="Run routines.", schema=None):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {"action": {"type": "string"}}},
    )


class _FakeSession:
    def __init__(self, result_text="ok", is_error=False):
        self.calls: list[tuple[str, dict]] = []
        self._result_text = result_text
        self._is_error = is_error

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._result_text)],
            isError=self._is_error,
        )


def test_register_builds_custom_tool_defs():
    bridge = McpToolBridge([])
    bridge._register("condor", _FakeSession(), [_tool()])
    defs = bridge.custom_tool_defs
    assert len(defs) == 1
    d = defs[0]
    assert d["type"] == "custom"
    assert d["name"] == "manage_routines"
    assert d["description"] == "Run routines."
    assert d["input_schema"]["type"] == "object"


def test_register_truncates_long_descriptions_to_api_limit():
    # The Managed Agents API rejects custom tool descriptions > 1024 chars
    bridge = McpToolBridge([])
    bridge._register("condor", _FakeSession(), [_tool(description="x" * 5000)])
    desc = bridge.custom_tool_defs[0]["description"]
    assert len(desc) <= 1024


def test_register_handles_missing_description_and_schema():
    bridge = McpToolBridge([])
    bridge._register("condor", _FakeSession(), [SimpleNamespace(name="t1", description=None, inputSchema=None)])
    d = bridge.custom_tool_defs[0]
    assert d["description"] == ""
    assert d["input_schema"] == {"type": "object", "properties": {}}


def test_register_first_server_wins_on_name_collision():
    bridge = McpToolBridge([])
    s1, s2 = _FakeSession("from-one"), _FakeSession("from-two")
    bridge._register("one", s1, [_tool("dupe")])
    bridge._register("two", s2, [_tool("dupe")])
    assert len(bridge.custom_tool_defs) == 1
    text, is_error = asyncio.run(bridge.call("dupe", {}))
    assert text == "from-one"
    assert not is_error


def test_call_dispatches_to_owning_session():
    bridge = McpToolBridge([])
    session = _FakeSession("result-text")
    bridge._register("condor", session, [_tool("manage_routines")])
    text, is_error = asyncio.run(bridge.call("manage_routines", {"action": "list"}))
    assert text == "result-text"
    assert not is_error
    assert session.calls == [("manage_routines", {"action": "list"})]


def test_call_unknown_tool_returns_error():
    bridge = McpToolBridge([])
    text, is_error = asyncio.run(bridge.call("nope", {}))
    assert is_error
    assert "nope" in text


def test_call_propagates_tool_error_flag():
    bridge = McpToolBridge([])
    bridge._register("condor", _FakeSession("boom", is_error=True), [_tool("t")])
    text, is_error = asyncio.run(bridge.call("t", {}))
    assert is_error
    assert text == "boom"


def test_call_survives_session_exception():
    class _ExplodingSession:
        async def call_tool(self, name, arguments):
            raise RuntimeError("pipe broken")

    bridge = McpToolBridge([])
    bridge._register("condor", _ExplodingSession(), [_tool("t")])
    text, is_error = asyncio.run(bridge.call("t", {}))
    assert is_error
    assert "pipe broken" in text
