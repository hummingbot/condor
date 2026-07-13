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


class _Bot:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))


def test_notify_appends_and_mirrors(outbox):
    bot = _Bot()
    entry = asyncio.run(notify(
        "position closed", user_id=7, chat_id=7, agent_id="lp_rebalancer_2",
        kind="session", bot=bot,
    ))
    assert entry["delivered_telegram"] is True
    assert bot.sent == [(7, "position closed")]
    lines = [json.loads(l) for l in outbox.read_text().splitlines()]
    assert lines[0]["agent_id"] == "lp_rebalancer_2"
    assert lines[0]["delivered_telegram"] is True


def test_notify_fail_soft_still_lands_in_outbox(outbox):
    entry = asyncio.run(notify(
        "tick error", user_id=7, chat_id=7, bot=_Bot(fail=True),
    ))
    assert entry["delivered_telegram"] is False
    assert read_notifications()[0]["text"] == "tick error"


def test_notify_chat_id_zero_falls_back_to_user_dm(outbox):
    """The old chat_id=0 web gap: mirror to the user's DM, never drop."""
    bot = _Bot()
    entry = asyncio.run(notify("web run done", user_id=42, chat_id=0, bot=bot))
    assert bot.sent == [(42, "web run done")]
    assert entry["delivered_telegram"] is True


def test_read_notifications_filters(outbox):
    bot = _Bot()

    async def run():
        await notify("a", user_id=1, agent_id="x_1", bot=bot)
        await notify("b", user_id=1, agent_id="y_1", bot=bot)
        await notify("c", user_id=1, agent_id="x_1", bot=bot)

    asyncio.run(run())
    all_entries = read_notifications()
    assert [e["text"] for e in all_entries] == ["a", "b", "c"]
    assert [e["text"] for e in read_notifications(agent_id="x_1")] == ["a", "c"]
    since = all_entries[0]["ts"]
    assert [e["text"] for e in read_notifications(since_ts=since)] == ["b", "c"]
    assert len(read_notifications(limit=1)) == 1


# -- serverless agents: condor-only MCP -----------------------------------------


def test_serverless_session_builds_condor_only():
    from handlers.agents._shared import build_mcp_servers_for_session

    servers = build_mcp_servers_for_session(
        user_id=1, chat_id=1, agent_slug="memecoin_trender",
        agent_id="memecoin_trender_1", strategy="trend_position",
        include_hummingbot=False,
    )
    assert [s["name"] for s in servers] == ["condor"]
    args = servers[0]["args"]
    assert "--agent-id" in args and "memecoin_trender_1" in args
    assert "--strategy" in args and "trend_position" in args


def test_serverless_prompt_has_no_hummingbot_refs():
    """The prompt must match the condor-only wiring: no mcp-hummingbot tools,
    the condor-native manage_executors instead, no controller_id line."""
    from types import SimpleNamespace

    from condor.agents.prompts import build_tick_prompt

    def prompt(server_required):
        agent = SimpleNamespace(slug="memecoin_trender", server_required=server_required,
                                agent_key="claude-acp:sonnet", instructions="hunt memecoins")
        strat = SimpleNamespace(agent_key=None, instructions="trend playbook",
                                slug="trend_position", agent_slug="memecoin_trender", dir=None)
        return build_tick_prompt(agent, strat, {"execution_mode": "loop"}, {}, "", "", "", {},
                                 tick_number=1, agent_id="memecoin_trender_2")

    serverless = prompt(False)
    assert "mcp-hummingbot" not in serverless
    assert "mcp__mcp-hummingbot" not in serverless
    assert "mcp__condor__manage_executors" in serverless
    assert "controller_id" not in serverless

    backed = prompt(True)
    assert "mcp__mcp-hummingbot__manage_executors" in backed
    assert "pre-configured" in backed
    assert "controller_id" in backed
