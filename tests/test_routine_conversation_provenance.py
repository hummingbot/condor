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
