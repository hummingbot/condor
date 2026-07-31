"""Unit tests for the emergency-shutdown feature (FEAT-007).

Covers the declarative ``shutdown.md`` policy loader and its strategy → agent →
default resolution, the deterministic winddown (policy → keep_position mapping,
verify/alert on residual), and the engine wrapper's idempotency guard.
"""

import asyncio
from types import SimpleNamespace

from condor.agents import shutdown as shutdown_module
from condor.agents import strategy as strategy_module
from condor.agents.engine import TickEngine
from condor.agents.shutdown import (
    DEFAULT_POLICY,
    POLICY_FLATTEN_ALL,
    POLICY_KEEP_ALL,
    POLICY_KEEP_SPOT_CLOSE_PERP,
    ShutdownPolicy,
    _is_perp,
    _keep_position,
    _should_remain_open,
    load_shutdown_policy,
    run_shutdown,
)
from condor.agents.strategy import Strategy


def _make_strategy(tmp_path, monkeypatch) -> Strategy:
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    s = Strategy(agent_slug="acme", name="Scalper")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


def _write_shutdown_md(
    path, policy: str, cancel: bool = True, body: str = "Body."
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\non_kill_switch: {policy}\ncancel_open_orders: {str(cancel).lower()}\n---\n\n{body}\n"
    )


# ── ShutdownPolicy.from_dict ──


def test_policy_from_dict_valid():
    p = ShutdownPolicy.from_dict(
        {"on_kill_switch": POLICY_FLATTEN_ALL, "cancel_open_orders": False}
    )
    assert p.on_kill_switch == POLICY_FLATTEN_ALL
    assert p.cancel_open_orders is False


def test_policy_from_dict_unknown_falls_back_to_default():
    p = ShutdownPolicy.from_dict({"on_kill_switch": "nuke_everything"})
    assert p.on_kill_switch == DEFAULT_POLICY  # keep_spot_close_perp
    assert p.cancel_open_orders is True  # default


def test_policy_from_dict_empty_is_default():
    p = ShutdownPolicy.from_dict({})
    assert p.on_kill_switch == DEFAULT_POLICY


# ── load_shutdown_policy resolution ──


def test_resolution_prefers_strategy_over_agent_over_default(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    agent_dir = s.dir.parent.parent  # {root}/acme
    defaults_dir = tmp_path / "_defaults"

    _write_shutdown_md(
        defaults_dir / "shutdown.md", DEFAULT_POLICY, body="default body"
    )
    _write_shutdown_md(agent_dir / "shutdown.md", POLICY_KEEP_ALL, body="agent body")
    _write_shutdown_md(s.dir / "shutdown.md", POLICY_FLATTEN_ALL, body="strategy body")

    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_FLATTEN_ALL
    assert body == "strategy body"


def test_resolution_falls_back_to_agent(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    agent_dir = s.dir.parent.parent
    _write_shutdown_md(tmp_path / "_defaults" / "shutdown.md", DEFAULT_POLICY)
    _write_shutdown_md(agent_dir / "shutdown.md", POLICY_KEEP_ALL, body="agent body")

    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_KEEP_ALL
    assert body == "agent body"


def test_resolution_falls_back_to_default_file(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    _write_shutdown_md(
        tmp_path / "_defaults" / "shutdown.md", POLICY_FLATTEN_ALL, body="default body"
    )
    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_FLATTEN_ALL
    assert body == "default body"


def test_resolution_no_files_returns_builtin_default(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == DEFAULT_POLICY
    assert body == ""


# ── policy → keep_position / verify mapping ──


def test_is_perp_detection():
    assert _is_perp("binance_perpetual") is True
    assert _is_perp("hyperliquid_perpetual") is True
    assert _is_perp("binance") is False
    assert _is_perp("kucoin") is False
    # Unknown/ambiguous → treated as perp (conservative kill-switch default).
    assert _is_perp("") is True


def test_keep_position_per_policy():
    spot = {"connector": "binance"}
    perp = {"connector": "binance_perpetual"}
    flatten = ShutdownPolicy(POLICY_FLATTEN_ALL)
    keep_all = ShutdownPolicy(POLICY_KEEP_ALL)
    hybrid = ShutdownPolicy(POLICY_KEEP_SPOT_CLOSE_PERP)

    assert _keep_position(spot, flatten) is False
    assert _keep_position(perp, flatten) is False
    assert _keep_position(spot, keep_all) is True
    assert _keep_position(perp, keep_all) is True
    # keep_spot_close_perp: keep spot, close perp
    assert _keep_position(spot, hybrid) is True
    assert _keep_position(perp, hybrid) is False


def test_should_remain_open_per_policy():
    spot = {"connector_name": "binance", "trading_pair": "BTC-USDT"}
    perp = {"connector_name": "binance_perpetual", "trading_pair": "BTC-USDT"}
    hybrid = ShutdownPolicy(POLICY_KEEP_SPOT_CLOSE_PERP)
    assert _should_remain_open(spot, hybrid) is True
    assert _should_remain_open(perp, hybrid) is False
    assert _should_remain_open(perp, ShutdownPolicy(POLICY_KEEP_ALL)) is True
    assert _should_remain_open(spot, ShutdownPolicy(POLICY_FLATTEN_ALL)) is False


# ── deterministic winddown ──


class _FakeExecutorsAPI:
    def __init__(self, positions_sequence):
        # positions_sequence: list of position-lists returned on successive calls
        self._positions_sequence = list(positions_sequence)
        self.stop_calls = []  # (executor_id, keep_position)

    async def stop_executor(self, executor_id, keep_position=False):
        self.stop_calls.append((executor_id, keep_position))
        return {"status": "ok"}

    async def get_positions_summary(self, controller_id=None):
        if len(self._positions_sequence) > 1:
            return {"positions": self._positions_sequence.pop(0)}
        return {
            "positions": self._positions_sequence[0] if self._positions_sequence else []
        }


class _FakeClient:
    def __init__(self, positions_sequence):
        self.executors = _FakeExecutorsAPI(positions_sequence)


class _FakeJournal:
    def __init__(self):
        self.tick_count = 0
        self.actions = []
        self.ticks = []

    def append_action(self, tick, action, reasoning, risk_note=""):
        self.actions.append((action, reasoning))

    def record_tick(self, summary="", actions=0):
        self.ticks.append(summary)
        self.tick_count += 1
        return self.tick_count


def _fake_engine(running_executors, positions_sequence, monkeypatch, tmp_path):
    """Build a duck-typed engine sufficient for run_shutdown, with no shutdown.md
    on disk so the built-in default (keep_spot_close_perp) applies."""
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    strat = Strategy(agent_slug="acme", name="Scalper")

    class _Registry:
        async def run_core_providers(self, client, config, agent_id=""):
            return {"executors": SimpleNamespace(data={"executors": running_executors})}

    client = _FakeClient(positions_sequence)
    notifications = []

    async def _notify(msg):
        notifications.append(msg)

    async def _get_client():
        return client

    engine = SimpleNamespace(
        strategy=strat,
        agent_id="acme.scalper_1",
        config={},
        journal=_FakeJournal(),
        provider_registry=_Registry(),
        _last_skill_data={"executors": running_executors},
        _get_client=_get_client,
        _notify=_notify,
    )
    return engine, client, notifications


def test_winddown_keep_spot_close_perp(tmp_path, monkeypatch):
    running = [
        {"id": "e_perp", "connector": "binance_perpetual"},
        {"id": "e_spot", "connector": "binance"},
    ]
    # No positions remain after the baseline stop → clean shutdown.
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    asyncio.run(run_shutdown(engine, "test breach"))

    calls = dict(client.executors.stop_calls)
    assert calls["e_perp"] is False  # perp closed
    assert calls["e_spot"] is True  # spot kept
    assert any("complete" in n for n in notes)
    assert not any("🚨" in n for n in notes)
    assert ("shutdown_done", "stopped=2, failures=0, verify=flat") in [
        (a, r) for a, r in engine.journal.actions
    ]


def test_winddown_flatten_all_closes_everything(tmp_path, monkeypatch):
    running = [
        {"id": "e_perp", "connector": "binance_perpetual"},
        {"id": "e_spot", "connector": "binance"},
    ]
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    # Force flatten_all via a strategy-level shutdown.md.
    (engine.strategy.dir).mkdir(parents=True, exist_ok=True)
    (engine.strategy.dir / "shutdown.md").write_text(
        "---\non_kill_switch: flatten_all\n---\nBody\n"
    )
    asyncio.run(run_shutdown(engine, "flat"))
    calls = dict(client.executors.stop_calls)
    assert calls["e_perp"] is False
    assert calls["e_spot"] is False


def test_winddown_alerts_on_stranded_position(tmp_path, monkeypatch):
    running = [{"id": "e_perp", "connector": "binance_perpetual"}]
    # A perp position stays open on every re-query → stranded → loud alert.
    stuck = [{"connector_name": "binance_perpetual", "trading_pair": "ETH-USDT"}]
    engine, client, notes = _fake_engine(running, [stuck], monkeypatch, tmp_path)
    asyncio.run(run_shutdown(engine, "breach"))
    assert any("🚨" in n and "ETH-USDT" in n for n in notes)


def test_winddown_no_client_alerts_loudly(tmp_path, monkeypatch):
    engine, client, notes = _fake_engine([], [[]], monkeypatch, tmp_path)

    async def _no_client():
        return None

    engine._get_client = _no_client
    asyncio.run(run_shutdown(engine, "breach"))
    assert any("🚨" in n and "could NOT reach the API" in n for n in notes)


# ── engine wrapper idempotency ──


def test_run_shutdown_idempotent(monkeypatch):
    calls = []

    async def fake_run_shutdown(engine, reason):
        calls.append(reason)

    monkeypatch.setattr(shutdown_module, "run_shutdown", fake_run_shutdown)

    notifications = []

    async def _notify(msg):
        notifications.append(msg)

    stub = SimpleNamespace(
        _shutting_down=False,
        _running=True,
        _paused=False,
        _task=None,
        _active_client=None,
        journal=None,
        agent_id="acme.scalper_1",
        _notify=_notify,
    )

    async def _drive():
        await TickEngine._run_shutdown(stub, "first")
        await TickEngine._run_shutdown(stub, "second")

    asyncio.run(_drive())
    assert calls == ["first"]  # second call is a guarded no-op
    assert stub._running is False
    assert stub._shutting_down is True


# ── soft-vs-hard drawdown triggers ──


class _FakeTracker:
    def __init__(self, drawdown_pct):
        self._dd = drawdown_pct

    def get_total_exposure(self):
        return 0.0

    def get_open_executor_count(self):
        return 0

    def get_drawdown_pct(self):
        return self._dd


def _risk(soft, hard):
    from condor.agents.risk import RiskEngine, RiskLimits

    return RiskEngine(RiskLimits(max_drawdown_pct=soft, shutdown_drawdown_pct=hard))


def test_drawdown_below_soft_does_nothing():
    state = _risk(soft=10.0, hard=20.0).get_state(_FakeTracker(5.0))
    assert state.is_blocked is False
    assert state.should_shutdown is False


def test_drawdown_between_soft_and_hard_pauses_only():
    state = _risk(soft=10.0, hard=20.0).get_state(_FakeTracker(15.0))
    assert state.is_blocked is True
    assert state.should_shutdown is False


def test_drawdown_beyond_hard_triggers_shutdown():
    state = _risk(soft=10.0, hard=20.0).get_state(_FakeTracker(25.0))
    assert state.is_blocked is True  # also over soft
    assert state.should_shutdown is True
    assert "shutdown limit" in state.shutdown_reason


def test_shutdown_threshold_disabled_by_default():
    # hard = -1 (disabled): even a huge drawdown never escalates to shutdown.
    state = _risk(soft=-1.0, hard=-1.0).get_state(_FakeTracker(99.0))
    assert state.should_shutdown is False
    assert state.is_blocked is False


# ── bounded LLM cleanup pass ──


def _engine_with_llm(running, positions_seq, tmp_path, monkeypatch, body):
    engine, client, notes = _fake_engine(running, positions_seq, monkeypatch, tmp_path)
    engine.agent = SimpleNamespace(slug="acme")
    engine.user_id = 7
    engine.chat_id = 99
    engine.strategy.dir.mkdir(parents=True, exist_ok=True)
    (engine.strategy.dir / "shutdown.md").write_text(
        f"---\non_kill_switch: flatten_all\n---\n{body}\n"
    )
    return engine, client, notes


def test_llm_cleanup_invoked_with_body(tmp_path, monkeypatch):
    from condor.agents import consult as consult_module

    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )
    seen = {}

    async def fake_complete(**kwargs):
        seen.update(kwargs)
        return "done"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_complete)
    asyncio.run(run_shutdown(engine, "breach"))
    assert seen["task"] == "Do cleanup."
    assert seen["slug"] == "acme"
    assert seen["permission_callback"] is None


def test_llm_cleanup_failure_does_not_block_winddown(tmp_path, monkeypatch):
    from condor.agents import consult as consult_module

    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Cleanup."
    )

    async def boom(**kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", boom)
    asyncio.run(run_shutdown(engine, "breach"))
    # The deterministic floor still ran and the winddown completed cleanly.
    assert dict(client.executors.stop_calls) == {"e1": False}
    assert any("complete" in n for n in notes)
    assert not any("🚨" in n for n in notes)
