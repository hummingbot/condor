"""Tests for managed-session rotation after consecutive tick timeouts.

Bug history: session 2 wedged on 2026-06-11 22:16 UTC — the hosted session
stopped echoing user messages and every tick for 15 hours (155 ticks) timed
out silently. The engine kept reusing the wedged session_id from
managed_agent.json. Fix: after N consecutive timeouts, drop the persisted
session (fresh one is provisioned next tick; agent + memory store are kept)
and alert the user.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from condor.acp.managed_agent_client import ManagedAgentClient
from condor.trading_agent.engine import ROTATE_AFTER_CONSECUTIVE_TIMEOUTS, TickEngine
from condor.trading_agent.strategy import Strategy


# ── rotate_persisted_session helper ──


class _FakeSessions:
    def __init__(self, fail_delete: bool = False):
        self.deleted: list[str] = []
        self.fail_delete = fail_delete

    async def delete(self, session_id: str):
        if self.fail_delete:
            raise RuntimeError("api down")
        self.deleted.append(session_id)


class _FakeSDK:
    def __init__(self, fail_delete: bool = False):
        self.beta = types.SimpleNamespace(sessions=_FakeSessions(fail_delete))


def _write_state(agent_dir: Path, session_id: str = "sesn_wedged") -> Path:
    state_dir = agent_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "managed_agent.json"
    path.write_text(json.dumps({
        "agent_id": "agent_x",
        "agent_fingerprint": "abc123",
        "memory_store_id": "memstore_y",
        "session_id": session_id,
    }))
    return path


def test_rotate_drops_session_and_keeps_agent_and_memory(tmp_path):
    path = _write_state(tmp_path)
    sdk = _FakeSDK()
    dropped = asyncio.run(
        ManagedAgentClient.rotate_persisted_session(tmp_path, sdk_client=sdk)
    )
    assert dropped == "sesn_wedged"
    state = json.loads(path.read_text())
    assert "session_id" not in state or not state["session_id"]
    assert state["agent_id"] == "agent_x"
    assert state["memory_store_id"] == "memstore_y"
    assert sdk.beta.sessions.deleted == ["sesn_wedged"]


def test_rotate_tolerates_delete_failure(tmp_path):
    path = _write_state(tmp_path)
    dropped = asyncio.run(
        ManagedAgentClient.rotate_persisted_session(
            tmp_path, sdk_client=_FakeSDK(fail_delete=True)
        )
    )
    assert dropped == "sesn_wedged"  # local state still cleared
    assert "sesn_wedged" not in path.read_text()


def test_rotate_no_state_file_is_noop(tmp_path):
    dropped = asyncio.run(
        ManagedAgentClient.rotate_persisted_session(tmp_path, sdk_client=_FakeSDK())
    )
    assert dropped == ""


def test_rotate_no_session_in_state_is_noop(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "managed_agent.json").write_text(json.dumps({"agent_id": "agent_x"}))
    dropped = asyncio.run(
        ManagedAgentClient.rotate_persisted_session(tmp_path, sdk_client=_FakeSDK())
    )
    assert dropped == ""


# ── engine timeout-streak handling ──


def _make_engine(tmp_path, monkeypatch) -> TickEngine:
    import condor.trading_agent.strategy as strategy_mod

    monkeypatch.setattr(strategy_mod, "_DATA_ROOT", tmp_path)
    strategy = Strategy(
        id="test12345678",
        name="Rotation Test Agent Nonexistent",
        description="test",
        agent_key="claude-managed",
        instructions="Trade.",
    )
    engine = TickEngine(
        strategy=strategy,
        config={"agent_key": "claude-managed", "execution_mode": "loop", "risk_limits": {}},
        chat_id=0,
        user_id=0,
    )
    return engine


def test_streak_below_threshold_does_not_rotate(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch)
    _write_state(engine.strategy.agent_dir)
    notifications: list[str] = []

    async def fake_notify(msg):
        notifications.append(msg)

    monkeypatch.setattr(engine, "_notify", fake_notify)

    for _ in range(ROTATE_AFTER_CONSECUTIVE_TIMEOUTS - 1):
        asyncio.run(engine._handle_timeout_streak(timed_out=True))

    state = json.loads(
        (engine.strategy.agent_dir / "state" / "managed_agent.json").read_text()
    )
    assert state["session_id"] == "sesn_wedged"
    assert notifications == []


def test_streak_at_threshold_rotates_and_notifies(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch)
    state_path = _write_state(engine.strategy.agent_dir)
    notifications: list[str] = []

    async def fake_notify(msg):
        notifications.append(msg)

    monkeypatch.setattr(engine, "_notify", fake_notify)
    # Avoid creating a real Anthropic client for the server-side delete
    fake_sdk = _FakeSDK()
    original = ManagedAgentClient.rotate_persisted_session

    async def fake_rotate(agent_dir, sdk_client=None):
        return await original(agent_dir, sdk_client=fake_sdk)

    monkeypatch.setattr(
        ManagedAgentClient, "rotate_persisted_session", staticmethod(fake_rotate)
    )

    for _ in range(ROTATE_AFTER_CONSECUTIVE_TIMEOUTS):
        asyncio.run(engine._handle_timeout_streak(timed_out=True))

    state = json.loads(state_path.read_text())
    assert not state.get("session_id")
    assert len(notifications) == 1
    assert "timeout" in notifications[0].lower()
    # Counter resets after rotation so the next streak re-alerts
    assert engine._consecutive_timeouts == 0


def test_successful_tick_resets_streak(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch)
    _write_state(engine.strategy.agent_dir)

    asyncio.run(engine._handle_timeout_streak(timed_out=True))
    asyncio.run(engine._handle_timeout_streak(timed_out=True))
    asyncio.run(engine._handle_timeout_streak(timed_out=False))
    assert engine._consecutive_timeouts == 0
