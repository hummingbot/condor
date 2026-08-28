"""A delegation speaks to the surface it was started from (CORR-262).

Two halves of one defect, reported from a dashboard-only install. The tool's
``next_steps`` hint is quoted back to the user verbatim, and it unconditionally
named ``/delegations`` in Telegram -- a command that install does not have. And
the completion, once it arrived, was written into the transcript but never
*pushed*, so an already-open dashboard showed nothing until a reload.

Pinned here: the hint branches on the session key's surface and keeps its guard
on both branches, the finished task shows itself in a live session without
paying for a model turn, exactly one of note-push / resume-turn fires, and the
bell entry has somewhere to click.
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegate import start_delegation
from condor.runtime import wake

# ── Half 1: the hint the model reads ──


def _hint(session_key: str) -> str:
    from mcp_servers.condor.tools import delegate as delegate_tool

    return delegate_tool._next_steps(session_key)


TODAYS_TELEGRAM_WORDING = (
    "Running in the background — the user is notified automatically "
    "when it finishes. Tell them they can check progress anytime with "
    "the /delegations command in Telegram. You can poll it yourself "
    'with delegate(action="get", task_id="<id>"). Do NOT invent any '
    "other status command (e.g. there is no /task command)."
)


def test_a_telegram_session_still_gets_todays_wording_verbatim():
    """The surface that *has* /delegations must not drift a single character."""
    assert _hint("tg:42") == TODAYS_TELEGRAM_WORDING


def test_a_web_session_is_never_told_to_use_a_telegram_command():
    hint = _hint("web:7:slot-1")

    assert "/delegations" not in hint
    assert "Telegram" not in hint
    # And it names the surface that actually exists there.
    assert "dashboard" in hint
    assert "Tasks list" in hint


@pytest.mark.parametrize("key", ["", "acp:9:slot-2", "not-a-key"])
def test_an_unknown_or_missing_surface_defaults_to_the_dashboard(key):
    """Only a ``tg:`` seat is known to have the command; everything else is
    told about a UI that exists on every install."""
    assert "/delegations" not in _hint(key)
    assert "dashboard" in _hint(key)


@pytest.mark.parametrize("key", ["tg:42", "web:7:slot-1", ""])
def test_the_do_not_invent_guard_survives_on_every_branch(key):
    """That clause is what the hint existed for -- it is surface-independent."""
    hint = _hint(key)

    assert "Do NOT invent any other status command" in hint
    assert "there is no /task command" in hint
    assert 'delegate(action="get", task_id="<id>")' in hint


@pytest.mark.parametrize("key", ["tg:42", "web:7:slot-1"])
def test_the_resume_addendum_still_appends_on_both_surfaces(key, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import delegate as delegate_tool

    monkeypatch.setattr(settings, "session_key", key)
    monkeypatch.setattr(settings, "agent_slug", "")
    monkeypatch.setattr(settings, "delegate_worker", False)

    async def fake_call(method, path, payload=None):
        return {"task_id": "scout-delegate-1", "status": "running"}

    monkeypatch.setattr(delegate_tool, "call_main_api", fake_call)
    result = asyncio.run(
        delegate_tool.delegate(
            action="start", agent="scout", task="scan pools", on_complete="resume"
        )
    )

    assert result["next_steps"].startswith(_hint(key))
    assert "end your turn now and continue then" in result["next_steps"]


def test_the_hint_the_tool_returns_follows_the_session_key(monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import delegate as delegate_tool

    monkeypatch.setattr(settings, "session_key", "web:7:slot-1")
    monkeypatch.setattr(settings, "agent_slug", "")
    monkeypatch.setattr(settings, "delegate_worker", False)

    async def fake_call(method, path, payload=None):
        return {"task_id": "scout-delegate-1", "status": "running"}

    monkeypatch.setattr(delegate_tool, "call_main_api", fake_call)
    result = asyncio.run(
        delegate_tool.delegate(action="start", agent="scout", task="scan pools")
    )

    assert result["next_steps"] == _hint("web:7:slot-1")


def test_the_tool_docstring_no_longer_asserts_telegram_only_tracking():
    """The docstring is the other thing the model reads before calling."""
    from mcp_servers.condor.server import delegate as delegate_tool_fn

    doc = " ".join((delegate_tool_fn.__doc__ or "").split())
    assert "dashboard" in doc
    assert "The user tracks a delegation in Telegram with the /delegations" not in doc
    assert "Never invent a status command" in doc


# ── Half 2: the finished task shows itself in a live session ──


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, *a, **kw):
        self.messages.append(kw.get("text") or (a[1] if len(a) > 1 else ""))


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


@pytest.fixture
def deliveries(monkeypatch):
    """Capture both wake deliveries, without a runtime behind them."""
    notes: list[dict] = []
    resumes: list[dict] = []

    async def fake_note(**kwargs):
        notes.append(kwargs)
        return True

    async def fake_resume(**kwargs):
        resumes.append(kwargs)
        return True

    monkeypatch.setattr(wake, "deliver_note", fake_note)
    monkeypatch.setattr(wake, "resume_session", fake_resume)
    return notes, resumes


def _agent_root(tmp_path, monkeypatch, slug="scout"):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(
        f"---\nname: {slug}\nwhen_to_consult: always\n---\n\nBody.\n"
    )
    return d


def _answer(text="scan complete: 3 pools"):
    async def fake_run(**kw):
        return text

    return fake_run


def _run_delegation(bot=None, **kwargs):
    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=bot or _FakeBot(),
            **kwargs,
        )
        if dt._task is not None:
            try:
                await dt._task
            except asyncio.CancelledError:
                pass
        return dt

    return asyncio.run(scenario())


def test_a_finished_notify_task_pushes_its_outcome_into_the_live_session(
    tmp_path, monkeypatch, deliveries
):
    notes, resumes = deliveries
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    dt = _run_delegation(conversation_id="conv-1", session_key="web:1:conv-1")

    assert len(notes) == 1
    assert notes[0]["session_key"] == "web:1:conv-1"
    assert notes[0]["conversation_id"] == "conv-1"
    assert notes[0]["kind"] == "delegation"
    # One story everywhere: the push carries the same line as the transcript.
    assert notes[0]["text"] == delegate_module._completion_text(dt)
    # And it stays a note: no model turn was paid for.
    assert resumes == []


def test_a_failed_notify_task_is_shown_too(tmp_path, monkeypatch, deliveries):
    """The thirty-seconds-in failure is precisely what a reload used to hide."""
    notes, _ = deliveries
    _agent_root(tmp_path, monkeypatch)

    async def boom(**kw):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", boom)

    dt = _run_delegation(conversation_id="conv-1", session_key="web:1:conv-1")

    assert dt.status == "error"
    assert len(notes) == 1
    assert notes[0]["text"] == delegate_module._completion_text(dt)


def test_a_resuming_task_is_not_woken_twice(tmp_path, monkeypatch, deliveries):
    """The resume turn already carries the outcome; a note would repeat it."""
    notes, resumes = deliveries
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    _run_delegation(
        conversation_id="conv-1", session_key="web:1:conv-1", on_complete="resume"
    )

    assert len(resumes) == 1
    assert notes == []


def test_a_resume_that_never_happens_still_shows_the_outcome(
    tmp_path, monkeypatch, deliveries
):
    """A failed ``resume`` task hands the agent nothing to continue from, so it
    gets the free note instead -- exactly one delivery either way."""
    notes, resumes = deliveries
    _agent_root(tmp_path, monkeypatch)

    async def boom(**kw):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", boom)

    _run_delegation(
        conversation_id="conv-1", session_key="web:1:conv-1", on_complete="resume"
    )

    assert resumes == []
    assert len(notes) == 1


def test_a_stopped_task_shows_nothing(tmp_path, monkeypatch, deliveries):
    notes, _ = deliveries
    _agent_root(tmp_path, monkeypatch)

    async def hang(**kw):
        await asyncio.sleep(30)
        return "never"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", hang)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=_FakeBot(),
            conversation_id="conv-1",
            session_key="web:1:conv-1",
        )
        await asyncio.sleep(0.05)
        await delegate_module.stop_delegation(dt.task_id)
        try:
            await dt._task
        except asyncio.CancelledError:
            pass
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "stopped"
    assert notes == []


@pytest.mark.parametrize(
    "provenance",
    [{}, {"conversation_id": "conv-1"}, {"session_key": "web:1:conv-1"}],
)
def test_a_delegation_without_provenance_is_a_silent_no_op(
    tmp_path, monkeypatch, deliveries, provenance
):
    """Consult- and tick-started delegations have no conversation to show."""
    notes, _ = deliveries
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    _run_delegation(**provenance)

    assert notes == []


def test_a_push_that_blows_up_costs_the_user_nothing(tmp_path, monkeypatch, deliveries):
    """By then they have been notified and the transcript holds the outcome."""
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    async def boom(**kwargs):
        raise RuntimeError("runtime unreachable")

    monkeypatch.setattr(wake, "deliver_note", boom)
    bot = _FakeBot()

    dt = _run_delegation(bot=bot, conversation_id="conv-1", session_key="web:1:conv-1")

    assert dt.status == "done"
    assert bot.messages == [delegate_module._completion_text(dt)]
