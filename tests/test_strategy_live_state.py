"""A strategy's live state is resolved once, and every view reads it (ARCH-691).

"First registered engine wins, else the latest session on disk, else idle" was
written out in ``_build_strategy_summary``, ``get_strategy`` and
``_strategy_cards``, the last in a different form. These pin the three views to
one answer, and pin the brain panel's path to stay cheap: it asks for status
only, so it must not pay the per-engine journal and actions.jsonl reads that
building a ``RunningInstance`` costs.
"""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from condor.runtime.registry_file import write_status
from condor.web.routes import agents as agents_routes


def _engine(**info):
    base = {
        "agent_id": "ag.st_4",
        "session_num": 4,
        "status": "running",
        "tick_count": 14,
        "daily_pnl": 0.0,
        "frequency_sec": 60,
        "last_tick_at": 1_800_000_000.0,
        "max_ticks": 100,
        "last_error": "",
    }
    base.update(info)
    return SimpleNamespace(
        get_info=lambda: base,
        journal=None,
        session_dir=None,
        agent_id=base["agent_id"],
    )


@pytest.fixture()
def strategy(tmp_path, monkeypatch):
    """A strategy with no server (so nothing is priced) and no sessions yet."""
    home = tmp_path / "ag" / "strategies" / "st"
    home.mkdir(parents=True)
    (home / "config.yml").write_text("server_name: ''\n")
    strat = SimpleNamespace(
        home=home,
        slug="st",
        agent_slug="ag",
        name="st",
        description="",
        default_config={},
        default_trading_context="",
        created_by=1,
    )
    monkeypatch.setattr(agents_routes, "_get_strategy", lambda slug, sslug: strat)
    monkeypatch.setattr(
        agents_routes,
        "_strategy_store",
        lambda: SimpleNamespace(list=lambda slug: [strat]),
    )
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda slug, sslug: [])
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", OrderedDict())
    return strat


def _three_views(strategy):
    user = SimpleNamespace(id=1, username="u1")
    card = agents_routes._strategy_cards("ag")[0]
    detail = asyncio.run(agents_routes.get_strategy("ag", "st", user=user))
    summary = asyncio.run(agents_routes._build_strategy_summary(strategy, user))
    return card, detail, summary


def test_a_paused_engine_reads_paused_on_every_view(strategy, monkeypatch):
    engine = _engine(status="paused")
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda a, s: [engine])

    card, detail, summary = _three_views(strategy)

    assert card.status == detail.status == summary.status == "paused"
    assert detail.agent_id == summary.agent_id == "ag.st_4"
    assert summary.tick_count == 14
    assert [i.agent_id for i in detail.instances] == ["ag.st_4"]


def test_an_interrupted_session_on_disk_reads_interrupted_on_every_view(strategy):
    session_dir = strategy.home / "sessions" / "session_2"
    session_dir.mkdir(parents=True)
    (session_dir / "journal.md").write_text("# Journal\n")
    write_status(session_dir, state="interrupted", agent_id="ag.st_2")

    card, detail, summary = _three_views(strategy)

    assert card.status == detail.status == summary.status == "interrupted"
    assert detail.agent_id == summary.agent_id == "ag.st_2"
    assert detail.instances == summary.instances == []


def test_no_engine_and_no_sessions_reads_idle_on_every_view(strategy):
    card, detail, summary = _three_views(strategy)

    assert card.status == detail.status == summary.status == "idle"
    assert detail.agent_id == summary.agent_id == ""
    assert summary.tick_count == 0


def test_the_brain_panel_never_builds_an_instance(strategy, monkeypatch):
    """Status only: no journal summary, no actions.jsonl tail per engine."""
    from condor.agents import fleet_map

    def _boom(*a, **kw):
        raise AssertionError("the brain panel must not read what a loop last did")

    monkeypatch.setattr(fleet_map, "read_last_did", _boom)
    monkeypatch.setattr(fleet_map, "read_last_action", _boom)
    monkeypatch.setattr(agents_routes, "_instance_from_engine", _boom)
    engine = _engine(status="running")
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda a, s: [engine])

    assert agents_routes._strategy_cards("ag")[0].status == "running"


def test_the_first_engine_wins_when_several_are_registered(strategy):
    first = _engine(status="paused", agent_id="ag.st_5", tick_count=3)
    second = _engine(status="running", agent_id="ag.st_6", tick_count=9)

    status, agent_id, ticks, instances = agents_routes._strategy_live_state(
        strategy.home, "ag.st", [first, second], {}
    )

    assert (status, agent_id, ticks) == ("paused", "ag.st_5", 3)
    assert [i.agent_id for i in instances] == ["ag.st_5", "ag.st_6"]
    # Status-only callers get the same headline and no instances.
    assert agents_routes._strategy_live_state(
        strategy.home, "ag.st", [first, second]
    ) == ("paused", "ag.st_5", 3, [])
