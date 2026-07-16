"""Regression tests for the 2026-07-15 review findings.

Covers: stop semantics (#1), runtime-enforced risk caps (#2), launch-config
deep-merge + validation (#3), filled-order holdings (#4), interrupted-session
sweep (#5), cross-session executor visibility (#6), ACP payload capture (#9),
control-socket hardening (#11), fixed-rate cadence (#14), directive
durability (#15).
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import condor.executors.service as service
from condor.executors.base import ExecutorStatus
from condor.executors.log import ExecutorLog
from condor.executors.order import OrderExecutor, OrderPredConfig, OrderStates
from condor.executors.position import PositionExecutor, PositionSpotConfig, PositionStates

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"


def pos_config(**over):
    kw = dict(
        chain_network="solana-mainnet-beta",
        wallet_address=WALLET,
        base_token="Mint111",
        quote_token="USDC",
        amount_quote=Decimal("1"),
        update_interval=0.01,
    )
    kw.update(over)
    return PositionSpotConfig(**kw)


# -- #3: launch config deep-merge + strict risk validation -----------------------


def test_merge_launch_config_partial_risk_override_keeps_baseline():
    from condor.agents.config import merge_launch_config, normalize_config

    base = normalize_config(
        {"risk_limits": {"max_position_size_quote": 50, "max_open_executors": 2}}
    )
    merged = merge_launch_config(base, {"risk_limits": {"max_drawdown_pct": 10}})
    rl = merged["risk_limits"]
    # The partial override must MERGE, not replace: baseline 50/2 survives.
    assert rl["max_position_size_quote"] == 50
    assert rl["max_open_executors"] == 2
    assert rl["max_drawdown_pct"] == 10


def test_merge_launch_config_rejects_unknown_risk_key():
    from condor.agents.config import merge_launch_config, normalize_config

    base = normalize_config({})
    with pytest.raises(Exception, match="max_position_size_quotee"):
        merge_launch_config(base, {"risk_limits": {"max_position_size_quotee": 5}})


def test_merge_launch_config_rejects_out_of_range_drawdown():
    from condor.agents.config import merge_launch_config, normalize_config

    base = normalize_config({})
    with pytest.raises(Exception, match="drawdown"):
        merge_launch_config(base, {"risk_limits": {"max_drawdown_pct": 250}})
    with pytest.raises(Exception):
        merge_launch_config(base, {"risk_limits": {"max_position_size_quote": -5}})


# -- #2: risk caps enforced at ops.create (platform invariant) --------------------


class _FakeAgentStore:
    def __init__(self, limits):
        self._limits = limits

    def get(self, slug):
        return SimpleNamespace(slug=slug, risk_limits=self._limits)


def _seed_open(store, agent_id, n, amount_quote=Decimal("1")):
    gw = SimpleNamespace()
    for i in range(n):
        ex = PositionExecutor(
            f"pos_{agent_id}_{i}",
            pos_config(agent_slug="mm", agent_id=agent_id, amount_quote=amount_quote),
            gw, store,
        )
        ex.status = ExecutorStatus.ACTIVE
        ex.state.state = PositionStates.ACTIVE
        ex.state.amount_spent = amount_quote
        store.save(ex)


def test_runtime_cap_blocks_create_over_open_count(tmp_path, monkeypatch):
    from condor.executors import ops

    store = ExecutorLog(tmp_path)
    _seed_open(store, "mm_1", 2)
    runtime = SimpleNamespace(store=store)

    import condor.agents.agent as agent_mod

    monkeypatch.setattr(
        agent_mod, "AgentStore", lambda: _FakeAgentStore({"max_open_executors": 2})
    )

    with pytest.raises(ops.ExecutorOpError) as ei:
        ops._enforce_agent_caps(
            runtime, "mm", SimpleNamespace(max_notional_quote=Decimal("1"))
        )
    assert ei.value.status == 409
    assert "max_open_executors" in ei.value.message


def test_runtime_cap_counts_other_sessions_of_same_slug(tmp_path, monkeypatch):
    """Concurrent sessions/delegations of one agent share the cap: mm_1's open
    executors count against a create attributed to mm_2."""
    from condor.executors import ops

    store = ExecutorLog(tmp_path)
    _seed_open(store, "mm_1", 1, amount_quote=Decimal("40"))
    _seed_open(store, "mm-d3", 1, amount_quote=Decimal("40"))
    runtime = SimpleNamespace(store=store)

    import condor.agents.agent as agent_mod

    monkeypatch.setattr(
        agent_mod,
        "AgentStore",
        lambda: _FakeAgentStore({"max_position_size_quote": 100}),
    )

    with pytest.raises(ops.ExecutorOpError) as ei:
        ops._enforce_agent_caps(
            runtime, "mm", SimpleNamespace(max_notional_quote=Decimal("30"))
        )
    assert "max_position_size_quote" in ei.value.message

    # Within cap passes.
    ops._enforce_agent_caps(
        runtime, "mm", SimpleNamespace(max_notional_quote=Decimal("15"))
    )


def test_runtime_cap_no_baseline_is_permissive(tmp_path, monkeypatch):
    from condor.executors import ops

    store = ExecutorLog(tmp_path)
    runtime = SimpleNamespace(store=store)

    import condor.agents.agent as agent_mod

    monkeypatch.setattr(agent_mod, "AgentStore", lambda: _FakeAgentStore({}))
    ops._enforce_agent_caps(
        runtime, "mm", SimpleNamespace(max_notional_quote=Decimal("10_000"))
    )
    # And unattributed creates (no slug) are out of scope here.
    ops._enforce_agent_caps(
        runtime, "", SimpleNamespace(max_notional_quote=Decimal("10_000"))
    )


# -- #5: interrupted-session sweep ------------------------------------------------


def test_interrupted_run_sweep(tmp_path):
    """§4.2: runs are memory-only — a run_started stream lacking run_ended is
    closed as interrupted at startup; ended runs are untouched."""
    from condor.agents.runstore import RunStore

    store = RunStore(root=tmp_path)
    r1 = store.start_run("mm", "session")
    r2 = store.start_run("mm", "session")
    store.end_run(r2, "stopped")
    # Simulate process death: forget r1's writer without run_ended.
    store._writers[r1].close()
    store._writers.clear()

    fresh = RunStore(root=tmp_path)
    fresh.sweep_interrupted()
    assert fresh.run_meta("mm", r1)["status"] == "interrupted"
    assert fresh.run_meta("mm", r2)["status"] == "stopped"


# -- #6 + #4: cross-session visibility + filled-order holdings --------------------


def _pred_config(**over):
    kw = dict(
        market="world-cup-winner",
        position="LONG",
        amount_quote=Decimal("10"),
        order_type="limit",
        limit_px=Decimal("0.5"),
        update_interval=0.01,
    )
    kw.update(over)
    return OrderPredConfig(**kw)


def test_provider_sees_prior_session_executors_and_holdings(tmp_path, monkeypatch):
    from condor.agents.providers.native_executors import NativeExecutorsProvider

    store = ExecutorLog(tmp_path)
    gw = SimpleNamespace()

    # Open position left by session 1 (the interrupted one).
    survivor = PositionExecutor("pos_old", pos_config(agent_slug="wc", agent_id="wc_1"), gw, store)
    survivor.status = ExecutorStatus.ACTIVE
    survivor.state.state = PositionStates.ACTIVE
    survivor.state.amount_spent = Decimal("1")
    store.save(survivor)

    # Filled order_pred from session 1: 20 shares @ 0.5 = $10 cost basis.
    filled = OrderExecutor("ord_filled", _pred_config(agent_slug="wc", agent_id="wc_1"), gw, store)
    filled.status = ExecutorStatus.CLOSED
    filled.state.state = OrderStates.DONE
    filled.state.size = Decimal("20")
    filled.state.entry_price = Decimal("0.5")
    filled.state.close_type = "filled"
    store.save(filled)

    # Cancelled (early_stop) order must NOT become a holding.
    pulled = OrderExecutor("ord_pulled", _pred_config(agent_slug="wc", agent_id="wc_1"), gw, store)
    pulled.status = ExecutorStatus.CLOSED
    pulled.state.state = OrderStates.DONE
    pulled.state.close_type = "early_stop"
    store.save(pulled)

    monkeypatch.setattr(service, "_runtime", SimpleNamespace(store=store))

    # Session 2 (fresh id) must still see both the survivor and the holding.
    result = asyncio.run(NativeExecutorsProvider().execute({}, agent_id="wc_2", agent_slug="wc"))
    rows = {e["id"]: e for e in result.data["executors"]}
    assert "pos_old" in rows, "prior-session open executor must stay visible"
    assert rows["pos_old"]["owner"] == "wc_1"
    holding = [e for e in result.data["executors"] if e["type"] == "holding_pred"]
    assert len(holding) == 1
    assert holding[0]["size"] == pytest.approx(20.0)
    assert holding[0]["amount"] == pytest.approx(10.0)  # cost basis
    # Exposure carries both (1 + 10).
    assert result.data["total_exposure"] == pytest.approx(11.0)
    assert "prior session" in result.summary


def test_provider_typed_rows_for_pred_orders(tmp_path, monkeypatch):
    from condor.agents.providers.native_executors import NativeExecutorsProvider

    store = ExecutorLog(tmp_path)
    resting = OrderExecutor(
        "ord_resting", _pred_config(agent_slug="wc", agent_id="wc_1", position="SHORT"), SimpleNamespace(), store
    )
    resting.status = ExecutorStatus.ACTIVE
    resting.state.state = OrderStates.RESTING
    store.save(resting)

    monkeypatch.setattr(service, "_runtime", SimpleNamespace(store=store))
    result = asyncio.run(NativeExecutorsProvider().execute({}, agent_id="wc_1", agent_slug="wc"))
    (row,) = result.data["executors"]
    assert row["pair"] == "world-cup-winner:No"  # not "?-?"
    assert row["side"] == "SHORT"
    assert row["price"] == pytest.approx(0.5)
    assert "?-?" not in result.summary


# -- #9: ACP payload variants -----------------------------------------------------


def test_extract_tool_output_variants():
    from condor.acp.client import _extract_tool_output

    assert _extract_tool_output({"rawOutput": {"ok": True}}) == '{"ok": true}'
    assert _extract_tool_output({"output": "plain"}) == "plain"
    assert (
        _extract_tool_output(
            {"content": [{"type": "content", "content": {"type": "text", "text": "hi"}}]}
        )
        == "hi"
    )
    assert _extract_tool_output({}) is None


# -- #11: control socket hardening -------------------------------------------------


def test_control_server_refuses_to_steal_live_socket():
    import uuid

    from condor.control.server import ControlServer

    # Short path: macOS caps AF_UNIX socket paths at ~104 chars.
    sock = f"/tmp/condor-rf-{uuid.uuid4().hex[:8]}.sock"

    async def run():
        first = ControlServer({}, socket_path=sock)
        await first.start()
        # Socket file is owner-only.
        assert (Path(sock).stat().st_mode & 0o777) == 0o600
        second = ControlServer({}, socket_path=sock)
        with pytest.raises(RuntimeError, match="already live"):
            await second.start()
        await first.stop()
        # Path proven stale now — a new server may claim it.
        third = ControlServer({}, socket_path=sock)
        await third.start()
        await third.stop()

    asyncio.run(run())


# -- #1: plain stop defaults to keep_position=True ----------------------------------


def test_engine_stop_defaults_to_detach(monkeypatch):
    """A plain stop must NOT liquidate: default keep_position True unless the
    config explicitly opts into close-out."""
    from condor.agents.config import AgentConfig

    assert AgentConfig().keep_position_on_stop is True

    # And the engine passes it through: simulate the stop() executor branch.
    calls = {}

    class _RT:
        def stop_agent_executors(self, agent_id, keep_position):
            calls["keep_position"] = keep_position
            return []

    monkeypatch.setattr(service, "_runtime", _RT())

    from condor.agents import engine as engine_mod

    eng = SimpleNamespace(
        config={},
        agent_id="mm_1",
        _task=None,
        _active_client=None,
        _running=False,
        _end_run=lambda status, reason="": None,
    )
    asyncio.run(engine_mod.TickEngine.stop(eng))
    assert calls["keep_position"] is True


# -- #15 + #8: directive durability + post-tick snapshot state ----------------------


def _make_engine(tmp_path, monkeypatch):
    from condor.agents import agent as agent_module
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.runstore import RunStore, set_run_store

    root = tmp_path / "agents"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(agent_module, "_DATA_ROOT", root)
    set_run_store(RunStore(root=root))
    agent = Agent(
        slug="acme", name="Acme", agent_key="claude-code"
    )
    agent.agent_dir.mkdir(parents=True, exist_ok=True)
    return TickEngine(agent=agent, config={}, chat_id=1, user_id=1)


def test_directives_survive_failed_tick_and_clear_on_success(tmp_path, monkeypatch):
    """A directive carried by a tick whose run_agent crashes must remain queued;
    it is acknowledged only once a tick completes with it."""
    from condor.agents import engine as engine_mod

    engine = _make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service, "_runtime", SimpleNamespace(store=ExecutorLog(tmp_path / "log"))
    )

    async def boom(*a, **kw):
        raise RuntimeError("model down")

    monkeypatch.setattr(engine_mod, "run_agent", boom)
    engine.inject_directive("tighten stops")
    with pytest.raises(RuntimeError):
        asyncio.run(engine._tick())
    assert engine._pending_directives == ["tighten stops"]

    seen = {}

    async def ok(agent, prompt, **kw):
        seen["prompt"] = prompt
        return SimpleNamespace(text="done", tool_calls=[], timed_out=False)

    monkeypatch.setattr(engine_mod, "run_agent", ok)
    asyncio.run(engine._tick())
    assert "tighten stops" in seen["prompt"]
    assert engine._pending_directives == []
