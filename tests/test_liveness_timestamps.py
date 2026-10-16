"""Per-session last reading and last successful action (FEAT-137).

GitHub issue [#272](https://github.com/hummingbot/condor/issues/272) cost a race
entry 4h45m because nothing could tell a market maker that was legitimately
quiet from one whose strategy had died. The two quantities that would have said
so are precisely the two nothing recorded:

- ``_last_tick_at`` is stamped at tick **start**, so it advances even when every
  call in the tick failed — a broken loop looks exactly as fresh as a healthy
  one — and it lives in memory, so a restart erases it.
- ``actions.latest_action()`` records the last tool **call**, not its result, so
  a tick spent being refused advances it.

So this pins outcomes, not attempts: a reading that said something, and a
mutating call that worked — persisted through ``record_tick`` so both survive
the process, and rendered beside the bot list as ages.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.actions import (
    EFFECT_VERBS,
    AgentAction,
    actions_from_tool_calls,
    is_effect,
    last_effect_at,
)
from condor.agents.providers.base import ProviderResult
from condor.fetchers.bot_performance import (
    clear_archived_cache,
    clear_history_cache,
    clear_live_names_cache,
    clear_snapshot_cache,
)

_CACHES = (
    clear_snapshot_cache,
    clear_history_cache,
    clear_archived_cache,
    clear_live_names_cache,
)


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    for clear in _CACHES:
        clear()
    yield
    for clear in _CACHES:
        clear()


def _engine(tmp_path, monkeypatch, **config):
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="brigado", name="Grid")
    strategy.home.mkdir(parents=True, exist_ok=True)
    return TickEngine(
        agent=Agent(slug="brigado", name="Brigado"),
        strategy=strategy,
        config={"execution_mode": "loop", **config},
        chat_id=1,
        user_id=1,
    )


def _gather(engine, monkeypatch, data: dict | None):
    """Run the gather phase with the executors provider returning ``data``."""

    async def run_core_providers(*args, **kwargs):
        if data is None:
            return {}
        return {
            "executors": ProviderResult(
                name="executors", data=data, summary="Active Executors: none"
            )
        }

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "_adopt_running_bots", nothing)
    monkeypatch.setattr(engine, "_adopt_running_executors", nothing)
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", run_core_providers
    )
    return asyncio.run(engine._gather_tick_context(object()))


# ── Criterion 1: a reading that said something ──


@pytest.mark.parametrize(
    "reading,stamps",
    [
        ("ok", True),
        ("empty", False),
        ("missing", False),
    ],
)
def test_only_an_ok_reading_advances_last_reading_at(
    tmp_path, monkeypatch, reading, stamps
):
    """``empty`` and ``missing`` are absence, and absence is not a reading.

    The three are the same 0.0 to every numeric field, which is what made a
    stopped strategy score as one sitting flat ([[CORR-725]]). The stamp is the
    thing that tells them apart over time.
    """
    engine = _engine(tmp_path, monkeypatch)
    assert engine._last_reading_at == 0.0

    assert _gather(engine, monkeypatch, {"executors": [], "reading": reading})

    assert (engine._last_reading_at > 0.0) is stamps


def test_a_reading_the_provider_never_reported_leaves_the_stamp_alone(
    tmp_path, monkeypatch
):
    """No ``reading`` key at all is the provider's own error path.

    ``ExecutorsProvider`` returns ``{"error": ...}`` when the fetch raises, so
    the key's absence is itself the statement that nothing was read — it must
    not be read as a reading that happened to be fine.
    """
    engine = _engine(tmp_path, monkeypatch)

    assert _gather(engine, monkeypatch, {"error": "connection refused"})

    assert engine._last_reading_at == 0.0


def test_a_stale_reading_stamp_is_not_rewound_by_a_later_empty_tick(
    tmp_path, monkeypatch
):
    """The stamp is a *last seen*, so a later absence leaves it where it was.

    Rewinding it to 0 would say "never read", which is a different and false
    statement from "last read eleven minutes ago" — and the age is the whole
    diagnosis.
    """
    engine = _engine(tmp_path, monkeypatch)
    _gather(engine, monkeypatch, {"reading": "ok"})
    first = engine._last_reading_at
    assert first > 0.0

    _gather(engine, monkeypatch, {"reading": "empty"})

    assert engine._last_reading_at == first


# ── Criterion 2: a tick that achieved nothing ──


def test_a_tick_whose_every_call_fails_advances_neither_stamp(tmp_path, monkeypatch):
    """Where ``_last_tick_at`` advances today, these two must not.

    This is #272's failure mode in one assertion: the tick ran, the clock the
    dashboard reads moved, and nothing was actually obtained or done.
    """
    engine = _engine(tmp_path, monkeypatch)

    # The tick starts: _last_tick_at is stamped before anything can fail.
    engine._last_tick_at = 1.0

    # The data fetch failed, so the provider reports an error and no reading…
    assert _gather(engine, monkeypatch, {"error": "upstream 503"})
    # …and every tool call the model made was refused or raised.
    failed = [
        {"name": "create_grid_executor", "status": "failed", "output": "insufficient"},
        {"name": "manage_bots", "status": "blocked", "input": {"action": "deploy"}},
    ]
    asyncio.run(
        engine._persist_tick(
            SimpleNamespace(
                risk_state=SimpleNamespace(to_dict=dict),
                core_data_summaries={},
                prompt="p",
                executors_summary="",
            ),
            "the market looks thin",
            failed,
            0.5,
        )
    )

    assert engine._last_tick_at == 1.0, "the attempt stamp is the engine's, untouched"
    assert engine._last_reading_at == 0.0
    assert engine._last_successful_action_at == 0.0


# ── Criterion 3: an effect, not any call that did not raise ──


def _row(verb: str, *, ok: bool = True, at: float = 100.0) -> AgentAction:
    return AgentAction(
        tick=1, at=at, tool=verb.split(":")[0], verb=verb, summary=verb, ok=ok
    )


def test_a_successful_read_does_not_advance_the_action_stamp():
    """Alternative 2 of the design fails here, which is why the set is explicit.

    ``manage_bots:status`` is not even recordable; the recordable-but-inert
    writes are the real trap — a saved controller config or a routine written
    is a successful call that says nothing about whether the strategy trades.
    """
    assert last_effect_at([_row("manage_controllers:upsert")]) == 0.0
    assert last_effect_at([_row("manage_gateway_config:tokens")]) == 0.0
    assert last_effect_at([_row("control_agent:pause")]) == 0.0


@pytest.mark.parametrize(
    "verb",
    [
        "create_order_executor",  # an order placed
        "place_order",  # …and the direct door
        "stop_executor",  # a cancel sent
        "create_grid_executor",  # an executor created
        "create_lp_executor",
        "manage_bots:deploy",  # a bot deployed
        "execute_swap",  # the DEX siblings
        "manage_clmm:open",
        "manage_amm:add_liquidity",
    ],
)
def test_each_named_effect_advances_the_action_stamp(verb):
    assert verb in EFFECT_VERBS
    assert last_effect_at([_row(verb, at=1234.0)]) == 1234.0


def test_a_failed_or_refused_effect_is_an_attempt_not_an_effect():
    """The half ``latest_action()`` gets wrong: it records the call, not its result."""
    assert is_effect(_row("create_grid_executor", ok=True))
    assert not is_effect(_row("create_grid_executor", ok=False))
    assert last_effect_at([_row("manage_bots:deploy", ok=False)]) == 0.0


def test_the_stamp_is_the_latest_effect_of_the_tick_not_the_last_row():
    """Rows within a tick share its stamp, so the rule is a max, not a tail read."""
    rows = [
        _row("create_grid_executor", at=200.0),
        _row("manage_controllers:upsert", at=300.0),
    ]
    assert last_effect_at(rows) == 200.0


def test_the_effect_set_is_read_off_a_real_folded_tool_call():
    """End to end from the stream's own shape, not from a hand-built row.

    ``actions_from_tool_calls`` is what the tick actually calls, so a verb this
    module spells differently from the fold would silently never match.
    """
    actions = actions_from_tool_calls(
        [
            {
                "name": "manage_bots",
                "status": "completed",
                "input": {"action": "deploy", "bot_name": "grid"},
            },
            {"name": "create_grid_executor", "status": "failed", "output": "no funds"},
        ],
        tick=4,
        at=999.0,
    )

    assert [(a.verb, a.ok) for a in actions] == [
        ("manage_bots:deploy", True),
        ("create_grid_executor", False),
    ]
    assert last_effect_at(actions) == 999.0


def test_a_tick_that_deployed_a_bot_advances_the_action_stamp(tmp_path, monkeypatch):
    """The engine's own wiring, not only the pure rule above."""
    engine = _engine(tmp_path, monkeypatch)
    ctx = SimpleNamespace(
        risk_state=SimpleNamespace(to_dict=dict),
        core_data_summaries={},
        prompt="p",
        executors_summary="",
    )

    asyncio.run(
        engine._persist_tick(
            ctx,
            "deployed",
            [
                {
                    "name": "manage_bots",
                    "status": "completed",
                    "input": {"action": "deploy", "bot_name": "grid"},
                }
            ],
            0.5,
        )
    )
    deployed_at = engine._last_successful_action_at
    assert deployed_at > 0.0

    # A later tick that only read leaves it where it was — that gap is the signal.
    asyncio.run(engine._persist_tick(ctx, "watched", [], 0.5))
    assert engine._last_successful_action_at == deployed_at


# ── Criterion 4: both survive the process ──


def test_both_stamps_are_read_back_from_status_json_with_no_live_engine(tmp_path):
    """``record_tick`` persists them; ``read_status`` reads them with no engine.

    The point of persisting through the existing seam rather than adding one:
    a crashed process leaves nothing in memory, and the two facts that say
    whether its strategy was alive are exactly the ones a reader wants next.
    """
    from condor.runtime.loops import LoopSupervisor
    from condor.runtime.registry_file import read_status

    session_dir = tmp_path / "session_3"
    session_dir.mkdir()
    engine = SimpleNamespace(
        agent_id="brigado.grid_3",
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="grid"),
        session_num=3,
        session_dir=session_dir,
        journal=SimpleNamespace(tick_count=11),
        chat_id=1,
        user_id=1,
        config={},
        _last_reading_at=1_700_000_000.0,
        _last_successful_action_at=1_699_999_000.0,
    )

    supervisor = LoopSupervisor()
    supervisor.register(engine)
    supervisor.record_tick(engine)

    # Nothing in memory: a fresh read of the file, as a restarted process makes.
    status = read_status(session_dir)
    assert status is not None
    assert status["last_reading_at"] == 1_700_000_000.0
    assert status["last_successful_action_at"] == 1_699_999_000.0


def test_a_run_that_never_read_or_acted_persists_zero_not_a_missing_key(tmp_path):
    """0.0 is "never", and a reader has to be able to say so rather than guess."""
    from condor.runtime.loops import LoopSupervisor
    from condor.runtime.registry_file import read_status

    session_dir = tmp_path / "session_1"
    session_dir.mkdir()
    engine = SimpleNamespace(
        agent_id="brigado.grid_1",
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="grid"),
        session_num=1,
        session_dir=session_dir,
        journal=None,
        chat_id=1,
        user_id=1,
        config={},
    )

    LoopSupervisor().record(engine, "running")

    status = read_status(session_dir)
    assert status["last_reading_at"] == 0.0
    assert status["last_successful_action_at"] == 0.0


# ── The reading a bot-mode session actually gets ──


def _bot_client(snapshots: list[dict], live: set[str]):
    class _Orch:
        async def get_latest_controller_performance(self, bot_name=None):
            return {"data": snapshots}

        async def get_controller_performance_history(self, *a, **kw):
            return {"data": []}

        async def get_active_bots_status(self):
            return {"data": {n: {} for n in live}}

    class _Archived:
        async def list_databases(self):
            return []

    class _Executors:
        async def search_executors(self, controller_ids, limit, cursor=None):
            return {"executors": []}

    return SimpleNamespace(
        base_url="",
        executors=_Executors(),
        bot_orchestration=_Orch(),
        archived_bots=_Archived(),
    )


def _bot_perf(snapshots, live, bases):
    from condor.agents.performance import fetch_agent_performance

    return asyncio.run(
        fetch_agent_performance(
            _bot_client(snapshots, live), "brigado.grid_1", bot_names=bases
        )
    )


def test_a_bot_reporting_nothing_rolls_up_as_an_empty_reading():
    """#272 exactly: the container is up and the performance object is ``{}``."""
    perf = _bot_perf(
        [{"bot_name": "grid", "controller_id": "c1", "performance": {}}],
        {"grid"},
        ["grid"],
    )
    assert perf.reading == "empty"


def test_a_bot_that_reports_figures_rolls_up_as_ok_even_when_flat():
    """A genuinely flat bot is alive, and must not read as absence ([[CORR-725]])."""
    perf = _bot_perf(
        [
            {
                "bot_name": "grid",
                "controller_id": "c1",
                "performance": {"realized_pnl_quote": 0.0, "volume_traded": 0.0},
            }
        ],
        {"grid"},
        ["grid"],
    )
    assert perf.reading == "ok"


def test_an_owned_base_with_no_instance_at_all_rolls_up_as_missing():
    perf = _bot_perf([], set(), ["grid"])
    assert perf.reading == "missing"
    assert perf.unresolved_bases == ["grid"]


def test_an_executor_only_agent_reads_ok_because_its_table_answered():
    """No bot dimension, so there is no absence to measure.

    Left at ``missing`` an executor-mode session would never stamp a reading and
    would read as dead the moment it was started, which is the false positive
    [[CORR-725]]'s fifth criterion exists to prevent.
    """
    perf = _bot_perf([], set(), [])
    assert perf.reading == "ok"


# ── Criterion 5's wire half: both reach the payloads the UI reads ──


def test_the_engine_reports_both_stamps_in_get_info(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    engine._last_reading_at = 42.0
    engine._last_successful_action_at = 7.0

    info = engine.get_info()

    assert info["last_reading_at"] == 42.0
    assert info["last_successful_action_at"] == 7.0


def test_both_wire_models_carry_the_stamps_and_default_to_never():
    from condor.web.routes.agents import LiveLoopModel, RunningInstance

    instance = RunningInstance(agent_id="a", session_num=1, status="running")
    assert instance.last_reading_at == 0.0
    assert instance.last_successful_action_at == 0.0

    loop = LiveLoopModel(agent_id="a", last_reading_at=5.0)
    assert loop.model_dump()["last_reading_at"] == 5.0
    assert loop.model_dump()["last_successful_action_at"] == 0.0
