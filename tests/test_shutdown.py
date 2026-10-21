"""Unit tests for the emergency-shutdown feature (FEAT-007).

Covers the declarative ``shutdown.md`` policy loader and its strategy → agent →
default resolution, the deterministic winddown (policy → keep_position mapping,
verify/alert on residual), and the engine wrapper's idempotency guard.
"""

import asyncio
import inspect
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
    cancel_resting_orders,
    claimed_order_ids,
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


#: The server this session resolved, asserted on the winddown seat's mount.
SHUTDOWN_SERVER = "the-fleet-this-session-is-on"


def _engine_with_llm(running, positions_seq, tmp_path, monkeypatch, body):
    from condor.agents.risk import RefusalLog, RiskEngine

    engine, client, notes = _fake_engine(running, positions_seq, monkeypatch, tmp_path)
    engine.agent = SimpleNamespace(slug="acme", tools=[], instructions="You are acme.")
    engine.user_id = 7
    engine.chat_id = 99
    # The run's one server answer, which ``build_gated_client`` now reads off the
    # engine rather than re-deriving from ``chat_id`` (CORR-737) — so the LLM
    # cleanup pass acts on the fleet the deterministic floor just acted on.
    engine.server_pinned = False
    engine._resolve_server = lambda: (SHUTDOWN_SERVER, {"host": "127.0.0.1"})
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
    # The winddown seat's trading tools act on the server the run resolved, not
    # on the one chat 99's ambient default would name (CORR-737). Pinned, because
    # a name chosen for this run must stick rather than quietly become another.
    assert mount["server_name"] == SHUTDOWN_SERVER
    assert mount["server_pinned"] is True
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


# ── stop fan-out (PERF-668) ──


def _gate_stops_in_batches(client, batch):
    """Make each fake stop wait until ``batch`` stops of its round have started.

    A serial loop never gets the next stop out, so it deadlocks on the gate and
    the caller's ``asyncio.wait_for`` raises instead.
    """
    api = client.executors

    async def gated_stop(executor_id, keep_position=False):
        api.stop_calls.append((executor_id, keep_position))
        target = -(-len(api.stop_calls) // batch) * batch
        while len(api.stop_calls) < target:
            await asyncio.sleep(0)
        return {"status": "ok"}

    api.stop_executor = gated_stop


def test_baseline_stops_executors_concurrently(tmp_path, monkeypatch):
    running = [
        {"id": "e1", "connector": "binance_perpetual"},
        {"id": "e2", "connector": "binance"},
        {"id": "e3", "connector": "hyperliquid_perpetual"},
    ]
    # Nothing stranded, so the verify step issues no retry stops.
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    _gate_stops_in_batches(client, 3)

    async def _go():
        await asyncio.wait_for(run_shutdown(engine, "test"), timeout=2)

    asyncio.run(_go())

    assert dict(client.executors.stop_calls) == {"e1": False, "e2": True, "e3": False}
    assert ("shutdown_done", "stopped=3, failures=0, verify=flat") in (
        engine.journal.actions
    )


class _StopRejected(Exception):
    def __init__(self, status, message):
        super().__init__(f"{status}, message={message!r}, url=http://internal:8000")
        self.status = status
        self.message = message


def test_one_failed_stop_is_reported_in_running_order(tmp_path, monkeypatch):
    running = [
        {"id": "e1", "connector": "binance_perpetual"},
        {"id": "e2", "connector": "binance_perpetual"},
        {"id": "e3", "connector": "binance_perpetual"},
    ]
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    api = client.executors

    async def stop(executor_id, keep_position=False):
        api.stop_calls.append((executor_id, keep_position))
        if executor_id == "e2":
            raise _StopRejected(409, "executor already stopping")
        return {"status": "ok"}

    api.stop_executor = stop
    stopped, failures = asyncio.run(
        shutdown_module._deterministic_baseline(engine, client, ShutdownPolicy())
    )

    assert failures == ["stop e2: executor already stopping"]
    assert stopped == len(running) - 1

    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    client.executors.stop_executor = stop
    asyncio.run(run_shutdown(engine, "breach"))
    [note] = notes
    assert "1 winddown error(s): stop e2: executor already stopping" in note
    assert "internal" not in note


def test_retry_pass_stops_executors_concurrently(tmp_path, monkeypatch):
    running = [
        {"id": "e1", "connector": "binance_perpetual"},
        {"id": "e2", "connector": "binance_perpetual"},
        {"id": "e3", "connector": "binance_perpetual"},
    ]
    stuck = [{"connector_name": "binance_perpetual", "trading_pair": "ETH-USDT"}]
    residual = [{"connector_name": "binance_perpetual", "trading_pair": "SOL-USDT"}]
    engine, client, notes = _fake_engine(
        running, [stuck, residual], monkeypatch, tmp_path
    )
    _gate_stops_in_batches(client, 3)

    async def _go():
        await asyncio.wait_for(run_shutdown(engine, "breach"), timeout=2)

    asyncio.run(_go())

    calls = client.executors.stop_calls
    assert len(calls) == 6
    assert sorted(calls[3:]) == [("e1", False), ("e2", False), ("e3", False)]
    assert any("🚨" in n and "SOL-USDT" in n for n in notes)


# ── cancel_open_orders is EXECUTED, not described (CORR-727) ──
#
# The flag used to reach only the LLM context as the sentence
# ``cancel_open_orders=True``; no code path cancelled an order, so a stop that
# reported success could leave resting orders on the venue
# (hummingbot/condor#273). These cover the execution, every way it can fail to
# be carried out, and the context sentence now being derived from the outcome.


class _FakeTradingAPI:
    """The ``client.trading`` half the sweep uses: list active, cancel one."""

    def __init__(self, orders, refuse=(), list_error=None):
        self.orders = list(orders)
        self.refuse = set(refuse)
        self.list_error = list_error
        self.cancel_calls = []
        self.list_calls = 0
        self.list_filters = []

    async def get_active_orders(
        self, limit=50, connector_names=None, trading_pairs=None, **_kw
    ):
        self.list_calls += 1
        self.list_filters.append((connector_names, trading_pairs))
        if self.list_error is not None:
            raise self.list_error
        rows = [
            o
            for o in self.orders
            if (connector_names is None or o["connector_name"] in connector_names)
            and (trading_pairs is None or o["trading_pair"] in trading_pairs)
        ]
        return {"data": rows[: int(limit)]}

    async def cancel_order(self, account_name, connector_name, client_order_id):
        self.cancel_calls.append((account_name, connector_name, client_order_id))
        if client_order_id in self.refuse:
            raise _StopRejected(500, "order already filled")
        return {"message": f"Order cancellation initiated for {client_order_id}"}


def _order(order_id, connector="binance", pair="BTC-USDT", status="OPEN"):
    return {
        "order_id": order_id,
        "account_name": "master_account",
        "connector_name": connector,
        "trading_pair": pair,
        "status": status,
    }


def _ex(
    claims=(),
    *,
    eid="e1",
    etype="position",
    connector="binance",
    pair="BTC-USDT",
    key="orders",
    status="OPEN",
):
    """One executor row of this session, claiming ``claims`` as its own orders.

    The ownership side of the sweep (SEC-739): an order id a ``custom_info``
    order list of this session's executors mentions is this session's, and the
    sweep cancels nothing else in its markets.
    """
    return {
        "id": eid,
        "type": etype,
        "connector": connector,
        "pair": pair,
        "custom_info": {key: [{"order_id": oid, "status": status} for oid in claims]},
    }


def _stop_stub(
    tmp_path, monkeypatch, executors, trading, *, cancel=True, policy="keep_all"
):
    """A ``_finish``-shaped stand-in: the graceful stop path and nothing else."""
    strat = _make_strategy(tmp_path, monkeypatch)
    _write_shutdown_md(strat.home / "shutdown.md", policy, cancel=cancel)
    client = SimpleNamespace(trading=trading)

    async def _get_client():
        return client

    stub = SimpleNamespace(
        agent_id="acme.scalper_1",
        strategy=strat,
        config={"execution_mode": "loop"},
        journal=_FakeJournal(),
        ledger=None,
        _cancel_outcome=None,
        _last_skill_data={"executors": executors},
        _get_client=_get_client,
    )
    stub._cancel_resting_orders = partial(TickEngine._cancel_resting_orders, stub)
    return stub


def test_graceful_stop_cancels_a_resting_order_in_code(tmp_path, monkeypatch):
    """Criterion 1: the cancel is issued by the stop, with no LLM turn at all."""
    trading = _FakeTradingAPI([_order("o1"), _order("o2", pair="ETH-USDT")])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1"])],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    # Only the market this session ran: o2's pair was never its own, and the
    # read itself was narrowed to that one market server-side.
    assert trading.cancel_calls == [("master_account", "binance", "o1")]
    assert trading.list_filters == [(["binance"], ["BTC-USDT"])]
    outcome = stub._cancel_outcome
    assert outcome.carried_out is True
    assert outcome.cancelled == ["binance BTC-USDT o1"]
    assert ("cancel_open_orders", outcome.describe()) in stub.journal.actions


def test_graceful_stop_cancels_nothing_when_the_policy_says_false(
    tmp_path, monkeypatch
):
    """Criterion 2."""
    trading = _FakeTradingAPI([_order("o1")])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1"])],
        trading,
        cancel=False,
    )
    asyncio.run(stub._cancel_resting_orders())

    assert trading.cancel_calls == []
    assert trading.list_calls == 0
    # Not even a read: the policy declined, so there is nothing to find out.
    assert stub._cancel_outcome is None


def test_a_market_with_no_cancel_path_is_recorded_not_reported_done(
    tmp_path, monkeypatch
):
    """Criterion 3: an LP/gateway market has no order to cancel and says so."""
    trading = _FakeTradingAPI([])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(etype="lp", connector="jupiter", pair="SOL-USDC")],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    outcome = stub._cancel_outcome
    assert outcome.requested is True
    assert outcome.carried_out is False
    assert outcome.unsupported == ["jupiter SOL-USDC"]
    assert "NOT carried out" in outcome.describe()
    # Nothing was swept, so nothing was read either.
    assert trading.list_calls == 0


def test_a_controller_mode_session_records_its_bot_as_out_of_reach(
    tmp_path, monkeypatch
):
    """Criterion 3: a bot's orders live in its own instance, not in /trading."""
    trading = _FakeTradingAPI([])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1"])],
        trading,
    )
    stub.ledger = SimpleNamespace(bases=lambda: ["acme-scalper"])
    asyncio.run(stub._cancel_resting_orders())

    outcome = stub._cancel_outcome
    assert "bot acme-scalper (own instance)" in outcome.unsupported
    assert outcome.carried_out is False
    # Nothing is swept: the only orders /trading could reach would be someone
    # else's.
    assert trading.list_calls == 0
    assert trading.cancel_calls == []


def test_a_failed_cancel_leaves_the_stop_standing_and_is_recorded(
    tmp_path, monkeypatch
):
    """Criterion 4: the refusal is an order left behind, not an aborted stop."""
    trading = _FakeTradingAPI([_order("o1"), _order("o2")], refuse={"o1"})
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1", "o2"])],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    outcome = stub._cancel_outcome
    assert outcome.cancelled == ["binance BTC-USDT o2"]
    assert outcome.failed == ["binance BTC-USDT o1: order already filled"]
    assert outcome.carried_out is False
    assert "LEFT RESTING" in outcome.describe()
    # The backend URL the exception stringifies with never reaches the record.
    assert "internal" not in outcome.describe()


def test_a_failed_active_order_read_is_unknown_not_a_clean_sweep(tmp_path, monkeypatch):
    trading = _FakeTradingAPI([_order("o1")], list_error=RuntimeError("boom"))
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1"])],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    outcome = stub._cancel_outcome
    assert outcome.cancelled == []
    assert outcome.carried_out is False
    assert "UNKNOWN" in outcome.describe()


def test_only_resting_statuses_are_cancelled(tmp_path, monkeypatch):
    trading = _FakeTradingAPI(
        [
            _order("o_open", status="OPEN"),
            _order("o_part", status="PARTIALLY_FILLED"),
            _order("o_filled", status="FILLED"),
            _order("o_going", status="PENDING_CANCEL"),
        ]
    )
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o_open", "o_part", "o_filled", "o_going"])],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    assert [c[2] for c in trading.cancel_calls] == ["o_open", "o_part"]


def test_a_dry_run_never_sweeps(tmp_path, monkeypatch):
    trading = _FakeTradingAPI([_order("o1")])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1"])],
        trading,
    )
    stub.config = {"execution_mode": "dry_run"}
    asyncio.run(stub._cancel_resting_orders())

    assert trading.list_calls == 0
    assert stub._cancel_outcome is None


def test_finish_does_not_sweep_again_after_a_winddown(tmp_path, monkeypatch):
    trading = _FakeTradingAPI([_order("o1")])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o1"])],
        trading,
    )
    stub._cancel_outcome = shutdown_module.CancelOutcome(requested=True)
    asyncio.run(stub._cancel_resting_orders())

    assert trading.list_calls == 0
    assert trading.cancel_calls == []


def test_the_winddown_sweeps_and_the_context_reports_what_it_executed(
    tmp_path, monkeypatch
):
    """Criterion 5: the sentence is derived from the outcome, not the flag."""
    running = [_ex(["o1", "o2"])]
    engine, client, notes = _fake_engine(running, [[]], monkeypatch, tmp_path)
    trading = _FakeTradingAPI([_order("o1"), _order("o2")], refuse={"o2"})
    client.trading = trading
    asyncio.run(run_shutdown(engine, "breach"))

    outcome = engine._cancel_outcome
    assert outcome.cancelled == ["binance BTC-USDT o1"]
    assert outcome.failed == ["binance BTC-USDT o2: order already filled"]
    # The journal and the operator both hear what was executed.
    assert ("cancel_open_orders", outcome.describe()) in engine.journal.actions
    assert any("LEFT RESTING" in n for n in notes)

    context = shutdown_module._build_llm_context(
        ShutdownPolicy(), running, [], [], outcome
    )
    assert "cancel_open_orders=true, executed:" in context
    assert "cancel_open_orders=True." not in context


def test_the_context_sentence_of_a_policy_that_declined(tmp_path, monkeypatch):
    context = shutdown_module._build_llm_context(
        ShutdownPolicy(cancel_open_orders=False),
        [],
        [],
        [],
        shutdown_module.CancelOutcome(requested=False),
    )
    assert "cancel_open_orders=false: no order cancel was attempted." in context


def test_the_winddown_still_reads_executors_only_twice_with_the_sweep(
    tmp_path, monkeypatch
):
    """The sweep reuses the baseline's rows -- PERF-641's budget is unchanged."""
    running = [_ex(["o1"])]
    engine, client, notes = _engine_with_llm(
        running, [[]], tmp_path, monkeypatch, body="Do cleanup."
    )
    _patch_llm(monkeypatch)
    client.trading = _FakeTradingAPI([_order("o1")])
    asyncio.run(run_shutdown(engine, "breach"))

    names = [c["names"] for c in engine.provider_registry.calls]
    assert names == [["executors"], ["executors"]]


def test_a_full_order_page_is_not_reported_as_the_whole_book(tmp_path, monkeypatch):
    """A read that fills its page may have left resting orders past it."""
    limit = shutdown_module._ORDER_SCAN_LIMIT
    trading = _FakeTradingAPI([_order(f"o{i}") for i in range(limit + 5)])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex([f"o{i}" for i in range(limit + 5)])],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    outcome = stub._cancel_outcome
    assert len(outcome.cancelled) == limit
    assert outcome.carried_out is False
    assert "not swept" in outcome.unknown


# ── the sweep cancels only what this session OWNS (SEC-739) ──
#
# CORR-727 narrowed the sweep to the ``(connector, pair)`` of this session's
# executors, which is a *scope*: on a server with a second principal on the same
# pair, those markets hold a neighbour's resting orders too, and cancelling them
# is not a brake but a liquidation (the order half of the envelope rule
# ``risk.py`` states and SEC-559 built for executors). The ownership test is the
# order ids this session's own executor records mention.


def test_a_stop_cancels_only_the_orders_its_own_executors_claim(tmp_path, monkeypatch):
    """Criteria 1 and 2: the neighbour's order is left resting, and disclosed."""
    trading = _FakeTradingAPI([_order("o_mine"), _order("o_neighbour")])
    stub = _stop_stub(tmp_path, monkeypatch, [_ex(["o_mine"])], trading)
    asyncio.run(stub._cancel_resting_orders())

    assert trading.cancel_calls == [("master_account", "binance", "o_mine")]
    outcome = stub._cancel_outcome
    assert outcome.cancelled == ["binance BTC-USDT o_mine"]
    # Not swept clean, not ``unsupported`` (the market has a cancel path and was
    # swept), not ``failed`` (no cancel was attempted, so none was refused).
    assert outcome.unattributed == ["binance BTC-USDT o_neighbour"]
    assert outcome.unsupported == []
    assert outcome.failed == []
    assert "LEFT ALONE" in outcome.describe()
    assert "o_neighbour" in outcome.describe()


def test_an_order_claimed_by_another_session_is_not_cancelled(tmp_path, monkeypatch):
    """Criterion 4: the other session's rows are not this stop's scope."""
    trading = _FakeTradingAPI([_order("o_mine"), _order("o_theirs")])
    # The neighbour's executor claims ``o_theirs``; this stop is handed only its
    # own rows, so that claim is not in its claimed set.
    stub = _stop_stub(tmp_path, monkeypatch, [_ex(["o_mine"])], trading)
    asyncio.run(stub._cancel_resting_orders())

    assert [c[2] for c in trading.cancel_calls] == ["o_mine"]
    assert stub._cancel_outcome.unattributed == ["binance BTC-USDT o_theirs"]


def test_a_terminal_record_of_an_order_the_venue_rests_is_still_cancelled(
    tmp_path, monkeypatch
):
    """Criterion 3: #273's own shape, and why ``record_orders`` is not reused.

    The record says the order failed; the venue still shows it resting. It is
    this session's either way, and leaving it would be the bug CORR-727 fixed.
    """
    trading = _FakeTradingAPI([_order("o_stale")])
    stub = _stop_stub(
        tmp_path,
        monkeypatch,
        [_ex(["o_stale"], key="failed_orders", status="FAILED")],
        trading,
    )
    asyncio.run(stub._cancel_resting_orders())

    assert [c[2] for c in trading.cancel_calls] == ["o_stale"]
    assert stub._cancel_outcome.unattributed == []


def test_the_closeout_path_attributes_from_the_rows_it_is_handed():
    """Criterion 6: ``engine=None`` (closeout.close_out_agent) gets the test too."""
    trading = _FakeTradingAPI([_order("o_mine"), _order("o_neighbour")])
    client = SimpleNamespace(trading=trading)
    outcome = asyncio.run(
        cancel_resting_orders(None, client, ShutdownPolicy(), [_ex(["o_mine"])])
    )

    assert [c[2] for c in trading.cancel_calls] == ["o_mine"]
    assert outcome.cancelled == ["binance BTC-USDT o_mine"]
    assert outcome.unattributed == ["binance BTC-USDT o_neighbour"]


def test_claimed_order_ids_unions_every_order_key_at_every_status():
    """Criterion 7: key by key, and indifferent to the status stated."""
    keys = (
        "orders",
        "open_orders",
        "close_orders",
        "active_orders",
        "held_position_orders",
        "failed_orders",
        "canceled_orders",
    )
    assert set(keys) == set(shutdown_module._CLAIM_ORDER_KEYS)
    for key in keys:
        assert claimed_order_ids([_ex(["o1"], key=key)]) == {"o1"}, key
    for status in ("OPEN", "FILLED", "CANCELED", "FAILED", "PENDING_CANCEL", ""):
        assert claimed_order_ids([_ex(["o1"], status=status)]) == {"o1"}, status

    # Every key of one row, unioned; ``client_order_id`` spelt either way; and a
    # row with no ids at all (``held_position_orders`` often carries only a
    # price) contributes nothing rather than raising.
    row = {"custom_info": {key: [{"order_id": f"{key}-1"}] for key in keys}}
    row["custom_info"]["orders"].append({"client_order_id": "alt"})
    row["custom_info"]["held_position_orders"].append({"price": 7.25})
    assert claimed_order_ids([row]) == {f"{key}-1" for key in keys} | {"alt"}

    # Shapes that carry no claim at all.
    assert claimed_order_ids(None) == set()
    assert claimed_order_ids([{}, {"custom_info": None}, "not a row"]) == set()


def test_the_disclosure_counts_every_order_and_names_the_first_few():
    """The line reaches a Telegram notify; a busy neighbour can fill the page."""
    outcome = shutdown_module.CancelOutcome(
        requested=True,
        unattributed=[f"binance BTC-USDT o{i}" for i in range(9)],
    )
    text = outcome.describe()

    assert "9 resting order(s) LEFT ALONE" in text
    assert "o4" in text and "o5" not in text
    assert "+4 more" in text
    # A disclosure, not an incompletion: this session's own orders were swept.
    assert outcome.carried_out is True


def test_the_sweep_filter_itself_tests_the_claimed_ids():
    """Criterion 9: the comment cannot drift from the code again."""
    source = inspect.getsource(shutdown_module.cancel_resting_orders)
    assert "claimed = claimed_order_ids(executors)" in source
    assert "_order_id(o) in claimed" in source

    # And the comment where the trade-off is made states the ownership test
    # rather than implying the market scope is one.
    module_source = inspect.getsource(shutdown_module)
    comment = module_source.split("cancel_open_orders -- executed")[1].split(
        "_NO_CANCEL_PATH_TYPES"
    )[0]
    assert "claimed_order_ids" in comment
