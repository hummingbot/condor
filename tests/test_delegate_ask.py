"""``delegate(action="ask")`` -- the blocking half, and why it exists.

Inter-agent communication. ``start`` detaches and reports to the *user*; ``ask``
blocks and returns the answer to the *agent that asked*. Both doors run the same
engine, so what is pinned here is the difference: who waits, what is recorded,
and the two refusals that bound the shape.

The load-bearing fact is the one that motivated the action at all: an unattended
seat has no conversation, so ``on_complete="resume"`` has nothing to wake. That
is asserted directly against ``wake`` rather than described, because it is the
whole reason a blocking door is not simply a worse delegation.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents import agent_run as agent_run_module
from condor.agents import delegate as delegate_module
from condor.agents.delegation_history import list_history
from condor.agents.run_records import KIND_CONSULT


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


# ── why the action exists: resume cannot reach an unattended seat ──


def test_resume_cannot_wake_a_seat_that_has_no_conversation():
    """The premise of the whole action, asserted rather than asserted-about.

    A tick and a delegate worker pass no ``session_key`` (see
    ``toolsets.build_mcp_servers_for_session``), so there is no live session for
    a wake to resolve and it declines. ``notify`` is no help either: it addresses
    the user, not the agent that needed the answer.
    """
    from condor.runtime import wake

    def resume(session_key, conversation_id):
        return asyncio.run(
            wake.resume_session(
                session_key=session_key,
                conversation_id=conversation_id,
                text="the answer",
                kind="delegate",
            )
        )

    # What an unattended seat actually has: no key at all, and no conversation.
    assert resume("", "") is False
    assert resume("tg:42", "") is False
    # Even a well-formed key resolves to nothing when no session is live on it.
    assert resume("tg:42", "conv-1") is False


# ── the answer comes back, and the run is recorded ──


def _ask(monkeypatch, *, answer="funding is 3bps", boom=None, **kw):
    """One ask through the real ``run_ask``, with a stubbed engine."""
    seen: dict = {}

    async def fake_engine(**engine_kw):
        seen.update(engine_kw)
        if boom is not None:
            raise boom
        return answer

    monkeypatch.setattr(agent_run_module, "run_agent_to_completion", fake_engine)

    async def scenario():
        return await agent_run_module.run_ask(
            slug=kw.pop("slug", "scout"),
            user_id=kw.pop("user_id", 7),
            chat_id=kw.pop("chat_id", 42),
            server_name=kw.pop("server_name", "local"),
            task=kw.pop("task", "what is the funding on HYPE right now"),
            **kw,
        )

    return asyncio.run(scenario()), seen


def _only_record(user_id=7):
    records = list_history(user_id=user_id, limit=100)
    assert len(records) == 1, records
    return records[0]


def test_an_ask_returns_the_answer_and_records_the_run(monkeypatch):
    answer, _ = _ask(monkeypatch, caller="condor")

    assert answer == "funding is 3bps"
    record = _only_record()
    # The door is ``ask``; the kind on disk stays "consult" so an install's
    # history lists under one filter rather than splitting across two.
    assert record["kind"] == KIND_CONSULT
    assert record["agent"] == "scout"
    assert record["caller"] == "condor"
    assert record["task"] == "what is the funding on HYPE right now"
    assert record["status"] == "done"
    assert record["result"] == "funding is 3bps"
    # A duration, not a guess: both ends are stamped by the two writes.
    assert record["started_at"] > 0
    assert record["ended_at"] >= record["started_at"]


def test_an_ask_a_person_made_names_no_caller(monkeypatch):
    """ "" means "a person asked", and is never dressed up as an agent."""
    _ask(monkeypatch)
    assert _only_record()["caller"] == ""


def test_a_failing_ask_records_the_error_and_still_raises(monkeypatch):
    with pytest.raises(RuntimeError, match="backend on fire"):
        _ask(monkeypatch, boom=RuntimeError("backend on fire"))

    record = _only_record()
    assert record["status"] == "error"
    assert record["error"] == "backend on fire"
    # Recording an error must not swallow it -- the raise above is the assertion.


def test_a_cancelled_ask_records_stopped_and_still_cancels(monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        _ask(monkeypatch, boom=asyncio.CancelledError())

    assert _only_record()["status"] == "stopped"


def test_a_long_answer_is_clipped_in_the_record_but_not_in_the_reply(monkeypatch):
    huge = "x" * (agent_run_module.MAX_RECORDED_RESULT + 500)
    answer, _ = _ask(monkeypatch, answer=huge)

    assert answer == huge  # the caller gets the whole thing
    recorded = _only_record()["result"]
    assert recorded == "x" * agent_run_module.MAX_RECORDED_RESULT + "\n… (truncated)"


def test_a_broken_record_write_does_not_break_the_ask(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("condor.runtime.registry_file.write_status", boom)

    answer, _ = _ask(monkeypatch)
    assert answer == "funding is 3bps"
    assert list_history(user_id=7, limit=100) == []


def test_the_target_of_an_ask_is_marked_so_it_cannot_ask_onward(monkeypatch):
    """Depth 1: the flag rides to the subprocess, where the guard reads it."""
    _, engine_kw = _ask(monkeypatch)
    assert engine_kw["ask_target"] is True


def test_an_ask_is_unattended_like_every_other_run():
    """No permission callback exists to hand it, so it cannot be re-attended."""
    import inspect

    assert (
        "permission_callback"
        not in inspect.signature(agent_run_module.run_agent_to_completion).parameters
    )


# ── the two refusals, in the subprocess that enforces them ──


def _tool(monkeypatch, **settings_kw):
    """The delegate tool with a stubbed settings seat and no network."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import delegate as delegate_tool

    for key, value in {
        "agent_slug": "scout",
        "ask_target": False,
        "delegate_worker": False,
        "chat_id": 42,
        "user_id": 7,
        "active_server": "",
        "session_key": "",
        **settings_kw,
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)

    called: list = []

    async def fake_api(method, path, body=None, timeout=None):
        called.append((method, path, body, timeout))
        return {"agent": "lp", "answer": "range 0.9-1.1"}

    monkeypatch.setattr(delegate_tool, "call_main_api", fake_api)
    return delegate_tool, called


def test_ask_posts_to_the_ask_route_with_the_caller_stamped(monkeypatch):
    tool, called = _tool(monkeypatch)

    result = asyncio.run(
        tool.delegate(action="ask", agent="lp", task="what range?", context="SOL-USDC")
    )

    assert result["answer"] == "range 0.9-1.1"
    method, path, body, timeout = called[0]
    assert (method, path) == ("POST", "/agents/lp/ask")
    assert body["task"] == "what range?"
    assert body["context"] == "SOL-USDC"
    assert body["caller"] == "scout"
    # Blocking, so the caller's turn is held for at most this long.
    assert timeout == tool.ASK_TIMEOUT_SEC


def test_ask_requires_an_agent_and_a_task(monkeypatch):
    tool, called = _tool(monkeypatch)

    assert "error" in asyncio.run(tool.delegate(action="ask", agent="lp"))
    assert "error" in asyncio.run(tool.delegate(action="ask", task="what range?"))
    assert called == []


def test_an_ask_target_may_not_ask_onward(monkeypatch):
    """Depth 1. A blocked caller waits on this run; it must not nest another."""
    tool, called = _tool(monkeypatch, ask_target=True)

    result = asyncio.run(tool.delegate(action="ask", agent="lp", task="what range?"))

    assert "Nested ask refused" in result["error"]
    assert called == []


def test_an_ask_target_may_still_start_a_detached_delegation(monkeypatch):
    """``start`` leaves nothing holding this turn open, so it stays open."""
    tool, called = _tool(monkeypatch, ask_target=True)

    result = asyncio.run(tool.delegate(action="start", agent="lp", task="go build X"))

    assert "error" not in result
    assert called[0][1] == "/agents/lp/delegate"


def test_asking_yourself_is_refused(monkeypatch):
    tool, called = _tool(monkeypatch, agent_slug="scout")

    result = asyncio.run(tool.delegate(action="ask", agent="scout", task="hmm"))

    assert "Self-ask refused" in result["error"]
    assert called == []


def test_the_unknown_action_error_lists_ask(monkeypatch):
    tool, _ = _tool(monkeypatch)
    assert "ask" in asyncio.run(tool.delegate(action="consult"))["error"]


# ── the route: same two gates delegate carries (SEC-035, SEC-198) ──


def _web_user(uid):
    return SimpleNamespace(id=uid, username="", first_name="", role="user")


def _ask_request(**kw):
    from condor.web.routes.agents import AskRequest

    kw.setdefault("task", "what's my balance?")
    return AskRequest(**kw)


def test_ask_route_denies_a_server_without_access(monkeypatch):
    from fastapi import HTTPException

    from condor.web.routes import agents as agents_module

    called = {"run": False}

    async def _fail(**kw):  # pragma: no cover - must not be reached
        called["run"] = True
        return "should not run"

    monkeypatch.setattr(agent_run_module, "run_ask", _fail)
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager",
        lambda: SimpleNamespace(has_server_access=lambda uid, name: False),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            agents_module.ask_agent(
                "lp", _ask_request(server_name="X", user_id=999), user=_web_user(42)
            )
        )
    assert exc.value.status_code == 403
    assert called["run"] is False  # no MCP client built for X


def test_ask_route_forces_the_caller_user_id(monkeypatch):
    from condor.web.routes import agents as agents_module

    seen = {}

    async def _capture(**kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(agent_run_module, "run_ask", _capture)
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager",
        lambda: SimpleNamespace(has_server_access=lambda uid, name: True),
    )

    # Caller is 42 but tries to impersonate user 999.
    result = asyncio.run(
        agents_module.ask_agent(
            "lp", _ask_request(server_name="X", user_id=999), user=_web_user(42)
        )
    )

    assert result["answer"] == "ok"
    assert seen["user_id"] == 42  # caller's id, not the 999 override
    assert seen["server_name"] == "X"
