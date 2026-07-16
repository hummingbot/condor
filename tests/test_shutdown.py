"""Unit tests for the emergency-shutdown feature (FEAT-007).

Covers the declarative ``shutdown.md`` policy loader and its agent → default
resolution, the deterministic winddown on the NATIVE executor runtime
(policy → keep_position mapping, verify/alert on residual), and the engine
wrapper's idempotency guard.
"""

import asyncio
from types import SimpleNamespace

from condor.agents import agent as agent_module
from condor.agents import shutdown as shutdown_module
from condor.agents.agent import Agent
from condor.agents.engine import TickEngine
from condor.agents.shutdown import (
    DEFAULT_POLICY,
    POLICY_FLATTEN_ALL,
    POLICY_KEEP_ALL,
    POLICY_KEEP_SPOT_CLOSE_PERP,
    ShutdownPolicy,
    _holds_inventory,
    _is_perp,
    _keep_position,
    load_shutdown_policy,
    run_shutdown,
)


def _make_agent(tmp_path, monkeypatch) -> Agent:
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    a = Agent(slug="acme", name="Acme")
    a.agent_dir.mkdir(parents=True, exist_ok=True)
    return a


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


def test_resolution_prefers_agent_over_default(tmp_path, monkeypatch):
    a = _make_agent(tmp_path, monkeypatch)
    defaults_dir = tmp_path / "_defaults"

    _write_shutdown_md(
        defaults_dir / "shutdown.md", DEFAULT_POLICY, body="default body"
    )
    _write_shutdown_md(a.agent_dir / "shutdown.md", POLICY_KEEP_ALL, body="agent body")

    policy, body = load_shutdown_policy(a)
    assert policy.on_kill_switch == POLICY_KEEP_ALL
    assert body == "agent body"


def test_resolution_falls_back_to_default_file(tmp_path, monkeypatch):
    a = _make_agent(tmp_path, monkeypatch)
    _write_shutdown_md(
        tmp_path / "_defaults" / "shutdown.md", POLICY_FLATTEN_ALL, body="default body"
    )
    policy, body = load_shutdown_policy(a)
    assert policy.on_kill_switch == POLICY_FLATTEN_ALL
    assert body == "default body"


def test_resolution_no_files_returns_builtin_default(tmp_path, monkeypatch):
    a = _make_agent(tmp_path, monkeypatch)
    policy, body = load_shutdown_policy(a)
    assert policy.on_kill_switch == DEFAULT_POLICY
    assert body == ""


# ── policy → keep_position / verify mapping ──


def test_is_perp_detection():
    assert _is_perp("position_perp") is True
    assert _is_perp("order_perp") is True
    assert _is_perp("position_spot") is False
    assert _is_perp("order_spot") is False
    assert _is_perp("position_pred") is False
    # Unknown/ambiguous → treated as perp (conservative kill-switch default).
    assert _is_perp("") is True
    assert _is_perp("something_new") is True


def _record(id, type_, status="ACTIVE", state=None, config=None):
    return SimpleNamespace(
        id=id,
        type=type_,
        status=status,
        state=dict(state or {}),
        config=dict(config or {}),
        agent_id="01JZX5B7Q2K4N8P1T3V5W7Y9ZB",
        updated_at="",
        close_reason="",
    )


def test_keep_position_per_policy():
    spot = _record("e_spot", "position_spot")
    perp = _record("e_perp", "position_perp")
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


def test_holds_inventory():
    # Live position states hold venue inventory; a detached close does too.
    assert _holds_inventory(_record("e", "position_perp", state={"state": "ACTIVE"}))
    assert _holds_inventory(
        _record("e", "position_perp", status="CLOSED",
                state={"state": "COMPLETE", "close_type": "detached"})
    )
    assert not _holds_inventory(
        _record("e", "position_perp", status="CLOSED",
                state={"state": "COMPLETE", "close_type": "early_stop"})
    )
    # Order-kind records never count as held inventory here.
    assert not _holds_inventory(_record("e", "order_perp", state={"state": "ACTIVE"}))


# ── deterministic winddown (native runtime) ──


class _FakeStore:
    def __init__(self, records):
        self.records = records

    def load_by_slug(self, slug):
        return list(self.records)


class _FakeRuntime:
    def __init__(self, records):
        self.store = _FakeStore(records)
        self._executors = {}  # no live overlay in tests


class _FakeOps:
    """Stands in for condor.executors.ops: records stop calls and settles the
    record to CLOSED, unless the id is marked as stranded."""

    def __init__(self, runtime, strand_ids=()):
        self._runtime = runtime
        self._strand = set(strand_ids)
        self.stop_calls = []  # (executor_id, keep_position)

    async def stop(self, runtime, *, executor_id, keep_position=True):
        self.stop_calls.append((executor_id, keep_position))
        if executor_id in self._strand:
            return {"id": executor_id, "stopping": True}
        for r in self._runtime.store.records:
            if r.id == executor_id:
                r.status = "CLOSED"
                r.state["state"] = "COMPLETE"
                r.state["close_type"] = "detached" if keep_position else "early_stop"
        return {"id": executor_id, "stopping": True}


def _fake_engine(records, monkeypatch, tmp_path, strand_ids=()):
    """Build a duck-typed engine + fake runtime/ops for run_shutdown, with no
    shutdown.md on disk so the built-in default (keep_spot_close_perp) applies."""
    agent = _make_agent(tmp_path, monkeypatch)
    runtime = _FakeRuntime(records)
    fake_ops = _FakeOps(runtime, strand_ids=strand_ids)

    monkeypatch.setattr(shutdown_module, "peek_executor_runtime", lambda: runtime)
    monkeypatch.setattr(shutdown_module, "ops", fake_ops)
    monkeypatch.setattr(shutdown_module, "_SETTLE_DELAY_S", 0)

    notifications = []

    async def _notify(msg):
        notifications.append(msg)

    decisions = []

    engine = SimpleNamespace(
        agent=agent,
        agent_id="01JZX5B7Q2K4N8P1T3V5W7Y9ZB",
        user_id=7,
        chat_id=99,
        config={},
        _notify=_notify,
        record_decision=lambda action, note="": decisions.append((action, note)),
        decisions=decisions,
    )
    return engine, fake_ops, notifications


def test_winddown_keep_spot_close_perp(tmp_path, monkeypatch):
    records = [
        _record("e_perp", "position_perp", config={"coin": "ETH"}),
        _record("e_spot", "position_spot", config={"trading_pair": "BONK-SOL"}),
    ]
    engine, fake_ops, notes = _fake_engine(records, monkeypatch, tmp_path)
    asyncio.run(run_shutdown(engine, "test breach"))

    calls = dict(fake_ops.stop_calls)
    assert calls["e_perp"] is False  # perp closed
    assert calls["e_spot"] is True  # spot kept (detached)
    assert any("complete" in n for n in notes)
    assert not any("🚨" in n for n in notes)
    assert ("shutdown_done", "stopped=2, failures=0, verify=flat") in engine.decisions


def test_winddown_flatten_all_closes_everything(tmp_path, monkeypatch):
    records = [
        _record("e_perp", "position_perp", config={"coin": "ETH"}),
        _record("e_spot", "position_spot", config={"trading_pair": "BONK-SOL"}),
    ]
    engine, fake_ops, notes = _fake_engine(records, monkeypatch, tmp_path)
    # Force flatten_all via the agent-level shutdown.md.
    (engine.agent.agent_dir / "shutdown.md").write_text(
        "---\non_kill_switch: flatten_all\n---\nBody\n"
    )
    asyncio.run(run_shutdown(engine, "flat"))
    calls = dict(fake_ops.stop_calls)
    assert calls["e_perp"] is False
    assert calls["e_spot"] is False


def test_winddown_skips_terminal_records(tmp_path, monkeypatch):
    records = [
        _record("e_done", "position_perp", status="CLOSED",
                state={"state": "COMPLETE", "close_type": "early_stop"}),
        _record("e_live", "position_perp", config={"coin": "ETH"}),
    ]
    engine, fake_ops, notes = _fake_engine(records, monkeypatch, tmp_path)
    asyncio.run(run_shutdown(engine, "breach"))
    assert [c[0] for c in fake_ops.stop_calls] == ["e_live"]


def test_winddown_alerts_on_stranded_position(tmp_path, monkeypatch):
    # The perp executor never settles → still nonterminal on every re-read →
    # retried once, then a loud alert.
    records = [_record("e_perp", "position_perp", config={"coin": "ETH"})]
    engine, fake_ops, notes = _fake_engine(
        records, monkeypatch, tmp_path, strand_ids={"e_perp"}
    )
    asyncio.run(run_shutdown(engine, "breach"))
    assert any("🚨" in n and "ETH-USD" in n for n in notes)
    # Baseline stop + single verify retry, both closing.
    assert fake_ops.stop_calls == [("e_perp", False), ("e_perp", False)]


def test_winddown_alerts_on_detached_close_that_should_flatten(tmp_path, monkeypatch):
    # ops.stop "succeeds" but the executor detaches instead of closing: the
    # record goes terminal while its state still holds venue inventory.
    records = [_record("e_perp", "position_perp", config={"coin": "ETH"})]

    engine, fake_ops, notes = _fake_engine(records, monkeypatch, tmp_path)

    async def _detaching_stop(runtime, *, executor_id, keep_position=True):
        fake_ops.stop_calls.append((executor_id, keep_position))
        for r in records:
            if r.id == executor_id:
                r.status = "CLOSED"
                r.state["state"] = "COMPLETE"
                r.state["close_type"] = "detached"  # inventory left on venue
        return {"id": executor_id, "stopping": True}

    monkeypatch.setattr(
        shutdown_module, "ops", SimpleNamespace(stop=_detaching_stop)
    )
    asyncio.run(run_shutdown(engine, "breach"))
    assert any("🚨" in n and "ETH-USD" in n for n in notes)


def test_winddown_stop_failure_is_isolated_and_surfaced(tmp_path, monkeypatch):
    records = [
        _record("e_bad", "position_perp", config={"coin": "ETH"}),
        _record("e_ok", "position_perp", config={"coin": "SOL"}),
    ]
    engine, fake_ops, notes = _fake_engine(records, monkeypatch, tmp_path)
    real_stop = fake_ops.stop

    async def _flaky_stop(runtime, *, executor_id, keep_position=True):
        if executor_id == "e_bad":
            fake_ops.stop_calls.append((executor_id, keep_position))
            raise RuntimeError("venue exploded")
        return await real_stop(
            runtime, executor_id=executor_id, keep_position=keep_position
        )

    monkeypatch.setattr(
        shutdown_module, "ops", SimpleNamespace(stop=_flaky_stop)
    )
    asyncio.run(run_shutdown(engine, "breach"))
    # The healthy executor was still stopped, and the failure is surfaced.
    assert ("e_ok", False) in fake_ops.stop_calls
    assert any("winddown error" in n for n in notes)


def test_winddown_no_runtime_alerts_loudly(tmp_path, monkeypatch):
    engine, fake_ops, notes = _fake_engine([], monkeypatch, tmp_path)
    monkeypatch.setattr(shutdown_module, "peek_executor_runtime", lambda: None)
    asyncio.run(run_shutdown(engine, "breach"))
    assert any("🚨" in n and "could NOT reach" in n for n in notes)
    assert ("shutdown_failed", "no executor runtime") in engine.decisions


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
        agent_id="01JZX5B7Q2K4N8P1T3V5W7Y9ZB",
        _notify=_notify,
        _end_run=lambda status, reason="": None,
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


def _engine_with_llm(records, tmp_path, monkeypatch, body):
    engine, fake_ops, notes = _fake_engine(records, monkeypatch, tmp_path)
    (engine.agent.agent_dir / "shutdown.md").write_text(
        f"---\non_kill_switch: flatten_all\n---\n{body}\n"
    )
    return engine, fake_ops, notes


def test_llm_cleanup_invoked_with_body(tmp_path, monkeypatch):
    from condor.agents import run as run_module

    records = [_record("e1", "position_perp", config={"coin": "ETH"})]
    engine, fake_ops, notes = _engine_with_llm(
        records, tmp_path, monkeypatch, body="Do cleanup."
    )
    seen = {}

    async def fake_run_agent(agent, prompt, **kwargs):
        seen["agent"] = agent
        seen["prompt"] = prompt
        seen.update(kwargs)
        return run_module.RunResult(text="done")

    monkeypatch.setattr(run_module, "run_agent", fake_run_agent)
    asyncio.run(run_shutdown(engine, "breach"))
    # The cleanup body is the task inside the built prompt; the pass runs
    # unattended (AUTO policy → permission_policy is None).
    assert "Do cleanup." in seen["prompt"]
    assert seen["agent"].slug == "acme"
    assert seen["permission_policy"] is None


def test_llm_cleanup_failure_does_not_block_winddown(tmp_path, monkeypatch):
    from condor.agents import run as run_module

    records = [_record("e1", "position_perp", config={"coin": "ETH"})]
    engine, fake_ops, notes = _engine_with_llm(
        records, tmp_path, monkeypatch, body="Cleanup."
    )

    async def boom(agent, prompt, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(run_module, "run_agent", boom)
    asyncio.run(run_shutdown(engine, "breach"))
    # The deterministic floor still ran and the winddown completed cleanly.
    assert dict(fake_ops.stop_calls) == {"e1": False}
    assert any("complete" in n for n in notes)
    assert not any("🚨" in n for n in notes)
