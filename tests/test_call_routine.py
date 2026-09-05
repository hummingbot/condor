"""Routines compose: one calls another and gets its result back (FEAT-052).

The properties pinned here are the ones that make a nested call safe to reach
for. A nested run is an *implementation detail of the caller*: it must not
appear in the dock, fire a hook or message the user, because the user asked for
one thing and would receive N. And it must not clobber the caller's report
attribution — the reason the inner routine runs in its own Task rather than as a
bare awaited coroutine (the isolation FEAT-047 measured for stdout capture).

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio

import pytest
from pydantic import BaseModel, Field

import condor.reports as reports
from condor import primitives, routine_store
from condor.reports import ReportBuilder, rendering
from condor.routine_store import RoutineStore, WebRoutineContext
from routines.base import RoutineInfo, RoutineResult


class _Config(BaseModel):
    """A test routine."""

    value: str = Field(default="ok", description="What to echo back")


def _routine(name, run_fn, *, continuous=False, config_class=_Config, source="global"):
    return RoutineInfo(
        name=name,
        config_class=config_class,
        run_fn=run_fn,
        is_continuous=continuous,
        source=source,
    )


@pytest.fixture
def library(monkeypatch):
    """A routine library the tests own, resolved exactly as production resolves.

    ``_resolve_routine`` reaches the general library through the ``get_routine``
    name bound in ``routine_store``, so patching that puts the fakes on the real
    resolution path instead of stubbing the resolver itself.
    """
    registry: dict[str, RoutineInfo] = {}
    monkeypatch.setattr(routine_store, "get_routine", registry.get)

    def add(name, run_fn, **kw):
        registry[name] = _routine(name, run_fn, **kw)
        return registry[name]

    return add


@pytest.fixture
def store(monkeypatch):
    """A fresh RoutineStore, so dock assertions cannot see another test's runs."""
    fresh = RoutineStore()
    monkeypatch.setattr(routine_store, "_store", fresh)
    return fresh


@pytest.fixture
def no_side_effects(monkeypatch, store):
    """Record whether the dock's side effects fire; a nested call must fire none."""
    fired = {"hooks": 0, "reported": 0}

    async def _hooks(self, *a, **kw):
        fired["hooks"] += 1

    async def _report(self, *a, **kw):
        fired["reported"] += 1

    monkeypatch.setattr(RoutineStore, "_fire_hooks", _hooks)
    monkeypatch.setattr(RoutineStore, "_report_run", _report)
    return fired


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "CHARTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(
        reports, "INDEX_FILE", tmp_path / "reports" / "reports_index.json"
    )
    monkeypatch.setattr(
        rendering,
        "plotly_bundle",
        lambda: "/**\n* plotly.js v0.0.0\n*/\nwindow.Plotly={};",
    )
    return tmp_path / "reports"


async def _save_report(title: str) -> str:
    return await ReportBuilder(title).markdown("hi").save()


# ── the happy path ──


def test_a_nested_call_returns_the_inner_result(library):
    async def inner(config, context):
        return f"inner says {config.value}"

    library("inner", inner)

    result = asyncio.run(primitives.call_routine("inner", {"value": "hello"}))

    assert isinstance(result, RoutineResult)
    assert result.text == "inner says hello"
    assert result.report_id == ""


def test_a_rich_result_survives_the_call_unwrapped(library):
    async def inner(config, context):
        return RoutineResult(text="t", table_columns=["a"], table_data=[{"a": 1}])

    library("inner", inner)

    result = asyncio.run(primitives.call_routine("inner"))

    assert result.table_data == [{"a": 1}]


def test_defaults_apply_when_no_config_is_passed(library):
    seen = {}

    async def inner(config, context):
        seen["value"] = config.value
        return "done"

    library("inner", inner)
    asyncio.run(primitives.call_routine("inner"))

    assert seen["value"] == "ok"


def test_an_agent_prefixed_name_resolves(library, monkeypatch):
    async def inner(config, context):
        return "agent routine"

    registry = {"slug/inner": _routine("slug/inner", inner, source="agent:slug")}
    monkeypatch.setattr(routine_store, "get_routine", registry.get)

    assert asyncio.run(primitives.call_routine("slug/inner")).text == "agent routine"


# ── a nested call is invisible ──


def test_a_nested_call_leaves_no_instance_no_hook_and_no_message(
    library, store, no_side_effects
):
    async def inner(config, context):
        return "quiet"

    library("inner", inner)

    asyncio.run(primitives.call_routine("inner"))

    assert store.list_instances() == []
    assert no_side_effects == {"hooks": 0, "reported": 0}


# ── report attribution ──


def test_a_nested_report_does_not_clobber_the_callers_report_id(
    library, reports_dir, store
):
    """The regression this whole design exists to prevent."""

    async def inner(config, context):
        await _save_report("inner report")
        return "inner done"

    library("inner", inner)

    async def caller():
        outer_id = await _save_report("outer report")
        result = await primitives.call_routine("inner")
        return outer_id, result, reports.get_last_report_id()

    outer_id, result, still_last = asyncio.run(caller())

    assert still_last == outer_id, "the nested run overwrote the caller's report id"
    assert result.report_id and result.report_id != outer_id
    assert reports.get_report(result.report_id)["title"] == "inner report"


def test_a_nested_run_inherits_the_callers_agent(library, reports_dir, store):
    """ARCH-217: the inner report lands in the dock the enclosing run does.

    ``_agent_of`` answers ``"condor"`` for every shared routine, so hardcoding it
    filed an agent's ``call_routine("portfolio_snapshot")`` under Condor while
    the enclosing snippet was stamped with the agent — and ``list_reports``
    matches ``agent`` exactly, so the dock never showed it.
    """

    async def inner(config, context):
        await _save_report("inner report")
        return "done"

    library("inner", inner)

    async def caller():
        with reports.attribute_to("market_making_expert"):
            return await primitives.call_routine("inner")

    result = asyncio.run(caller())

    assert reports.get_report(result.report_id)["agent"] == "market_making_expert"


def test_an_explicit_agent_beats_the_ambient_one(library, reports_dir, store):
    async def inner(config, context):
        await _save_report("inner report")
        return "done"

    library("inner", inner)

    async def caller():
        with reports.attribute_to("market_making_expert"):
            return await primitives.call_routine("inner", agent="scout")

    result = asyncio.run(caller())

    assert reports.get_report(result.report_id)["agent"] == "scout"


def test_an_unattributed_caller_falls_back_to_the_routines_own_owner(
    library, reports_dir, store
):
    async def inner(config, context):
        await _save_report("inner report")
        return "done"

    library("inner", inner, source="agent:scout")

    result = asyncio.run(primitives.call_routine("inner"))

    assert reports.get_report(result.report_id)["agent"] == "scout"


def test_the_nested_run_leaves_the_callers_attribution_alone(
    library, reports_dir, store
):
    async def inner(config, context):
        await _save_report("inner report")
        return "done"

    library("inner", inner, source="agent:scout")

    async def caller():
        with reports.attribute_to("market_making_expert"):
            await primitives.call_routine("inner", agent="scout")
            return await _save_report("outer report")

    outer_id = asyncio.run(caller())

    assert reports.get_report(outer_id)["agent"] == "market_making_expert"


def test_a_nested_report_is_filed_under_the_inner_routine(library, reports_dir, store):
    async def inner(config, context):
        await _save_report("inner report")
        return "done"

    library("inner", inner)

    result = asyncio.run(primitives.call_routine("inner"))
    entry = reports.get_report(result.report_id)

    assert entry["source_type"] == "routine"
    assert entry["source_name"] == "inner"


def test_gather_runs_both_and_keeps_their_attribution_apart(
    library, reports_dir, store
):
    started = []

    async def make(name):
        async def inner(config, context):
            started.append(name)
            await asyncio.sleep(0)  # force interleaving
            await _save_report(f"{name} report")
            return name

        library(name, inner)

    async def caller():
        await make("alpha")
        await make("beta")
        return await asyncio.gather(
            primitives.call_routine("alpha"),
            primitives.call_routine("beta"),
        )

    alpha, beta = asyncio.run(caller())

    assert {alpha.text, beta.text} == {"alpha", "beta"}
    assert alpha.report_id and beta.report_id and alpha.report_id != beta.report_id
    assert reports.get_report(alpha.report_id)["source_name"] == "alpha"
    assert reports.get_report(beta.report_id)["source_name"] == "beta"


# ── context inheritance ──


def test_the_nested_run_inherits_the_callers_context(library, store):
    seen = {}

    async def inner(config, context):
        seen["ctx"] = context
        return "done"

    library("inner", inner)
    ctx = WebRoutineContext("some-server", bot=None, chat_id=99)

    async def caller():
        with primitives.bind_context(ctx):
            await primitives.call_routine("inner")

    asyncio.run(caller())

    assert seen["ctx"] is ctx
    assert seen["ctx"].server_name == "some-server"


def test_an_explicit_context_wins_over_the_inherited_one(library, store):
    seen = {}

    async def inner(config, context):
        seen["ctx"] = context
        return "done"

    library("inner", inner)
    inherited = WebRoutineContext("a", bot=None, chat_id=1)
    explicit = WebRoutineContext("b", bot=None, chat_id=2)

    async def caller():
        with primitives.bind_context(inherited):
            await primitives.call_routine("inner", context=explicit)

    asyncio.run(caller())

    assert seen["ctx"] is explicit


def test_a_call_with_no_context_at_all_still_runs(library, store):
    async def inner(config, context):
        return f"server={context.server_name!r}"

    library("inner", inner)

    assert asyncio.run(primitives.call_routine("inner")).text == "server=''"


# ── refusals ──


def test_a_continuous_routine_is_refused_with_a_pointer_to_start_routine(library):
    async def forever(config, context):  # pragma: no cover - never runs
        await asyncio.sleep(3600)

    library("monitor", forever, continuous=True)

    with pytest.raises(ValueError) as e:
        asyncio.run(primitives.call_routine("monitor"))

    assert "continuous" in str(e.value)
    assert "start_routine('monitor')" in str(e.value)


def test_a_direct_cycle_is_refused_with_the_chain_spelled_out(library):
    async def a(config, context):
        return await primitives.call_routine("a")

    library("a", a)

    with pytest.raises(RuntimeError) as e:
        asyncio.run(primitives.call_routine("a"))

    assert "a → a" in str(e.value)
    assert "cannot call itself" in str(e.value)


def test_an_indirect_cycle_is_refused(library):
    async def a(config, context):
        return await primitives.call_routine("b")

    async def b(config, context):
        return await primitives.call_routine("a")

    library("a", a)
    library("b", b)

    with pytest.raises(RuntimeError) as e:
        asyncio.run(primitives.call_routine("a"))

    assert "a → b → a" in str(e.value)


def test_a_chain_deeper_than_three_is_refused_naming_the_chain(library):
    for name, nxt in (("a", "b"), ("b", "c"), ("c", "d")):

        async def step(config, context, nxt=nxt):
            return await primitives.call_routine(nxt)

        library(name, step)

    async def d(config, context):  # pragma: no cover - never reached
        return "deep"

    library("d", d)

    with pytest.raises(RuntimeError) as e:
        asyncio.run(primitives.call_routine("a"))

    assert f"depth {primitives._MAX_DEPTH} exceeded" in str(e.value)
    assert "a → b → c → d" in str(e.value)


def test_a_chain_exactly_at_the_limit_is_allowed(library):
    for name, nxt in (("a", "b"), ("b", "c")):

        async def step(config, context, nxt=nxt):
            return await primitives.call_routine(nxt)

        library(name, step)

    async def c(config, context):
        return "bottom"

    library("c", c)

    assert asyncio.run(primitives.call_routine("a")).text == "bottom"


def test_siblings_in_a_gather_do_not_see_each_others_stack(library):
    async def leaf(config, context):
        return "leaf"

    async def branch(config, context):
        return (await primitives.call_routine("leaf")).text

    library("leaf", leaf)
    library("branch", branch)

    async def caller():
        return await asyncio.gather(
            primitives.call_routine("branch"),
            primitives.call_routine("branch"),
        )

    left, right = asyncio.run(caller())

    assert left.text == right.text == "leaf"


def test_the_stack_is_empty_again_after_a_call_returns(library):
    async def inner(config, context):
        return "done"

    library("inner", inner)

    async def caller():
        await primitives.call_routine("inner")
        await primitives.call_routine("inner")  # not a cycle: the first one ended
        return primitives._STACK.get()

    assert asyncio.run(caller()) == ()


def test_an_unknown_routine_names_the_near_misses(library):
    async def inner(config, context):  # pragma: no cover - never runs
        return ""

    library("portfolio_snapshot", inner)

    with pytest.raises(ValueError) as e:
        asyncio.run(primitives.call_routine("portfolio_snapshoot"))

    assert "Unknown ref" in str(e.value)


def test_an_invalid_config_names_the_fields_the_routine_accepts(library):
    class Strict(BaseModel):
        """Strict config."""

        model_config = {"extra": "forbid"}
        depth: int = 5

    async def inner(config, context):  # pragma: no cover - never runs
        return ""

    library("inner", inner, config_class=Strict)

    with pytest.raises(ValueError) as e:
        asyncio.run(primitives.call_routine("inner", {"deth": 5}))

    assert "Invalid config for routine 'inner'" in str(e.value)
    assert "Fields: depth" in str(e.value)
    assert 'describe("routine:inner")' in str(e.value)


def test_a_failing_routine_raises_with_its_name_and_keeps_the_cause(library):
    async def inner(config, context):
        raise KeyError("missing_field")

    library("inner", inner)

    with pytest.raises(RuntimeError) as e:
        asyncio.run(primitives.call_routine("inner"))

    assert "Routine 'inner' failed: KeyError" in str(e.value)
    assert isinstance(e.value.__cause__, KeyError)


def test_a_slow_routine_times_out_pointing_at_the_two_valves(library):
    async def inner(config, context):
        await asyncio.sleep(5)
        return "never"  # pragma: no cover

    library("inner", inner)

    with pytest.raises(TimeoutError) as e:
        asyncio.run(primitives.call_routine("inner", timeout=0.05))

    assert "did not finish within 0.05s" in str(e.value)
    assert "start_routine()" in str(e.value)


# ── start_routine: the full-fat path, unchanged ──


def test_start_routine_returns_an_instance_id_readable_through_get_instance(
    library, store
):
    async def inner(config, context):
        return f"backgrounded {config.value}"

    library("inner", inner)

    async def caller():
        instance_id = await primitives.start_routine("inner", {"value": "yes"})
        for _ in range(200):
            await asyncio.sleep(0.01)
            meta = store.get_instance(instance_id)
            if meta and meta["status"] != "running":
                return instance_id, meta
        raise AssertionError("the backgrounded run never finished")

    instance_id, meta = asyncio.run(caller())

    assert instance_id
    assert meta["instance_id"] == instance_id
    assert meta["routine_name"] == "inner"
    assert meta["result_text"] == "backgrounded yes"


def test_start_routine_runs_a_continuous_routine_as_a_background_task(library, store):
    ticks = []

    async def forever(config, context):
        while True:
            ticks.append(1)
            await asyncio.sleep(0.01)

    library("monitor", forever, continuous=True)

    async def caller():
        instance_id = await primitives.start_routine("monitor")
        await asyncio.sleep(0.05)
        meta = store.get_instance(instance_id)
        store.stop(instance_id)
        return meta

    meta = asyncio.run(caller())

    assert meta["status"] == "running"
    assert ticks


def test_start_routine_validates_config_before_backgrounding_anything(library, store):
    class Strict(BaseModel):
        """Strict config."""

        model_config = {"extra": "forbid"}
        depth: int = 5

    async def inner(config, context):  # pragma: no cover - never runs
        return ""

    library("inner", inner, config_class=Strict)

    with pytest.raises(ValueError):
        asyncio.run(primitives.start_routine("inner", {"nope": 1}))

    assert store.list_instances() == []


# ── the Telegram runner's context (CORR-205) ──


class _PTBContext:
    """The shape ``handlers.routines._execute_routine`` hands a routine.

    The delivery chat and the owner differ in a group, and the server lives in
    the owner's preferences rather than in a ``server_name`` attribute.
    """

    def __init__(self, chat_id, owner_id, server):
        from condor.preferences import SERVER_PIN_KEY, USER_PREFERENCES_KEY

        self._chat_id = chat_id
        self._owner_id = owner_id
        self._user_data = {
            USER_PREFERENCES_KEY: {"general": {"active_server": server}},
            SERVER_PIN_KEY: True,
        }
        self.bot = None

    @property
    def user_data(self):
        return self._user_data


def test_a_nested_call_inherits_the_telegram_runners_own_context(library, store):
    seen = {}

    async def inner(config, context):
        seen["ctx"] = context
        return "done"

    library("inner", inner)
    ptb = _PTBContext(chat_id=-100, owner_id=42, server="prod")

    async def caller():
        with primitives.bind_context(ptb):
            await primitives.call_routine("inner")

    asyncio.run(caller())

    assert seen["ctx"] is ptb


def test_a_nested_call_in_a_group_is_owned_by_the_starter_not_the_group(
    library, store, reports_dir
):
    async def inner(config, context):
        return await _save_report("nested")

    library("inner", inner)
    ptb = _PTBContext(chat_id=-100, owner_id=42, server="prod")

    async def caller():
        with primitives.bind_context(ptb):
            return await primitives.call_routine("inner")

    result = asyncio.run(caller())

    assert reports.get_report(result.report_id)["user_id"] == 42


def test_start_routine_forwards_the_telegram_owner_and_resolved_server(
    library, store, monkeypatch
):
    forwarded = {}

    async def _execute(self, name, config, server, user_id, agent=""):
        forwarded.update(name=name, server=server, user_id=user_id)
        return "inst-1"

    monkeypatch.setattr(RoutineStore, "execute", _execute)

    async def inner(config, context):  # pragma: no cover - never runs
        return ""

    library("inner", inner)
    ptb = _PTBContext(chat_id=-100, owner_id=42, server="prod")

    async def caller():
        with primitives.bind_context(ptb):
            return await primitives.start_routine("inner")

    assert asyncio.run(caller()) == "inst-1"
    assert forwarded == {"name": "inner", "server": "prod", "user_id": 42}


def test_start_routine_still_reads_a_web_contexts_server_name(
    library, store, monkeypatch
):
    forwarded = {}

    async def _execute(self, name, config, server, user_id, agent=""):
        forwarded.update(server=server, user_id=user_id)
        return "inst-2"

    monkeypatch.setattr(RoutineStore, "execute", _execute)

    async def inner(config, context):  # pragma: no cover - never runs
        return ""

    library("inner", inner)

    async def caller():
        with primitives.bind_context(WebRoutineContext("web", bot=None, chat_id=7)):
            return await primitives.start_routine("inner")

    asyncio.run(caller())

    assert forwarded == {"server": "web", "user_id": 7}
