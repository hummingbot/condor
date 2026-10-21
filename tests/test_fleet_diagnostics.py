"""The signals the fleet diagnostics view reads (FEAT-141).

The view is presentation over signals four other items produce, so what is
tested here is the two groups nothing produced — host capacity and executor
hygiene — and the one constraint the whole feature rests on: it may add **no**
poll. The cases are the race's own:

* a host carrying 173 terminal executor rows nothing prunes ([[PERF-733]]);
* a RUNNING executor whose owning session is gone, which cost one entry 13.5h
  ([[CORR-726]]);
* and, running through all of them, that a signal that could not be read comes
  back *unavailable* rather than clean. A clean answer invented from a missing
  reading is [[CORR-725]] exactly.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.diagnostics import (
    api_lag,
    executor_hygiene,
    host_load,
    live_sessions_by_strategy,
)

AGENT = "solana_dex_lp_expert"
STRATEGY = "lp_slot_operator"


def _row(executor_id, status, controller_id, **over):
    row = {
        "id": executor_id,
        "status": status,
        "controller_id": controller_id,
        "config": {"trading_pair": "SOL-USDC", "type": "lp_executor"},
    }
    row.update(over)
    return row


# ── Host capacity ──


def test_the_host_reading_divides_load_by_the_cores_it_has():
    reading = host_load()
    # Darwin and Linux both have a load average; the suite runs on both.
    assert reading["available"] is True
    assert reading["vcpus"] > 0
    assert reading["load_per_vcpu"] == pytest.approx(
        reading["load_1m"] / reading["vcpus"], abs=1e-3
    )


def test_a_platform_with_no_load_average_reports_unavailable(monkeypatch):
    def _boom():
        raise OSError("no load average here")

    monkeypatch.setattr("os.getloadavg", _boom)
    assert host_load() == {
        "available": False,
        "load_1m": 0.0,
        "vcpus": 0,
        "load_per_vcpu": 0.0,
    }


def test_a_load_with_no_cpu_count_is_not_a_reading(monkeypatch):
    """3.0 is idle on 32 vCPUs and on fire on 2, so the bare number is withheld."""
    monkeypatch.setattr("os.getloadavg", lambda: (3.0, 2.0, 1.0))
    monkeypatch.setattr("os.cpu_count", lambda: None)
    reading = host_load()
    assert reading["available"] is False
    assert reading["load_per_vcpu"] == 0.0


def test_the_lag_rows_come_off_counters_the_poller_already_kept(monkeypatch):
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service",
        lambda: SimpleNamespace(
            get_stats=lambda: {
                "health": {
                    "moneymaker": {
                        "status": "online",
                        "last_latency_ms": 118.4,
                        "consecutive_failures": 0,
                    },
                    "brigado": {"status": "degraded", "consecutive_failures": 3},
                }
            }
        ),
    )
    rows = api_lag()
    assert [row["host"] for row in rows] == ["brigado", "moneymaker"]
    assert rows[1]["last_latency_ms"] == pytest.approx(118.4)
    # A server that has never answered reports 0ms, which the view draws as
    # unknown rather than as an instantaneous API.
    assert rows[0]["last_latency_ms"] == 0.0


# ── Executor hygiene ──


def test_nothing_cached_is_unavailable_and_not_a_clean_host():
    assert executor_hygiene(None) == {
        "available": False,
        "counted": 0,
        "running": 0,
        "terminated": 0,
        "truncated": False,
        "orphaned_running": [],
    }


def test_the_terminal_rows_are_counted_apart_from_the_live_ones():
    rows = [_row(f"t{i}", "TERMINATED", f"{AGENT}.{STRATEGY}_3") for i in range(173)]
    rows.append(_row("live", "RUNNING", f"{AGENT}.{STRATEGY}_4"))
    rows.append(_row("flat", "SHUTTING_DOWN", f"{AGENT}.{STRATEGY}_4"))

    fold = executor_hygiene(rows, {(AGENT, STRATEGY): {4}}, truncated=True)
    assert fold["terminated"] == 173
    assert fold["running"] == 2
    assert fold["counted"] == 175
    assert fold["truncated"] is True
    # Both live rows belong to the session that is alive.
    assert fold["orphaned_running"] == []


def test_a_running_executor_whose_session_is_gone_is_an_orphan():
    rows = [
        _row("ex-old", "RUNNING", f"{AGENT}.{STRATEGY}_3"),
        _row("ex-new", "RUNNING", f"{AGENT}.{STRATEGY}_4"),
    ]
    (orphan,) = executor_hygiene(rows, {(AGENT, STRATEGY): {4}})["orphaned_running"]
    assert orphan == {
        "executor_id": "ex-old",
        "controller_id": f"{AGENT}.{STRATEGY}_3",
        "agent_slug": AGENT,
        "strategy_slug": STRATEGY,
        "session_num": 3,
        "pair": "SOL-USDC",
        "kind": "lp_executor",
    }


def test_a_strategy_with_no_live_session_at_all_orphans_its_running_rows():
    """The boot case: a graceful stop leaves executors running and no engine."""
    rows = [_row("ex-old", "RUNNING", f"{AGENT}.{STRATEGY}_3")]
    fold = executor_hygiene(rows, {})
    assert [o["executor_id"] for o in fold["orphaned_running"]] == ["ex-old"]


def test_a_terminated_row_is_never_an_orphan():
    """It has nothing to adopt; a stranded position has its own recovery path."""
    rows = [_row("ex-old", "TERMINATED", f"{AGENT}.{STRATEGY}_3")]
    assert executor_hygiene(rows, {})["orphaned_running"] == []


@pytest.mark.parametrize(
    "controller_id",
    [
        "main",  # the backend's default tag
        f"{AGENT}.{STRATEGY}_e3",  # an experiment, not a session
        "",  # untagged
        f"{AGENT}.{STRATEGY}",  # the lineage with no session number
    ],
)
def test_a_tag_that_is_not_a_session_is_not_judged(controller_id):
    """Only a real session tag can have a dead owner; everything else is nobody's."""
    rows = [_row("ex", "RUNNING", controller_id)]
    assert executor_hygiene(rows, {})["orphaned_running"] == []


def test_a_sibling_strategys_executor_is_not_this_ones_orphan():
    rows = [_row("ex", "RUNNING", f"{AGENT}.other_loop_1")]
    fold = executor_hygiene(rows, {(AGENT, "other_loop"): {1}})
    assert fold["orphaned_running"] == []


def test_the_live_sessions_come_from_the_registry(monkeypatch):
    engine = SimpleNamespace(
        agent=SimpleNamespace(slug=AGENT),
        strategy=SimpleNamespace(slug=STRATEGY),
        session_num=4,
    )
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor",
        lambda: SimpleNamespace(all=lambda: {"x": engine}),
    )
    assert live_sessions_by_strategy() == {(AGENT, STRATEGY): {4}}


# ── The constraint: no new poll ──


def test_the_bots_payload_folds_hygiene_from_the_cache_without_fetching(monkeypatch):
    """A fetch here would cost a Hummingbot round-trip on every 5s bots frame."""
    from condor.web.routes import bots as bots_route

    async def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("executor hygiene fetched instead of reading the cache")

    monkeypatch.setattr(
        bots_route,
        "get_server_data_service",
        lambda: SimpleNamespace(
            get=lambda *a, **k: [_row("ex-old", "RUNNING", f"{AGENT}.{STRATEGY}_3")],
            get_or_fetch=_boom,
        ),
    )
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor",
        lambda: SimpleNamespace(all=lambda: {}),
    )

    fold = bots_route._executor_hygiene("moneymaker")
    assert fold["available"] is True
    assert [o["executor_id"] for o in fold["orphaned_running"]] == ["ex-old"]


def test_an_empty_executor_cache_reads_as_unknown(monkeypatch):
    from condor.web.routes import bots as bots_route

    monkeypatch.setattr(
        bots_route,
        "get_server_data_service",
        lambda: SimpleNamespace(get=lambda *a, **k: None),
    )
    assert bots_route._executor_hygiene("moneymaker")["available"] is False


def test_an_unreadable_registry_withholds_the_orphan_answer(monkeypatch):
    """An empty map would answer it by calling every running executor an orphan."""
    from condor.web.routes import bots as bots_route

    monkeypatch.setattr(
        bots_route,
        "get_server_data_service",
        lambda: SimpleNamespace(
            get=lambda *a, **k: [_row("ex", "RUNNING", f"{AGENT}.{STRATEGY}_3")]
        ),
    )
    monkeypatch.setattr(
        "condor.agents.diagnostics.live_sessions_by_strategy",
        lambda: (_ for _ in ()).throw(RuntimeError("registry gone")),
    )
    assert bots_route._executor_hygiene("moneymaker")["available"] is False


def test_the_map_reports_how_long_the_last_tick_took(monkeypatch, tmp_path):
    """The host-capacity signal a slow tick *is*, read without opening a journal."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    agent_dir = tmp_path / "brigado"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text("---\nname: Brigado\n---\n\nBody.\n")
    loop_dir = agent_dir / "loops" / "brl_mm"
    loop_dir.mkdir(parents=True)
    (loop_dir / "loop.md").write_text("---\nname: BRL MM\n---\n\nPlaybook.\n")

    from condor.agents.fleet_map import build_fleet_map, reset_fleet_map_cache

    engine = SimpleNamespace(
        agent=SimpleNamespace(slug="brigado", name="Brigado"),
        strategy=SimpleNamespace(slug="brl_mm", name="BRL MM"),
        agent_id="brigado.brl_mm_7",
        session_num=7,
        status="running",
        journal=SimpleNamespace(tick_count=214, read_summary=lambda: ""),
        config={"frequency_sec": 60},
        _last_tick_at=1_700_000_500.0,
        _last_tick_duration=92.5,
        _last_error="",
        session_dir=None,
    )
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor",
        lambda: SimpleNamespace(all=lambda: {engine.agent_id: engine}),
    )
    reset_fleet_map_cache()
    try:
        (owner,) = build_fleet_map()
    finally:
        reset_fleet_map_cache()

    assert owner.live is not None
    # 92.5s on a 60s loop: the tick cannot keep up with its own interval, which
    # is what an oversubscribed host looks like from here.
    assert owner.live.last_tick_duration == pytest.approx(92.5)


def test_the_fleet_map_route_carries_the_diagnostics_block(monkeypatch, tmp_path):
    """One response, so the view costs the page no second poll."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    from condor.agents.fleet_map import reset_fleet_map_cache
    from condor.web.routes.agents import get_fleet_map

    reset_fleet_map_cache()
    monkeypatch.setattr("condor.agents.fleet_map._live_owners", lambda: {})
    try:
        response = asyncio.run(get_fleet_map(user=SimpleNamespace(id=1, is_admin=True)))
    finally:
        reset_fleet_map_cache()

    assert response.diagnostics.host.available is True
    assert response.diagnostics.host.vcpus > 0
    # Additive: the map a client read before is untouched.
    assert response.owners == []
