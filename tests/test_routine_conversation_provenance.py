"""A routine run reports back to the conversation that asked for it (ARCH-089).

A routine an agent starts from chat used to announce itself only on Telegram: the
conversation ended on "I started it" and ``replay_context`` handed the next
session the same incomplete story. What is pinned here is the chain that closes
it — the MCP runner stamps the run with its session, and the finished run writes
one ``system`` turn back — plus the two things that must NOT change: a run with
no conversation behind it stays silent, and a transcript that cannot be written
never costs the run its own delivery.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from condor.routine_store import RoutineStore
from mcp_servers.condor.tools import routines as mcp_routines


@pytest.fixture
def notes(monkeypatch):
    """Capture what the run writes into a transcript."""
    written: list[tuple] = []
    from condor.runtime import conversations

    monkeypatch.setattr(
        conversations,
        "record_system",
        lambda user_id, conv_id, text, kind="": written.append(
            (user_id, conv_id, text, kind)
        ),
    )
    return written


def _routine(run_fn):
    class Config(BaseModel):
        pass

    return SimpleNamespace(
        name="probe", source="global", config_class=Config, run_fn=run_fn
    )


async def _ok(config, ctx):
    return "24 pairs scanned"


async def _boom(config, ctx):
    raise ValueError("no server")


def _store(conversation_id: str, session_key: str = "") -> RoutineStore:
    store = RoutineStore()
    store._instances["i1"] = store._new_instance_meta(
        "probe",
        {},
        "srv",
        7,
        source="mcp",
        conversation_id=conversation_id,
        session_key=session_key,
    )
    return store


@pytest.fixture
def shown(monkeypatch):
    """Capture what the run pushes to an attached surface."""
    pushed: list[dict] = []
    from condor.runtime import wake

    async def deliver(**kwargs):
        pushed.append(kwargs)
        return True

    monkeypatch.setattr(wake, "deliver_note", deliver)
    return pushed


def _run(store: RoutineStore, run_fn) -> dict:
    asyncio.run(
        store._execute_and_record(
            "i1",
            _routine(run_fn),
            {},
            "srv",
            7,
            status_after="completed",
            failed_status="failed",
            fire_hooks=False,
        )
    )
    return store._instances["i1"]


# ── The store: a finished run writes itself back ──


def test_a_run_with_a_conversation_notes_its_outcome_there(notes):
    _run(_store("conv-1"), _ok)

    assert len(notes) == 1
    user_id, conv_id, text, kind = notes[0]
    assert (user_id, conv_id, kind) == (7, "conv-1", "routine")
    assert "probe" in text and "24 pairs scanned" in text


def test_a_run_with_no_conversation_behind_it_stays_silent(notes):
    """The scheduler, the dashboard and the Telegram menu keep working as before."""
    meta = _run(_store(""), _ok)

    assert notes == []
    assert meta["status"] == "completed"


def test_a_failed_run_reports_the_error_the_instance_recorded(notes):
    """One shared helper, so the note and the instance record cannot disagree."""
    meta = _run(_store("conv-1"), _boom)

    assert meta["error"] == "ValueError: no server"
    assert notes[0][2] == "❌ Routine probe failed: ValueError: no server"


def test_a_run_shows_its_outcome_in_the_session_that_started_it(notes, shown):
    """Recording the note is not showing it.

    A dashboard reads the transcript when it loads, so a run that finished while
    the tab was open stayed invisible until the user refreshed the page — which
    is exactly what a fire-and-forget run is for.
    """
    _run(_store("conv-1", "web:7:slot-1"), _ok)

    assert len(shown) == 1
    assert shown[0]["session_key"] == "web:7:slot-1"
    assert shown[0]["conversation_id"] == "conv-1"
    assert shown[0]["kind"] == "routine"
    # One note, two deliveries: what is shown is what is recorded, verbatim.
    assert shown[0]["text"] == notes[0][2]


def test_a_run_with_no_session_behind_it_is_recorded_but_not_pushed(notes, shown):
    """The scheduler and the Telegram menu have no live surface to reach."""
    _run(_store("conv-1"), _ok)

    assert len(notes) == 1
    assert shown == []


def test_a_surface_that_cannot_be_reached_does_not_fail_the_run(notes, monkeypatch):
    """Same rule as the transcript: delivery is best-effort, the run is not."""
    from condor.runtime import wake

    async def explode(**kwargs):
        raise RuntimeError("socket is gone")

    monkeypatch.setattr(wake, "deliver_note", explode)

    meta = _run(_store("conv-1", "web:7:slot-1"), _ok)

    assert meta["status"] == "completed"
    assert meta["last_result"] == "24 pairs scanned"
    assert len(notes) == 1


def test_a_transcript_that_cannot_be_written_does_not_fail_the_run(monkeypatch):
    """A missing note must not cost the user the run's result or its hooks."""
    from condor.runtime import conversations

    def explode(*a, **kw):
        raise RuntimeError("transcript is gone")

    monkeypatch.setattr(conversations, "record_system", explode)

    meta = _run(_store("conv-1"), _ok)

    assert meta["status"] == "completed"
    assert meta["last_result"] == "24 pairs scanned"


# ── The MCP runner: the run carries its session across the process boundary ──


class _API:
    def __init__(self):
        self.posted: dict = {}

    async def __call__(self, method, path, body=None):
        if method == "POST" and path in ("/routines/run", "/routines/start"):
            self.posted = body or {}
            return {"instance_id": "inst-1"}
        return {"instance_id": "inst-1", "status": "completed", "result_text": "ok"}


@pytest.fixture
def api(monkeypatch):
    fake = _API()
    monkeypatch.setattr(mcp_routines, "call_main_api", fake)
    monkeypatch.setattr(mcp_routines.settings, "active_server", "srv")
    return fake


def _stub_routine(monkeypatch, *, continuous: bool):
    class Config(BaseModel):
        pass

    routine = SimpleNamespace(
        name="probe",
        source="global",
        config_class=Config,
        is_continuous=continuous,
    )
    monkeypatch.setattr(mcp_routines, "_resolve_routine", lambda name: routine)
    monkeypatch.setattr(
        mcp_routines, "_resolve_with_owner", lambda name, target: (routine, "condor")
    )


def test_a_one_shot_submitted_from_a_session_carries_it(api, monkeypatch):
    _stub_routine(monkeypatch, continuous=False)
    monkeypatch.setattr(mcp_routines.settings, "session_key", "web:7:slot-1")

    asyncio.run(mcp_routines.run_async_routine("probe", {}))

    assert api.posted["session_key"] == "web:7:slot-1"


def test_a_continuous_start_carries_it_too(api, monkeypatch):
    """The action that most needs it: nothing else reports a continuous run back."""
    _stub_routine(monkeypatch, continuous=True)
    monkeypatch.setattr(mcp_routines.settings, "session_key", "web:7:slot-1")

    asyncio.run(mcp_routines.start_routine("probe", {}))

    assert api.posted["session_key"] == "web:7:slot-1"


def test_a_runner_with_no_session_sends_no_provenance(api, monkeypatch):
    """A tick loop or a delegate worker has no conversation to report to."""
    _stub_routine(monkeypatch, continuous=False)
    monkeypatch.setattr(mcp_routines.settings, "session_key", "")

    asyncio.run(mcp_routines.run_async_routine("probe", {}))

    assert "session_key" not in api.posted


# ── on_complete: a run an agent asked for wakes it instead of noting at it ──


@pytest.fixture
def woken(monkeypatch):
    """Capture the turns a finished run drives into a live session."""
    turns: list[dict] = []
    from condor.runtime import wake

    async def resume(**kwargs):
        turns.append(kwargs)
        return True

    monkeypatch.setattr(wake, "resume_session", resume)
    return turns


def _resuming_store(session_key: str = "web:7:slot-1") -> RoutineStore:
    store = RoutineStore()
    store._instances["i1"] = store._new_instance_meta(
        "probe",
        {},
        "srv",
        7,
        source="mcp",
        conversation_id="conv-1",
        session_key=session_key,
        on_complete="resume",
    )
    return store


def test_a_run_submitted_with_resume_wakes_the_agent_that_asked(woken, shown):
    """The whole point: an agent that ended its turn is handed the outcome.

    Before this it got the same passive note a human gets, which woke nobody —
    so the only way to act on a background run was to poll it.
    """
    _run(_resuming_store(), _ok)

    assert len(woken) == 1
    assert woken[0]["session_key"] == "web:7:slot-1"
    assert woken[0]["conversation_id"] == "conv-1"
    assert woken[0]["kind"] == "resume"
    assert "24 pairs scanned" in woken[0]["text"]
    # Exactly one delivery: the wake already carries the outcome.
    assert shown == []


def test_a_failed_run_wakes_it_too(woken):
    """A failure is what the agent most needs to hear: it can fix the config."""
    _run(_resuming_store(), _boom)

    assert len(woken) == 1
    assert "no server" in woken[0]["text"]


def test_the_wake_tells_the_agent_where_the_full_result_is(woken):
    """The pushed summary is clipped, so the turn must name what to read."""
    _run(_resuming_store(), _ok)

    assert "get_instance" in woken[0]["text"]
    assert "i1" in woken[0]["text"]


def test_a_run_defaults_to_the_delivery_that_spends_nothing(woken, shown):
    """The dashboard, the scheduler and Telegram must not start paying for turns."""
    _run(_store("conv-1", "web:7:slot-1"), _ok)

    assert woken == []
    assert len(shown) == 1


def test_a_wake_with_nobody_listening_falls_back_to_the_note(monkeypatch, shown):
    """A closed tab must not lose an outcome it would previously have been shown."""
    from condor.runtime import wake

    async def nobody(**kwargs):
        return False

    monkeypatch.setattr(wake, "resume_session", nobody)

    _run(_resuming_store(), _ok)

    assert len(shown) == 1
    assert shown[0]["kind"] == "routine"


def test_a_wake_that_raises_does_not_cost_the_run_its_result(monkeypatch, shown):
    """Delivery is best-effort; the run is not."""
    from condor.runtime import wake

    async def explode(**kwargs):
        raise RuntimeError("session is gone")

    monkeypatch.setattr(wake, "resume_session", explode)

    meta = _run(_resuming_store(), _ok)

    assert meta["status"] == "completed"
    assert meta["last_result"] == "24 pairs scanned"
    assert len(shown) == 1


def test_a_run_with_no_session_behind_it_is_never_woken(woken, notes):
    """``resume`` on a scheduled run has nothing to prompt — and must not try."""
    store = RoutineStore()
    store._instances["i1"] = store._new_instance_meta(
        "probe",
        {},
        "srv",
        7,
        source="web",
        conversation_id="conv-1",
        on_complete="resume",
    )
    _run(store, _ok)

    assert woken == []
    assert len(notes) == 1


# ── set_on_complete: a blocking run that ran out of patience ──


def test_a_running_instance_can_be_converted_to_wake_its_caller():
    store = _store("conv-1", "web:7:slot-1")

    assert store.set_on_complete("i1", "resume") == "running"
    assert store._instances["i1"]["on_complete"] == "resume"


def test_a_finished_instance_is_never_converted():
    """Otherwise the caller reads the result AND the agent is woken with it."""
    store = _store("conv-1", "web:7:slot-1")
    store._instances["i1"]["status"] = "completed"

    assert store.set_on_complete("i1", "resume") == "completed"
    assert store._instances["i1"]["on_complete"] == "notify"


def test_converting_an_unknown_instance_reports_nothing_to_convert():
    assert RoutineStore().set_on_complete("nope", "resume") is None


# ── The MCP runner: which delivery each action asks for ──


def test_run_async_asks_to_be_woken(api, monkeypatch):
    _stub_routine(monkeypatch, continuous=False)
    monkeypatch.setattr(mcp_routines.settings, "session_key", "web:7:slot-1")

    out = asyncio.run(mcp_routines.run_async_routine("probe", {}))

    assert api.posted["on_complete"] == "resume"
    assert "END YOUR TURN" in out["note"]


def test_a_blocking_run_reads_its_own_result(api, monkeypatch):
    """It is still waiting, so waking it would deliver the same outcome twice."""
    _stub_routine(monkeypatch, continuous=False)
    monkeypatch.setattr(mcp_routines.settings, "session_key", "web:7:slot-1")

    asyncio.run(mcp_routines.run_routine("probe", {}))

    assert api.posted["on_complete"] == "notify"


class _StuckAPI:
    """A run that never leaves ``running`` — what the blocking budget is for."""

    def __init__(self, applied: bool = True, on_complete: str = "resume"):
        self.applied = applied
        self.on_complete = on_complete
        self.handed_off: dict | None = None

    async def __call__(self, method, path, body=None):
        if method == "POST" and path == "/routines/run":
            return {"instance_id": "inst-1"}
        if method == "POST" and path.endswith("/on_complete"):
            self.handed_off = body or {}
            # The route answers with the *bounded* value, which is not always
            # the one that was asked for.
            return {
                "applied": self.applied,
                "status": "running",
                "on_complete": self.on_complete,
            }
        return {"instance_id": "inst-1", "status": "running"}


def _run_with_no_patience(monkeypatch, api) -> dict:
    _stub_routine(monkeypatch, continuous=False)
    monkeypatch.setattr(mcp_routines, "call_main_api", api)
    monkeypatch.setattr(mcp_routines.settings, "active_server", "srv")
    monkeypatch.setattr(mcp_routines.settings, "session_key", "web:7:slot-1")
    # Give up immediately instead of holding the suite for the real budget.
    monkeypatch.setattr(mcp_routines, "_RUN_BUDGET", 0.0)
    return asyncio.run(mcp_routines.run_routine("probe", {}))


def test_a_blocking_run_that_outlives_its_budget_hands_off_instead_of_polling(
    monkeypatch,
):
    api = _StuckAPI()

    out = _run_with_no_patience(monkeypatch, api)

    assert api.handed_off == {"on_complete": "resume"}
    assert out["started"] is True
    assert out["instance_id"] == "inst-1"
    assert "END YOUR TURN" in out["note"]
    assert "error" not in out


def test_a_hand_off_that_could_not_be_arranged_says_how_to_read_it(monkeypatch):
    """The run finished mid-hand-off, or nothing is listening: read it, once."""
    api = _StuckAPI(applied=False)

    out = _run_with_no_patience(monkeypatch, api)

    assert "get_instance" in out["error"]
    assert out["instance_id"] == "inst-1"


# ── CORR-287: the note reports what the route did, not what was asked for ──


def test_a_hand_off_the_route_downgraded_does_not_promise_a_wake(monkeypatch):
    """``applied`` is true and yet nobody will be woken: say so, and say how to
    read it instead. Promising the wake here strands the run — the agent ends
    its turn and no turn ever comes back."""
    api = _StuckAPI(applied=True, on_complete="notify")

    out = _run_with_no_patience(monkeypatch, api)

    assert out["started"] is True
    assert out["instance_id"] == "inst-1"
    assert "END YOUR TURN" not in out["note"]
    assert "get_instance" in out["note"]


class _DowngradingAPI:
    """A ``/routines/run`` that answers with the bounded ``on_complete``."""

    def __init__(self, on_complete: str = "notify"):
        self.on_complete = on_complete
        self.posted: dict = {}

    async def __call__(self, method, path, body=None):
        if method == "POST" and path == "/routines/run":
            self.posted = body or {}
            return {"instance_id": "inst-1", "on_complete": self.on_complete}
        return {"instance_id": "inst-1", "status": "running"}


def test_run_async_does_not_promise_a_wake_the_route_refused(monkeypatch):
    """It still asks for ``resume``; it just does not lie about the answer."""
    _stub_routine(monkeypatch, continuous=False)
    api = _DowngradingAPI()
    monkeypatch.setattr(mcp_routines, "call_main_api", api)
    monkeypatch.setattr(mcp_routines.settings, "active_server", "srv")
    monkeypatch.setattr(mcp_routines.settings, "session_key", "web:7:slot-1")

    out = asyncio.run(mcp_routines.run_async_routine("probe", {}))

    assert api.posted["on_complete"] == "resume"
    assert out["on_complete"] == "notify"
    assert "END YOUR TURN" not in out["note"]
    assert "get_instance" in out["note"]


class _FakeStore:
    async def execute(self, **kwargs):
        self.kwargs = kwargs
        return "inst-1"


def _run_v2(monkeypatch, *, waking: bool) -> dict:
    """Drive ``POST /routines/run`` with the wake bound on or off."""
    from condor.runtime import wake
    from condor.web import models
    from condor.web.routes import routines as routes

    monkeypatch.setattr(routes, "check_server_access", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "get_routine_store", lambda: _FakeStore())

    async def _conv(session_key: str) -> str:
        return "conv-1"

    monkeypatch.setattr(routes.client, "conversation_for_session", _conv)
    monkeypatch.setattr(wake, "is_waking", lambda conv: waking)

    body = routes.RunRequestV2(
        routine_name="probe",
        server_name="srv",
        session_key="web:7:slot-1",
        on_complete="resume",
    )
    user = models.WebUser(id=7, username="u", first_name="U", role="user")
    return asyncio.run(routes.run_routine_v2(body, user))


def test_the_run_route_reports_the_on_complete_it_actually_applied(monkeypatch):
    """The MCP side phrases its instruction from this field, so it must be the
    bounded value — otherwise the caller is told it will be woken by a run the
    route already downgraded to a passive note."""
    assert _run_v2(monkeypatch, waking=False)["on_complete"] == "resume"
    assert _run_v2(monkeypatch, waking=True)["on_complete"] == "notify"


# ── The route: the same depth-1 bound the delegate route holds ──


def test_a_run_started_from_inside_a_wake_may_not_wake_again(monkeypatch):
    """Otherwise a routine that ends by starting another drives turns forever."""
    from condor.runtime import wake
    from condor.web.routes.routines import _bounded_on_complete

    monkeypatch.setattr(wake, "is_waking", lambda conv: conv == "conv-1")

    assert _bounded_on_complete("resume", "conv-1") == "notify"
    assert _bounded_on_complete("resume", "conv-2") == "resume"
    assert _bounded_on_complete("notify", "conv-2") == "notify"
