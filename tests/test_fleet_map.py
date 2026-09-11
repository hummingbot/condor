"""The fleet map: whose trading is whose, and what the loop is doing (FEAT-096).

Every case here is a way ``/bots`` could attribute trading to the wrong agent,
or pay for the answer: a bot outside its namespace credited anyway, a legacy
declared name dropped, a session that has not ticked yet reported as idle, or a
Hummingbot call sneaking into a route the bots page polls every five seconds.
"""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.routing import Match

from condor.agents import agent as agent_module
from condor.agents import fleet_map as fleet_map_module
from condor.agents import strategy as strategy_module
from condor.agents.fleet_map import build_fleet_map, reset_fleet_map_cache
from condor.web.routes.agents import router


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The registry is memoised for a minute; every test starts cold."""
    reset_fleet_map_cache()
    yield
    reset_fleet_map_cache()


def _write_agent(root, slug, name):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(f"---\nname: {name}\n---\n\nBody.\n")
    return d


def _write_strategy(root, agent_slug, sslug, name, *, config=""):
    d = root / agent_slug / "strategies" / sslug
    d.mkdir(parents=True, exist_ok=True)
    (d / "strategy.md").write_text(f"---\nname: {name}\n{config}---\n\nPlaybook.\n")
    return d


def _roots(monkeypatch, tmp_path):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))


def _no_engines(monkeypatch):
    monkeypatch.setattr(
        fleet_map_module,
        "_live_owners",
        lambda: {},
    )


def _fake_engine(agent_slug, agent_name, sslug, sname, **over):
    """An engine as the supervisor holds it — only what the map reads."""
    summary = over.get("summary_text", "")
    journal = SimpleNamespace(
        tick_count=over.get("tick_count", 0),
        read_summary=lambda: summary,
    )
    engine = SimpleNamespace(
        agent=SimpleNamespace(slug=agent_slug, name=agent_name),
        strategy=SimpleNamespace(slug=sslug, name=sname),
        agent_id=f"{agent_slug}.{sslug}_{over.get('session_num', 1)}",
        session_num=over.get("session_num", 1),
        status=over.get("status", "running"),
        journal=journal,
        config=over.get("config", {"frequency_sec": 90}),
        _last_tick_at=over.get("last_tick_at", 1_700_000_000.0),
        _last_error=over.get("last_error", ""),
        session_dir=over.get("session_dir"),
    )
    return engine


def _supervisor(monkeypatch, engines):
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor",
        lambda: SimpleNamespace(all=lambda: {e.agent_id: e for e in engines}),
    )


# ── The registry half ──


def test_every_strategy_is_an_owner_with_its_namespace(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _no_engines(monkeypatch)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")

    (owner,) = build_fleet_map()
    assert owner.run_key == "brigado.brl_mm"
    assert owner.namespace == "brigado-brl_mm"
    assert owner.agent_name == "Brigado"
    assert owner.strategy_name == "BRL MM"
    # No engine: still an owner — its bots may still be trading.
    assert owner.live is None


def test_a_legacy_bot_name_outside_the_namespace_is_declared(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _no_engines(monkeypatch)
    _write_agent(tmp_path, "river", "River")
    _write_strategy(
        tmp_path,
        "river",
        "scalper",
        "Scalper",
        config="default_config:\n  bot_name: river-scalper-legacy\n",
    )

    (owner,) = build_fleet_map()
    # Outside `river-scalper`? No — it is *inside*, so the prefix already proves
    # it and nothing needs declaring.
    assert owner.declared_bots == []

    reset_fleet_map_cache()
    _write_strategy(
        tmp_path,
        "river",
        "scalper",
        "Scalper",
        config="default_config:\n  bot_name: old_hand_bot\n",
    )
    (owner,) = build_fleet_map()
    assert owner.declared_bots == ["old_hand_bot"]


def test_sessions_on_disk_are_the_executor_tag_set(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _no_engines(monkeypatch)
    _write_agent(tmp_path, "brigado", "Brigado")
    d = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    (d / "sessions" / "session_3").mkdir(parents=True)
    (d / "sessions" / "session_7").mkdir(parents=True)

    (owner,) = build_fleet_map()
    assert owner.agent_ids == ["brigado.brl_mm_3", "brigado.brl_mm_7"]


# ── The live half ──


def test_a_live_loop_reports_its_session_tick_and_last_action(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    engine = _fake_engine(
        "brigado",
        "Brigado",
        "brl_mm",
        "BRL MM",
        session_num=7,
        tick_count=214,
        summary_text=(
            "Last tick: #214 at 10:02 UTC\n"
            "Status: running | PnL: $+1.20 | Open: 2 executors\n"
            "Last action: Spreads held; BTC vol falling, widening the ask side."
        ),
        last_tick_at=1_700_000_500.0,
    )
    _supervisor(monkeypatch, [engine])

    (owner,) = build_fleet_map()
    assert owner.live is not None
    assert owner.live.session_num == 7
    assert owner.live.status == "running"
    assert owner.live.tick_count == 214
    assert owner.live.frequency_sec == 90
    assert owner.live.last_tick_at == 1_700_000_500.0
    assert owner.live.last_action.startswith("Spreads held;")
    # The live session tags executors, so its id belongs in the tag set even
    # though no session dir was on disk when the registry was walked.
    assert "brigado.brl_mm_7" in owner.agent_ids


def test_a_paused_loop_says_paused(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _supervisor(
        monkeypatch,
        [_fake_engine("brigado", "Brigado", "brl_mm", "BRL MM", status="paused")],
    )

    (owner,) = build_fleet_map()
    assert owner.live.status == "paused"


def test_a_loop_whose_strategy_the_cache_has_not_seen_still_names_itself(
    monkeypatch, tmp_path
):
    """The registry is a minute stale; a loop started inside it is not lost."""
    _roots(monkeypatch, tmp_path)
    _supervisor(
        monkeypatch,
        [_fake_engine("brigado", "Brigado", "fresh", "Fresh", session_num=1)],
    )

    (owner,) = build_fleet_map()
    assert owner.run_key == "brigado.fresh"
    assert owner.namespace == "brigado-fresh"
    assert owner.agent_name == "Brigado"
    assert owner.live.session_num == 1


def test_the_newest_session_wins_when_two_engines_share_a_strategy(
    monkeypatch, tmp_path
):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _supervisor(
        monkeypatch,
        [
            _fake_engine("brigado", "Brigado", "brl_mm", "BRL MM", session_num=4),
            _fake_engine("brigado", "Brigado", "brl_mm", "BRL MM", session_num=9),
        ],
    )

    (owner,) = build_fleet_map()
    assert owner.live.session_num == 9


def test_one_bad_engine_does_not_lose_the_rest(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    broken = SimpleNamespace(agent_id="broken")  # no .agent at all
    good = _fake_engine("brigado", "Brigado", "brl_mm", "BRL MM")
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor",
        lambda: SimpleNamespace(all=lambda: {"broken": broken, "good": good}),
    )

    (owner,) = build_fleet_map()
    assert owner.live is not None


# ── What the route must never do ──


def test_the_map_makes_no_hummingbot_call(monkeypatch, tmp_path):
    """The bots page polls this; a trading-API call here would be a fleet-wide cost."""
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _supervisor(monkeypatch, [_fake_engine("brigado", "Brigado", "brl_mm", "BRL MM")])

    import config_manager

    async def _boom(
        *args, **kwargs
    ):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("fleet map reached for a Hummingbot client")

    monkeypatch.setattr(config_manager.ConfigManager, "get_client", _boom)

    assert build_fleet_map()


def test_the_route_answers_with_no_server_configured(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _no_engines(monkeypatch)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")

    from condor.web.routes.agents import get_fleet_map

    response = asyncio.run(get_fleet_map(user=SimpleNamespace(id=1, is_admin=True)))
    assert [owner.run_key for owner in response.owners] == ["brigado.brl_mm"]
    assert response.owners[0].live is None


def test_fleet_map_is_not_shadowed_by_the_slug_catch_all():
    """``/agents/fleet-map`` is a literal; ``/{slug}`` would swallow it."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/agents/fleet-map",
        "path_params": {},
    }
    for route in router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            assert route.endpoint.__name__ == "get_fleet_map"
            return
    raise AssertionError("no route matched /agents/fleet-map")


# ── The deed (FEAT-097) ──
#
# ``last_action`` is what the agent *said*; ``last_did`` is what it did. The
# band shows them as two separate statements, so the map must not conflate
# them — and a session with no log must read exactly as it did before.


def _acted(session_dir, **over):
    """Write one action to a session's log and hand back its directory."""
    from condor.agents.actions import actions_from_tool_calls, append_actions

    session_dir.mkdir(parents=True, exist_ok=True)
    call = {
        "id": "tc1",
        "name": over.get("tool", "create_grid_executor"),
        "status": over.get("status", "completed"),
        "input": over.get(
            "input", {"trading_pair": "SOL-USDC", "total_amount_quote": 100}
        ),
    }
    append_actions(
        session_dir,
        actions_from_tool_calls([call], tick=over.get("tick", 212), at=1.0),
    )
    return session_dir


def test_a_live_loop_reports_what_it_last_did(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    session_dir = _acted(tmp_path / "sessions" / "session_7")
    _supervisor(
        monkeypatch,
        [
            _fake_engine(
                "brigado",
                "Brigado",
                "brl_mm",
                "BRL MM",
                session_num=7,
                session_dir=session_dir,
                summary_text="Last action: Spreads held.",
            )
        ],
    )

    (owner,) = build_fleet_map()
    assert owner.live.last_did == {
        "tick": 212,
        "at": 1.0,
        "tool": "create_grid_executor",
        "verb": "create_grid_executor",
        "summary": "Create grid executor on SOL-USDC for 100 quote",
        "ok": True,
        "error": "",
        # Only a deploy has a bot name worth joining on (FEAT-102).
        "subject": "",
    }
    # The words are still the words. Two statements, not one.
    assert owner.live.last_action == "Spreads held."


def test_the_newest_deed_wins(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    session_dir = tmp_path / "sessions" / "session_7"
    _acted(session_dir, tick=1)
    _acted(session_dir, tick=2, tool="stop_executor", input={"executor_id": "abcdef"})
    _supervisor(
        monkeypatch,
        [_fake_engine("brigado", "B", "brl_mm", "M", session_dir=session_dir)],
    )

    (owner,) = build_fleet_map()
    assert owner.live.last_did["tick"] == 2
    assert owner.live.last_did["tool"] == "stop_executor"


def test_a_session_with_no_log_reads_exactly_as_before(monkeypatch, tmp_path):
    """Nothing is backfilled: an existing session shows only the words line."""
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    empty = tmp_path / "sessions" / "session_7"
    empty.mkdir(parents=True)
    _supervisor(
        monkeypatch,
        [_fake_engine("brigado", "B", "brl_mm", "M", session_dir=empty)],
    )

    (owner,) = build_fleet_map()
    assert owner.live.last_did is None


def test_an_unreadable_log_does_not_lose_the_loop(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    session_dir = tmp_path / "sessions" / "session_7"
    (session_dir / "actions.jsonl").mkdir(parents=True)
    _supervisor(
        monkeypatch,
        [_fake_engine("brigado", "B", "brl_mm", "M", session_dir=session_dir)],
    )

    (owner,) = build_fleet_map()
    assert owner.live is not None
    assert owner.live.last_did is None
