"""Tests for Cursor SDK bridge error streak handling in TickEngine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from condor.trading_agent.engine import (
    ROTATE_AFTER_CONSECUTIVE_CURSOR_ERRORS,
    TickEngine,
)
from condor.trading_agent.strategy import Strategy


def _make_engine(tmp_path: Path, monkeypatch) -> TickEngine:
    import condor.trading_agent.strategy as strategy_mod

    monkeypatch.setattr(strategy_mod, "_DATA_ROOT", tmp_path)
    strategy = Strategy(
        id="composer123456",
        name="Composer Rotation Test Agent",
        description="test",
        agent_key="cursor-managed",
        instructions="Trade BTC.",
    )
    return TickEngine(
        strategy=strategy,
        config={
            "agent_key": "cursor-managed",
            "execution_mode": "loop",
            "risk_limits": {},
            "frequency_sec": 300,
        },
        chat_id=0,
        user_id=0,
    )


def test_start_rotates_cursor_agent_for_new_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    engine = _make_engine(tmp_path, monkeypatch)
    state_dir = engine.strategy.agent_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "cursor_agent.json"
    path.write_text(json.dumps({"agent_id": "agent-old", "agent_fingerprint": "abc"}))

    async def _run():
        await engine.start()
        await engine.stop()

    asyncio.run(_run())
    state = json.loads(path.read_text())
    assert "agent_id" not in state


def test_cursor_error_streak_rotates_after_threshold(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch)
    state_dir = engine.strategy.agent_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "cursor_agent.json").write_text(
        json.dumps({"agent_id": "agent-wedged", "agent_fingerprint": "fp"})
    )

    engine._consecutive_cursor_errors = ROTATE_AFTER_CONSECUTIVE_CURSOR_ERRORS - 1
    notified: list[str] = []

    async def _notify(msg: str) -> None:
        notified.append(msg)

    monkeypatch.setattr(engine, "_notify", _notify)

    async def _run():
        await engine._handle_cursor_error_streak(
            Exception("internal: internal error")
        )

    asyncio.run(_run())

    assert engine._consecutive_cursor_errors == 0
    assert notified
    state = json.loads((state_dir / "cursor_agent.json").read_text())
    assert "agent_id" not in state
