"""Unit tests for the emergency-shutdown feature (FEAT-007).

Covers the declarative ``shutdown.md`` policy loader and its strategy → agent →
default resolution, the deterministic winddown (policy → keep_position mapping,
verify/alert on residual), and the engine wrapper's idempotency guard.
"""

import asyncio
from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace

import pytest

import condor.agents.journal as journal_mod
from condor.agents import shutdown as shutdown_module
from condor.agents import strategy as strategy_module
from condor.agents.engine import TickEngine
from condor.agents.journal import JournalManager
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
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    s = Strategy(agent_slug="acme", name="Scalper")
    s.home.mkdir(parents=True, exist_ok=True)
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
    agent_dir = s.home.parent.parent  # {local root}/acme
    defaults_dir = tmp_path / "_defaults"

    _write_shutdown_md(
        defaults_dir / "shutdown.md", DEFAULT_POLICY, body="default body"
    )
    _write_shutdown_md(agent_dir / "shutdown.md", POLICY_KEEP_ALL, body="agent body")
    _write_shutdown_md(s.home / "shutdown.md", POLICY_FLATTEN_ALL, body="strategy body")

    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_FLATTEN_ALL
    assert body == "strategy body"


def test_resolution_falls_back_to_agent(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    agent_dir = s.home.parent.parent
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

    @contextmanager
    def batch(self):
        yield self

    def append_action(self, tick, action, reasoning, risk_note=""):
        self.actions.append((action, reasoning))

    def record_tick(self, summary="", actions=0):
        self.ticks.append(summary)
        self.tick_count += 1
        return self.tick_count


def _fake_engine(running_executors, positions_sequence, monkeypatch, tmp_path):
    """Build a duck-typed engine sufficient for run_shutdown, with no shutdown.md
    on disk so the built-in default (keep_spot_close_perp) applies."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    strat = Strategy(agent_slug="acme", name="Scalper")

    class _Registry:
        """Records each provider run; a full core sweep fails loudly (PERF-641)."""

        def __init__(self):
            self.calls: list[dict] = []

        async def run_core_providers(
            self, client, config, agent_id="", bot_names=None, owned=None, names=None
        ):
            assert names is not None, "shutdown must not run every core provider"
            self.calls.append(
                {"names": list(names), "bot_names": bot_names, "owned": owned}
            )
            if "executors" not in names:
                return {}
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
        # Executor-mode session: no bot, so no ownership window to release.
        ledger=None,
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
    (engine.strategy.home).mkdir(parents=True, exist_ok=True)
    (engine.strategy.home / "shutdown.md").write_text(
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


# ── journal write amplification (PERF-173) ──


@pytest.fixture
def journal_writes(monkeypatch) -> list[str]:
    """Record every full rewrite of a journal.md, in order."""
    recorded: list[str] = []
    original = journal_mod.atomic_write_text

    def spy(path, text, **kwargs):
        if str(path).endswith("journal.md"):
            recorded.append(text)
        return original(path, text, **kwargs)

    monkeypatch.setattr(journal_mod, "atomic_write_text", spy)
    return recorded


def _with_real_journal(engine, tmp_path, journal_writes=None) -> JournalManager:
    engine.journal = JournalManager(engine.agent_id, session_dir=tmp_path)
    # Creating the journal writes the template (atomically, CORR-193); that
    # bootstrap write is not part of the winddown behavior under test.
    if journal_writes is not None:
        journal_writes.clear()
    return engine.journal


def test_winddown_rewrites_the_journal_once_at_the_end(
    tmp_path, monkeypatch, journal_writes
):
    """The terminal pair (append_action + record_tick) wrote the file twice."""
    running = [{"id": "e_perp", "connector": "binance_perpetual"}]
    engine, _client, _notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    _with_real_journal(engine, tmp_path, journal_writes)

    asyncio.run(run_shutdown(engine, "breach"))

    # shutdown_start (its own write, so it lands before the API-bound winddown)
    # plus ONE write for the shutdown_done pair -- three writes before.
    assert len(journal_writes) == 2
    last = journal_writes[-1]
    assert "shutdown_done" in last and "- tick#1 " in last


def test_winddown_without_client_rewrites_the_journal_once(
    tmp_path, monkeypatch, journal_writes
):
    engine, _client, _notes = _fake_engine([], [[]], monkeypatch, tmp_path)
    _with_real_journal(engine, tmp_path, journal_writes)

    async def _no_client():
        return None

    engine._get_client = _no_client
    asyncio.run(run_shutdown(engine, "breach"))

    assert len(journal_writes) == 2
    last = journal_writes[-1]
    assert "shutdown_failed" in last and "- tick#1 " in last


def test_a_winddown_that_raises_still_journals_what_it_recorded(
    tmp_path, monkeypatch, journal_writes
):
    """The batch flushes in a finally, so the recorded action is not lost."""
    running = [{"id": "e_perp", "connector": "binance_perpetual"}]
    engine, _client, _notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    journal = _with_real_journal(engine, tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(journal, "record_tick", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(run_shutdown(engine, "breach"))

    assert "shutdown_done" in journal._path.read_text()


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
        ledger=None,
        agent_id="acme.scalper_1",
        _notify=_notify,
    )
    # The shared teardown every exit path ends in (ARCH-646).
    stub._reap_client = partial(TickEngine._reap_client, stub)
    stub._finish = partial(TickEngine._finish, stub)

    async def _drive():
        await TickEngine._run_shutdown(stub, "first")
        await TickEngine._run_shutdown(stub, "second")

    asyncio.run(_drive())
    assert calls == ["first"]  # second call is a guarded no-op
    assert stub._running is False
    assert stub._shutting_down is True


# ── stop() racing an emergency winddown (CORR-644) ──


class _RecordingSupervisor:
    def __init__(self):
        self.finals = []

    def unregister(self, agent_id, final_state):
        self.finals.append(final_state)


def _winddown_stub(monkeypatch):
    """A stub engine whose run_shutdown blocks on a gate, plus its supervisor."""
    import condor.agents.engine as engine_module
    from condor.runtime.registry_file import LoopState

    gate = asyncio.Event()
    events = []

    async def fake_run_shutdown(engine, reason):
        events.append(("start", reason))
        await gate.wait()
        events.append(("done", reason))

    monkeypatch.setattr(shutdown_module, "run_shutdown", fake_run_shutdown)
    supervisor = _RecordingSupervisor()
    monkeypatch.setattr(engine_module, "_supervisor", lambda: supervisor)

    async def _notify(msg):
        events.append(("notify", msg))

    stub = SimpleNamespace(
        _shutting_down=False,
        _shutdown_finished=None,
        _running=True,
        _paused=False,
        _task=None,
        _active_client=None,
        journal=None,
        ledger=None,
        config={},
        _last_stop_reason="",
        agent_id="acme.scalper_1",
        _notify=_notify,
    )
    stub._reap_client = partial(TickEngine._reap_client, stub)
    stub._finish = partial(TickEngine._finish, stub)
    return stub, gate, events, supervisor, LoopState


def test_stop_during_a_winddown_lets_it_finish(monkeypatch):
    """A risk-engine winddown runs inside the tick task; stop() must not cancel it."""
    stub, gate, events, supervisor, LoopState = _winddown_stub(monkeypatch)

    async def _drive():
        stub._task = asyncio.create_task(TickEngine._run_shutdown(stub, "risk"))
        await asyncio.sleep(0)
        assert events == [("start", "risk")]

        stop_task = asyncio.create_task(TickEngine.stop(stub))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not stop_task.done()  # waiting on the winddown, not tearing down

        gate.set()
        result = await stop_task
        # stop() returned only after the winddown completed.
        assert ("done", "risk") in events
        await stub._task
        return result

    result = asyncio.run(_drive())
    assert result is False
    assert [e for e in events if e[0] != "notify"] == [
        ("start", "risk"),
        ("done", "risk"),
    ]
    assert not any(e[0] == "notify" for e in events)
    assert stub._task.cancelled() is False
    assert supervisor.finals == [LoopState.STOPPED]
    assert stub._last_stop_reason == "shutdown"


def test_stop_after_a_manual_winddown_waits_for_it(monkeypatch):
    """Manual /shutdown cancels _task first; a concurrent stop() still waits."""
    stub, gate, events, supervisor, LoopState = _winddown_stub(monkeypatch)

    async def _drive():
        stub._task = asyncio.create_task(asyncio.sleep(3600))
        await asyncio.sleep(0)
        manual = asyncio.create_task(TickEngine._run_shutdown(stub, "manual"))
        for _ in range(3):
            await asyncio.sleep(0)
        assert stub._task.cancelled() is True
        assert events == [("start", "manual")]

        stop_task = asyncio.create_task(TickEngine.stop(stub))
        for _ in range(3):
            await asyncio.sleep(0)
        assert not stop_task.done()
        assert supervisor.finals == []

        gate.set()
        result = await stop_task  # must not raise CancelledError
        assert ("done", "manual") in events
        await manual
        return result

    assert asyncio.run(_drive()) is False
    assert supervisor.finals == [LoopState.STOPPED]


def test_plain_stop_still_cancels_the_tick_task(monkeypatch):
    """Regression guard: no winddown in flight, stop() cancels and stops once."""
    stub, _gate, events, supervisor, LoopState = _winddown_stub(monkeypatch)

    async def _drive():
        stub._task = asyncio.create_task(asyncio.sleep(3600))
        await asyncio.sleep(0)
        return await TickEngine.stop(stub)

    assert asyncio.run(_drive()) is True
    assert stub._task.cancelled() is True
    assert supervisor.finals == [LoopState.STOPPED]
    assert stub._last_stop_reason == "user"
    assert events == []


# ── soft-vs-hard drawdown triggers ──


class _FakeTracker:
    def __init__(self, drawdown_pct):
        self._dd = drawdown_pct

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
    from condor.agents.risk import RefusalLog, RiskEngine

    engine, client, notes = _fake_engine(running, positions_seq, monkeypatch, tmp_path)
    engine.agent = SimpleNamespace(slug="acme", tools=[], instructions="You are acme.")
    engine.user_id = 7
    engine.chat_id = 99
    # What the tick's gate reads off the engine (SEC-631).
    engine.risk = RiskEngine()
    engine.journal.get_drawdown_pct = lambda: 0.0
    engine._refusals = RefusalLog()
    engine._last_refusals = []
    engine._agent_key = lambda: "claude-code"
    engine._executor_owners = partial(TickEngine._executor_owners, engine)
    engine._journal_refusals = partial(TickEngine._journal_refusals, engine)
    engine.strategy.home.mkdir(parents=True, exist_ok=True)
    (engine.strategy.home / "shutdown.md").write_text(
        f"---\non_kill_switch: flatten_all\n---\n{body}\n"
    )
    return engine, client, notes


class _FakeLLM:
    """A model client that runs ``script(callback)`` as its one prompt."""

    def __init__(self, permission_callback, script=None):
        self.permission_callback = permission_callback
        self.script = script
        self.prompts: list[str] = []
        self.started = self.stopped = False

    async def start(self):
        self.started = True

    async def prompt(self, text):
        self.prompts.append(text)
        if self.script is not None:
            await self.script(self.permission_callback)
        return "done"

    async def stop(self):
        self.stopped = True


def _patch_llm(monkeypatch, script=None, mounts=None):
    """Stub the mount + client factory the gated builder uses; return the log."""
    import condor.runtime.llm_client as llm_client_module
    import condor.runtime.toolsets as toolsets_module

    built: list[tuple[dict, _FakeLLM]] = []

    def fake_mounts(user_id, chat_id, **kwargs):
        if mounts is not None:
            mounts.append({"user_id": user_id, "chat_id": chat_id, **kwargs})
        return [{"name": "fake"}]

    def fake_build(agent_key, **kwargs):
        llm = _FakeLLM(kwargs.get("permission_callback"), script)
        built.append((kwargs, llm))
        return llm

    monkeypatch.setattr(toolsets_module, "build_mcp_servers_for_session", fake_mounts)
    monkeypatch.setattr(llm_client_module, "build_llm_client", fake_build)
    return built


def test_llm_cleanup_invoked_with_body(tmp_path, monkeypatch):
    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )
    built = _patch_llm(monkeypatch)
    asyncio.run(run_shutdown(engine, "breach"))

    [(kwargs, llm)] = built
    [prompt] = llm.prompts
    assert "[TASK]\nDo cleanup." in prompt
    assert "You are acme." in prompt
    assert llm.started and llm.stopped


def test_llm_cleanup_mounts_the_tick_profile(tmp_path, monkeypatch):
    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )
    mounts: list[dict] = []
    built = _patch_llm(monkeypatch, mounts=mounts)
    asyncio.run(run_shutdown(engine, "breach"))

    [mount] = mounts
    assert mount["tick"] is True
    assert mount["agent_slug"] == "acme"
    assert (mount["user_id"], mount["chat_id"]) == (7, 99)
    [(kwargs, _)] = built
    assert kwargs["permission_callback"] is not None


def test_llm_cleanup_refuses_new_exposure_and_allows_owned_stops(tmp_path, monkeypatch):
    import condor.fetchers.executors as executors_fetcher

    running = [
        {
            "id": "e1",
            "connector": "binance_perpetual",
            "controller_id": "acme.scalper_1",
        }
    ]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )

    async def detail(api, executor_id):
        return {"id": executor_id, "controller_id": "other.session_3"}

    monkeypatch.setattr(executors_fetcher, "get_executor_detail", detail)
    options = [{"kind": "allow_once", "optionId": "allow"}]
    outcomes: dict[str, str] = {}

    async def script(callback):
        calls = {
            "create": {
                "tool": "create_position_executor",
                "input": {"controller_id": "acme.scalper_1", "amount": 1},
            },
            "leverage": {
                "tool": "set_account_position_mode_and_leverage",
                "input": {"leverage": 2},
            },
            "deploy": {
                "tool": "manage_bots",
                "input": {"action": "deploy", "bot_name": "acme-x"},
            },
            "swap": {"tool": "execute_swap", "input": {"amount": 1}},
            "own_stop": {"tool": "stop_executor", "input": {"executor_id": "e1"}},
            "foreign_stop": {
                "tool": "stop_executor",
                "input": {"executor_id": "e_theirs"},
            },
        }
        for name, call in calls.items():
            result = await callback(call, options)
            outcomes[name] = result["outcome"]["outcome"]

    _patch_llm(monkeypatch, script=script)
    asyncio.run(run_shutdown(engine, "breach"))

    assert outcomes == {
        "create": "cancelled",
        "leverage": "cancelled",
        "deploy": "cancelled",
        "swap": "cancelled",
        "own_stop": "selected",
        "foreign_stop": "cancelled",
    }
    blocked = [r for a, r in engine.journal.actions if a == "risk_blocked"]
    assert len(blocked) == 5
    for tool in (
        "create_position_executor",
        "set_account_position_mode_and_leverage",
        "manage_bots",
        "execute_swap",
        "stop_executor",
    ):
        assert any(r.startswith(f"{tool} refused") for r in blocked), tool
    assert engine._refusals.drain() == []


def test_llm_cleanup_never_bypasses_the_risk_gate(tmp_path, monkeypatch):
    from condor.agents import agent_run as agent_run_module

    running = [
        {"id": "e_perp", "connector": "binance_perpetual"},
        {"id": "e_spot", "connector": "binance"},
    ]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )

    async def ungated(**kwargs):
        raise AssertionError("the cleanup must not use the ungated delegation path")

    monkeypatch.setattr(agent_run_module, "run_agent_to_completion", ungated)
    built = _patch_llm(monkeypatch)
    asyncio.run(run_shutdown(engine, "breach"))

    assert dict(client.executors.stop_calls) == {"e_perp": False, "e_spot": False}
    [(_, llm)] = built
    assert len(llm.prompts) == 1
    assert any("complete" in n for n in notes)


def test_llm_cleanup_failure_does_not_block_winddown(tmp_path, monkeypatch):
    import condor.runtime.llm_client as llm_client_module

    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Cleanup."
    )
    _patch_llm(monkeypatch)

    def boom(agent_key, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(llm_client_module, "build_llm_client", boom)
    asyncio.run(run_shutdown(engine, "breach"))
    # The deterministic floor still ran and the winddown completed cleanly.
    assert dict(client.executors.stop_calls) == {"e1": False}
    assert any("complete" in n for n in notes)
    assert not any("🚨" in n for n in notes)


def test_winddown_survives_a_positions_fetch_failure_during_verify(
    tmp_path, monkeypatch
):
    """_verify_and_retry calls the positions fetch unguarded, so it must go
    through the non-strict fetcher: a failed request reads as no positions and
    the winddown still completes instead of propagating ([[ARCH-682]])."""
    running = [{"id": "e_perp", "connector": "binance_perpetual"}]
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)

    async def boom(controller_id=None):
        raise RuntimeError("positions endpoint down")

    monkeypatch.setattr(client.executors, "get_positions_summary", boom)
    asyncio.run(run_shutdown(engine, "test breach"))

    assert ("shutdown_done", "stopped=1, failures=0, verify=flat") in [
        (a, r) for a, r in engine.journal.actions
    ]


# ── winddown reads only the executor list (PERF-641) ──


class _Ledger:
    def bases(self):
        return ["bot_a", "bot_b"]

    def owned(self):
        return ["owned-records"]


def _count_positions_calls(client):
    counter = {"n": 0}
    original = client.executors.get_positions_summary

    async def counted(controller_id=None):
        counter["n"] += 1
        return await original(controller_id=controller_id)

    client.executors.get_positions_summary = counted
    return counter


def test_winddown_only_runs_the_executors_provider(tmp_path, monkeypatch):
    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )
    _patch_llm(monkeypatch)
    positions_calls = _count_positions_calls(client)
    asyncio.run(run_shutdown(engine, "breach"))

    # Baseline + LLM pass; nothing stranded, so no verify retry.
    calls = engine.provider_registry.calls
    assert [c["names"] for c in calls] == [["executors"], ["executors"]]
    assert all(c["bot_names"] is None and c["owned"] is None for c in calls)
    # One positions read from the LLM pass, one from the verify step.
    assert positions_calls["n"] == 2
    assert any("complete" in n for n in notes)


def test_a_stranded_retry_reads_the_executors_provider_again(tmp_path, monkeypatch):
    running = [{"id": "e_perp", "connector": "binance_perpetual"}]
    stuck = [{"connector_name": "binance_perpetual", "trading_pair": "ETH-USDT"}]
    engine, client, notes = _engine_with_llm(
        running, [stuck, stuck, []], tmp_path, monkeypatch, body="Do cleanup."
    )
    _patch_llm(monkeypatch)
    asyncio.run(run_shutdown(engine, "breach"))

    names = [c["names"] for c in engine.provider_registry.calls]
    assert names == [["executors"]] * 3
    assert not any("🚨" in n for n in notes)


def test_winddown_scopes_the_executor_read_to_the_ledger(tmp_path, monkeypatch):
    running = [{"id": "e1", "connector": "binance"}]
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    engine.ledger = _Ledger()
    asyncio.run(run_shutdown(engine, "breach"))

    [call] = engine.provider_registry.calls
    assert call["bot_names"] == ["bot_a", "bot_b"]
    assert call["owned"] == ["owned-records"]


def test_llm_pass_reads_executors_and_positions_concurrently(tmp_path, monkeypatch):
    """Each read waits for the other to start, so a serial pass times out."""
    running = [{"id": "e1", "connector": "binance_perpetual"}]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )
    started: list[str] = []
    gate = asyncio.Event()

    async def _mark(name):
        started.append(name)
        if len(started) == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=2)

    async def executors(engine_, client_):
        await _mark("executors")
        return running

    async def positions(client_, agent_id):
        await _mark("positions")
        return []

    monkeypatch.setattr(shutdown_module, "_get_running_executors", executors)
    monkeypatch.setattr(shutdown_module, "_fetch_positions", positions)
    built = _patch_llm(monkeypatch)
    asyncio.run(
        shutdown_module._run_llm_cleanup(
            engine, client, ShutdownPolicy(), "Do cleanup.", []
        )
    )

    assert sorted(started) == ["executors", "positions"]
    [(_, llm)] = built
    assert len(llm.prompts) == 1
