"""Tests for routing agent_key 'cursor-managed' through TickEngine and prompts."""

from __future__ import annotations

import asyncio
import sys
import types

from condor.acp.cursor_agent_client import CursorAgentClient, is_cursor_agent_key
from condor.trading_agent.engine import TickEngine
from condor.trading_agent.prompts import build_cursor_system_prompt, build_managed_tick_prompt
from condor.trading_agent.strategy import Strategy


def _install_fake_handlers(monkeypatch):
    shared = types.ModuleType("handlers.agents._shared")
    shared.build_mcp_servers_for_agent = lambda *a, **k: []
    shared.build_mcp_servers_for_session = lambda *a, **k: []
    shared.get_project_dir = lambda: "."
    shared.is_dangerous_tool_call = lambda *a, **k: False
    handlers_pkg = types.ModuleType("handlers")
    agents_pkg = types.ModuleType("handlers.agents")
    handlers_pkg.agents = agents_pkg
    agents_pkg._shared = shared
    monkeypatch.setitem(sys.modules, "handlers", handlers_pkg)
    monkeypatch.setitem(sys.modules, "handlers.agents", agents_pkg)
    monkeypatch.setitem(sys.modules, "handlers.agents._shared", shared)


def _make_strategy() -> Strategy:
    return Strategy(
        id="test12345678",
        name="Composer Routing Test Agent Nonexistent",
        description="test",
        agent_key="cursor-managed",
        instructions="Trade BTC perps. Avoid chop.",
    )


def _make_engine(config: dict) -> TickEngine:
    base = {"execution_mode": "dry_run", "risk_limits": {}}
    base.update(config)
    return TickEngine(strategy=_make_strategy(), config=base, chat_id=0, user_id=0)


def test_is_cursor_agent_key():
    assert is_cursor_agent_key("cursor-managed")
    assert is_cursor_agent_key("cursor-managed:composer-2.5")
    assert not is_cursor_agent_key("claude-managed")
    assert not is_cursor_agent_key("")


def test_create_client_routes_cursor_managed(monkeypatch):
    _install_fake_handlers(monkeypatch)
    engine = _make_engine({"agent_key": "cursor-managed", "model": "composer-2.5"})
    client = asyncio.run(engine._create_client())
    assert isinstance(client, CursorAgentClient)
    assert client.model == "composer-2.5"
    assert client.slug == "composer_routing_test_agent_nonexistent"
    assert client.persist_session is False
    assert "Avoid chop." in client.system_prompt
    assert "/mnt/memory" not in client.system_prompt
    assert "memory/" in client.system_prompt


def test_create_client_loop_mode_persists_session(monkeypatch):
    _install_fake_handlers(monkeypatch)
    engine = _make_engine({"agent_key": "cursor-managed", "execution_mode": "loop"})
    client = asyncio.run(engine._create_client())
    assert isinstance(client, CursorAgentClient)
    assert client.persist_session is True


def test_create_client_inline_model_key(monkeypatch):
    _install_fake_handlers(monkeypatch)
    engine = _make_engine({"agent_key": "cursor-managed:composer-2.5"})
    client = asyncio.run(engine._create_client())
    assert client.model == "composer-2.5"


def test_cursor_system_prompt_uses_filesystem_memory():
    strategy = _make_strategy()
    system = build_cursor_system_prompt(strategy, {"execution_mode": "dry_run"}, "")
    assert "memory/" in system
    assert "/mnt/memory" not in system
    assert "Trade BTC perps. Avoid chop." in system
