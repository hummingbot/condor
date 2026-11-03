"""FEAT-076: a backtest runs on the server it is recorded under.

``backtest_chart`` resolved two things separately: the client it submits with,
from the chat's ambient preferences, and the server it files the result under,
from the launch. A run with no chat behind it -- every web and agent run --
could therefore execute on one box and be recorded as belonging to another.

That was survivable while the dashboard submitted its own backtests. Once the
routine is the only thing that runs one, it is not: a dashboard run launched
against ``brigado_2`` must not quietly execute on the default server.
"""

from __future__ import annotations

import asyncio

import pytest

import condor.backtest_store as store_mod
import condor.reports as reports_mod
from condor.backtest_store import BacktestStore
from condor.routine_store import WebRoutineContext
from tests.conftest import load_shared_routine

# The scripted client and the stubbed renderer are FEAT-039's, and this is about
# which server they are asked for -- not about rebuilding them.
from tests.test_backtest_one_surface import (
    CONFIG_NAME,
    FakeBacktesting,
    FakeClient,
    FakeReportBuilder,
)

bc = load_shared_routine("backtest_chart")

LAUNCHED_ON = "brigado_2"
CHAT_DEFAULT = "local"


# ── The routine runs where it records ─────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    fresh = BacktestStore(tmp_path / "backtests")
    monkeypatch.setattr(store_mod, "_store", fresh)
    return fresh


@pytest.fixture
def asked(monkeypatch):
    """Record which server the routine asked for a client for."""
    calls: list[dict] = []

    async def fake_get_client(chat_id, user_id=None, context=None, server=None):
        calls.append({"chat_id": chat_id, "server": server})
        return FakeClient(FakeBacktesting("task-1", [_completed_envelope()]))

    monkeypatch.setattr(bc, "get_client", fake_get_client)
    monkeypatch.setattr(
        bc, "generate_chart", lambda *a, render_png=True, **k: (None, object())
    )
    monkeypatch.setattr(reports_mod, "ReportBuilder", FakeReportBuilder)
    return calls


def _completed_envelope() -> dict:
    from tests.test_backtest_one_surface import _envelope

    return _envelope("task-1")


def test_a_web_run_uses_the_server_it_was_launched_against(store, asked):
    """No chat_id at all, and the launch server still decides both halves."""
    config = bc.Config(
        config_name=CONFIG_NAME,
        start_date="2025-04-22",
        end_date="2025-04-23",
        chart=False,
    )
    asyncio.run(bc.run(config, WebRoutineContext(server_name=LAUNCHED_ON)))

    assert [c["server"] for c in asked] == [LAUNCHED_ON]
    assert store.get_result("task-1")["server"] == LAUNCHED_ON


def test_a_chat_run_still_resolves_its_server_from_the_chat(store, asked):
    """The Telegram seat is unchanged: the active-server preference decides."""

    class ChatContext:
        _chat_id = 42
        bot = None
        user_data = {"user_preferences": {"general": {"active_server": CHAT_DEFAULT}}}

    config = bc.Config(
        config_name=CONFIG_NAME,
        start_date="2025-04-22",
        end_date="2025-04-23",
        chart=False,
    )
    asyncio.run(bc.run(config, ChatContext()))

    assert [c["server"] for c in asked] == [CHAT_DEFAULT]
    assert store.get_result("task-1")["server"] == CHAT_DEFAULT


def test_naming_the_server_outranks_the_chat_default():
    """The seam itself: ``get_client(server=...)`` is not re-derived."""
    import config_manager as cm_mod

    seen = {}

    class _CM:
        async def get_client_for_chat(self, chat_id, user_id, preferred_server):
            seen.update(
                chat_id=chat_id, user_id=user_id, preferred_server=preferred_server
            )
            return "client"

    class ChatContext:
        user_data = {"user_preferences": {"general": {"active_server": CHAT_DEFAULT}}}

    original = cm_mod.get_config_manager
    cm_mod.get_config_manager = lambda: _CM()
    try:
        client = asyncio.run(
            cm_mod.get_client(42, context=ChatContext(), server=LAUNCHED_ON)
        )
    finally:
        cm_mod.get_config_manager = original

    assert client == "client"
    assert seen["preferred_server"] == LAUNCHED_ON
