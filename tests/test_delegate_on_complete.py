"""``on_complete`` decides whether a finished delegation wakes its chat (FEAT-034).

The passive half — Telegram push and transcript note — is unchanged and covered
by ``tests/test_delegate.py``. What is pinned here is the new choice: the flag
round-trips into the status file, a ``resume`` task that succeeded hands its
result back to the conversation that asked, and every other outcome does not.
"""

import asyncio

import pytest
from fastapi import HTTPException

from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegate import start_delegation
from condor.runtime import wake
from condor.runtime.registry_file import read_status
from condor.web.models import WebUser
from condor.web.routes.agents import DelegateRequest, delegate_agent

CALLER = WebUser(id=1, role="user")


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, *a, **kw):
        self.messages.append(kw.get("text") or (a[1] if len(a) > 1 else ""))


def _write_agent(root, slug):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(
        f"---\nname: {slug}\nwhen_to_consult: always\n---\n\nBody.\n"
    )
    return d


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


@pytest.fixture
def wakes(monkeypatch):
    """Capture what would have been sent to the runtime, without a session."""
    calls: list[dict] = []

    async def fake_resume(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(wake, "resume_session", fake_resume)
    return calls


def _agent_root(tmp_path, monkeypatch, slug="scout"):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    return _write_agent(tmp_path, slug)


def _answer(text="scan complete: 3 pools"):
    async def fake_run(**kw):
        return text

    return fake_run


async def _drain(dt):
    if dt._task is not None:
        try:
            await dt._task
        except asyncio.CancelledError:
            pass


# ── Slice 1: the flag is carried and persisted ──


def test_on_complete_and_session_key_round_trip_into_the_status_file(
    tmp_path, monkeypatch, wakes
):
    agent_dir = _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

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
            on_complete="resume",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.on_complete == "resume"
    assert dt.session_key == "web:1:conv-1"
    status = read_status(agent_dir / "delegations", f"{dt.task_id}.status.json")
    assert status["on_complete"] == "resume"
    assert status["session_key"] == "web:1:conv-1"


def test_on_complete_defaults_to_notify_and_is_exposed_but_session_key_is_not(
    tmp_path, monkeypatch, wakes
):
    """``to_dict`` is polled straight into a chat agent's context: it may carry
    the one-word intent, never the plumbing."""
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=_FakeBot(),
            session_key="web:1:conv-1",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.to_dict()["on_complete"] == "notify"
    assert "session_key" not in dt.to_dict()


def test_an_unknown_on_complete_degrades_to_notify(tmp_path, monkeypatch, wakes):
    """The edges reject it; a delegation must not fail to *start* over a flag."""
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

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
            on_complete="explode",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.on_complete == "notify"
    assert wakes == []


# ── Slice 2: only a finished "resume" wakes the chat ──


def test_a_finished_resume_task_wakes_its_conversation(tmp_path, monkeypatch, wakes):
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=bot,
            conversation_id="conv-1",
            session_key="web:1:conv-1",
            on_complete="resume",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert len(wakes) == 1
    call = wakes[0]
    assert call["session_key"] == "web:1:conv-1"
    assert call["conversation_id"] == "conv-1"
    assert call["kind"] == "resume"
    # One story, three places: the injected turn is built on the same
    # completion text the push and the transcript note use.
    assert delegate_module._completion_text(dt) in call["text"]
    assert "do not restate the result" in call["text"]
    # The wake is additional, never a replacement: the user still got pinged.
    assert bot.messages == [delegate_module._completion_text(dt)]


def test_the_default_behaves_exactly_as_before(tmp_path, monkeypatch, wakes):
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=bot,
            conversation_id="conv-1",
            session_key="web:1:conv-1",
        )
        await _drain(dt)
        return dt

    asyncio.run(scenario())

    assert wakes == []
    assert len(bot.messages) == 1


def test_a_failed_task_never_resumes(tmp_path, monkeypatch, wakes):
    """The agent would have nothing to continue from, and the error is already
    in the chat and in the transcript."""
    _agent_root(tmp_path, monkeypatch)

    async def boom(**kw):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", boom)

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
            on_complete="resume",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "error"
    assert wakes == []


def test_a_stopped_task_never_resumes(tmp_path, monkeypatch, wakes):
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
            on_complete="resume",
        )
        await asyncio.sleep(0.05)
        await delegate_module.stop_delegation(dt.task_id)
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "stopped"
    assert wakes == []


def test_a_timed_out_task_never_resumes(tmp_path, monkeypatch, wakes):
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
            timeout_s=0,
            conversation_id="conv-1",
            session_key="web:1:conv-1",
            on_complete="resume",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "error"
    assert wakes == []


def test_a_resume_without_provenance_is_a_no_op(tmp_path, monkeypatch, wakes):
    """A consult- or tick-started delegation has no conversation to wake."""
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=_FakeBot(),
            on_complete="resume",
        )
        await _drain(dt)
        return dt

    asyncio.run(scenario())

    assert wakes == []


# ── Slice 6: the depth-1 guard ──


@pytest.fixture
def route(tmp_path, monkeypatch):
    """The delegate route with the runtime lookup stubbed to one conversation."""
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())
    monkeypatch.setattr(wake, "_in_flight", set())

    async def fake_conversation_for_session(session_key):
        return "conv-1" if session_key else ""

    monkeypatch.setattr(
        "condor.web.routes.agents._conversation_for_session",
        fake_conversation_for_session,
    )
    return None


def _post(on_complete="notify", session_key="web:1:conv-1"):
    async def scenario():
        result = await delegate_agent(
            "scout",
            DelegateRequest(
                task="scan pools",
                chat_id=42,
                session_key=session_key,
                on_complete=on_complete,
            ),
            user=CALLER,
        )
        dt = delegate_module.get_delegation(result["task_id"])
        await _drain(dt)
        return dt

    return asyncio.run(scenario())


def test_a_delegation_started_mid_wake_is_forced_back_to_notify(route, wakes):
    """A wake that could start a resuming delegation could keep waking itself."""
    wake._in_flight.add("conv-1")

    dt = _post(on_complete="resume")

    assert dt.on_complete == "notify"
    assert wakes == []


def test_the_same_call_outside_a_wake_keeps_resume(route, wakes):
    dt = _post(on_complete="resume")

    assert dt.on_complete == "resume"
    assert len(wakes) == 1


def test_the_guard_does_not_confuse_conversations(route, wakes):
    """Another conversation being mid-wake says nothing about this one."""
    wake._in_flight.add("some-other-conversation")

    dt = _post(on_complete="resume")

    assert dt.on_complete == "resume"


def test_the_route_rejects_an_unknown_on_complete(route, wakes):
    with pytest.raises(HTTPException) as excinfo:
        _post(on_complete="explode")

    assert excinfo.value.status_code == 400
    assert "on_complete" in str(excinfo.value.detail)


def test_a_wake_that_blows_up_does_not_cost_the_notification(
    tmp_path, monkeypatch, wakes
):
    _agent_root(tmp_path, monkeypatch)
    monkeypatch.setattr(consult_module, "_run_agent_to_completion", _answer())

    async def boom(**kwargs):
        raise RuntimeError("runtime unreachable")

    monkeypatch.setattr(wake, "resume_session", boom)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan pools",
            bot=bot,
            conversation_id="conv-1",
            session_key="web:1:conv-1",
            on_complete="resume",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "done"
    assert len(bot.messages) == 1
