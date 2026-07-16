"""Tests: notifications outbox chokepoint + serverless condor-only MCP wiring."""

import asyncio
import json

import pytest

import condor.notifications as notifications
from condor.notifications import notify, read_notifications


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    path = tmp_path / "notifications.jsonl"
    monkeypatch.setattr(notifications, "OUTBOX_PATH", path)
    return path


def test_notify_appends_to_outbox(outbox):
    entry = asyncio.run(notify(
        "position closed", user_id=7, chat_id=7, agent_id="range_trader_2",
        kind="session",
    ))
    assert entry["id"]
    # §4.1: pure outbox entry — no delivery-mirror fields.
    assert set(entry) == {
        "id", "ts", "user_id", "chat_id", "agent_id", "kind", "origin", "text",
    }
    lines = [json.loads(l) for l in outbox.read_text().splitlines()]
    assert lines[0]["agent_id"] == "range_trader_2"
    assert lines[0]["text"] == "position closed"
    assert lines[0]["kind"] == "session"


def test_notify_fail_soft_returns_entry(outbox, monkeypatch):
    """An append failure never raises into the emitter."""
    import condor.notifications as notifications

    def boom(entry):
        raise OSError("disk full")

    monkeypatch.setattr(notifications, "_append_outbox", boom)
    entry = asyncio.run(notify("tick error", user_id=7, chat_id=7))
    assert entry["text"] == "tick error"


def test_read_notifications_filters(outbox):

    async def run():
        await notify("a", user_id=1, agent_id="x_1")
        await notify("b", user_id=1, agent_id="y_1")
        await notify("c", user_id=1, agent_id="x_1")

    asyncio.run(run())
    all_entries = read_notifications()
    assert [e["text"] for e in all_entries] == ["a", "b", "c"]
    assert [e["text"] for e in read_notifications(agent_id="x_1")] == ["a", "c"]
    since = all_entries[0]["ts"]
    assert [e["text"] for e in read_notifications(since_ts=since)] == ["b", "c"]
    assert len(read_notifications(limit=1)) == 1


# -- agent sessions: condor-only MCP ---------------------------------------------


def test_session_builds_condor_only():
    from condor.agents.context import build_mcp_servers_for_session

    servers = build_mcp_servers_for_session(
        user_id=1, chat_id=1, agent_slug="memecoin_trender",
        agent_id="memecoin_trender_1",
    )
    assert [s["name"] for s in servers] == ["condor"]
    args = servers[0]["args"]
    assert "--agent-id" in args and "memecoin_trender_1" in args
    assert "--server-name" not in args


def test_tick_prompt_has_no_hummingbot_refs():
    """The prompt must match the condor-only wiring: no mcp-hummingbot tools,
    the condor-native manage_executors, no controller_id line."""
    from types import SimpleNamespace

    from condor.agents.prompts import build_tick_prompt

    agent = SimpleNamespace(slug="memecoin_trender",
                            agent_key="claude-acp:sonnet",
                            instructions="hunt memecoins\n\ntrend playbook")
    prompt = build_tick_prompt(agent, {"execution_mode": "loop"}, {}, "", "", "", {},
                               tick_number=1, agent_id="memecoin_trender_2")

    assert "mcp-hummingbot" not in prompt
    assert "mcp__mcp-hummingbot" not in prompt
    assert "mcp__condor__manage_executors" in prompt
    assert "controller_id" not in prompt
