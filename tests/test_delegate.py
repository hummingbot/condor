"""Unit tests for DELEGATE -- fire-and-forget background agent tasks (FEAT-006).

Covers the lifecycle (running -> done/error/stopped), result capture, transcript
persistence, completion notification, and that the runner drives the shared consult
engine with ``permission_callback=None`` (auto-approve).
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegate import (
    get_all_delegations,
    get_delegation,
    start_delegation,
    stop_delegation,
)


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, *a, **kw):
        self.messages.append(kw.get("text") or (a[1] if len(a) > 1 else ""))


def _write_agent(root, slug):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(
        f"---\nname: {slug}\nwhen_to_consult: always\n---\n\nBody.\n"
    )
    return d


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


async def _drain(dt):
    """Await the background task to completion (ignoring cancellation)."""
    if dt._task is not None:
        try:
            await dt._task
        except asyncio.CancelledError:
            pass


def test_delegation_runs_to_done_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    seen = {}

    async def fake_run(*, permission_callback, **kw):
        seen.update(kw)
        seen["permission_callback"] = permission_callback
        return "scan complete: 3 pools"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan SOL pools",
            bot=bot,
        )
        # Returns immediately, still running before we await it.
        assert dt.status == "running"
        assert get_delegation(dt.task_id) is dt
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    # Lifecycle + result capture.
    assert dt.status == "done"
    assert dt.result == "scan complete: 3 pools"
    # Auto-approve: the runner drives consult with NO permission callback.
    assert seen["permission_callback"] is None
    assert seen["task"] == "scan SOL pools"
    # Transcript written under agents/{slug}/delegations/{task_id}.md.
    transcript = tmp_path / "scout" / "delegations" / f"{dt.task_id}.md"
    assert transcript.exists()
    assert "scan complete: 3 pools" in transcript.read_text()
    # Notification delivered.
    assert any("done" in m for m in bot.messages)


def test_delegation_captures_error(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    async def boom(**kw):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", boom)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="do thing",
            bot=bot,
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "error"
    assert "model exploded" in dt.error
    assert any("failed" in m for m in bot.messages)


def test_stop_cancels_running_delegation(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    async def slow(**kw):
        await asyncio.sleep(60)
        return "never"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", slow)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="long task",
            bot=bot,
        )
        await asyncio.sleep(0)  # let the runner start
        assert dt.task_id in get_all_delegations()
        stopped = await stop_delegation(dt.task_id)
        await _drain(dt)
        return dt, stopped

    dt, stopped = asyncio.run(scenario())

    assert stopped is True
    assert dt.status == "stopped"
    # A stopped task does not spam a completion notification.
    assert bot.messages == []


def test_stop_unknown_returns_false():
    assert asyncio.run(stop_delegation("nope-delegate-x")) is False
