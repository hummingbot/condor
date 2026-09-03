"""A chat turn and a delegation leave the same mark a tick does (FEAT-105).

``prompt_stream`` is the one seam every frontend goes through, and it used to
forward its events and keep nothing: a bot you asked Condor to deploy in the
chat was written to the Hummingbot API and recorded nowhere, which is why the
attribution surfaces reported a dash. The delegate worker was in the same
position with the harder half already done — it folds the calls into a ``tc_map``
and hands it to one consumer.

These tests drive both seams with the events the real clients emit, and assert
on the two files, not on the call.
"""

from __future__ import annotations

import asyncio

import pytest

from condor import paths
from condor.acp.client import PromptDone, TextChunk, ToolCallEvent, ToolCallUpdate
from condor.agents.actions import read_actions
from condor.agents.ownership import read_owned
from condor.runtime.keys import SessionKey
from condor.runtime.sessions import AgentSession

USER = 771


def _deploy_events(bot_name: str = "pmm-king-btcbrl") -> list:
    """One deploy as the adapter really streams it, plus some prose."""
    return [
        TextChunk(text="Deploying now."),
        ToolCallEvent(
            tool_call_id="1",
            title="mcp__mcp-hummingbot__manage_bots",
            status="pending",
            input={},  # the arguments are still streaming (FEAT-102)
        ),
        ToolCallUpdate(
            tool_call_id="1",
            status="completed",
            input={
                "action": "deploy",
                "bot_name": bot_name,
                "controllers_config": ["king-btcbrl-1"],
            },
        ),
        PromptDone(stop_reason="end_turn"),
    ]


class _FakeClient:
    """A chat client that replays a fixed event list."""

    def __init__(self, events: list):
        self._events = events
        self.alive = True
        self.aborted = False

    async def prompt_stream(self, text, images=None):
        for event in self._events:
            await asyncio.sleep(0)
            yield event

    async def abort_prompt(self):
        self.aborted = True


def _session(events: list, *, agent_slug: str = "", conv: str = "conv1", user=USER):
    return AgentSession(
        key=SessionKey(surface="web", owner=str(user or 0), slot="a"),
        agent_key="claude-code",
        client=_FakeClient(events),
        user_id=user,
        agent_slug=agent_slug,
        conversation_id=conv,
    )


async def _drain(session) -> list:
    return [event async for event in session.prompt_stream("go")]


# ── The chat ──


@pytest.mark.asyncio
async def test_a_chat_turn_that_deploys_leaves_a_row_naming_the_bot():
    session = _session(_deploy_events())

    await _drain(session)

    (row,) = read_actions(paths.conversation_dir(USER, "conv1"))
    assert row.verb == "manage_bots:deploy"
    assert "pmm-king-btcbrl" in row.summary
    assert row.ok is True


@pytest.mark.asyncio
async def test_a_chat_turn_that_deploys_takes_ownership_of_the_bot():
    session = _session(_deploy_events("chat-deployed-btc"))

    await _drain(session)

    (owned,) = read_owned(paths.conversation_dir(USER, "conv1"))
    assert owned.base == "chat-deployed-btc"
    assert owned.origin == "deployed"


@pytest.mark.asyncio
async def test_a_bound_specialist_is_credited_and_an_unbound_turn_is_condor():
    """The owner of a chat's work is ``session.agent_slug or "condor"``."""
    import json

    await _drain(_session(_deploy_events(), agent_slug="brigado", conv="bound"))
    await _drain(_session(_deploy_events(), conv="unbound"))

    def namespace(conv: str) -> str:
        path = paths.conversation_dir(USER, conv) / "owned_bots.json"
        return json.loads(path.read_text())["namespace"]

    assert namespace("bound") == "brigado-chat"
    assert namespace("unbound") == "condor-chat"


@pytest.mark.asyncio
async def test_a_turn_that_only_reads_creates_no_file_at_all():
    """The common case, and it has to stay free."""
    session = _session(
        [
            TextChunk(text="Your portfolio is..."),
            ToolCallEvent(
                tool_call_id="1",
                title="get_portfolio_overview",
                status="completed",
                input={"server": "brigado"},
            ),
            PromptDone(stop_reason="end_turn"),
        ]
    )

    await _drain(session)

    assert not paths.conversation_dir(USER, "conv1").exists()


@pytest.mark.asyncio
async def test_the_events_still_reach_the_caller_untouched():
    """The fold reads the stream; it must not consume or reorder it."""
    session = _session(_deploy_events())

    events = await _drain(session)

    assert [type(e).__name__ for e in events] == [
        "TextChunk",
        "ToolCallEvent",
        "ToolCallUpdate",
        "PromptDone",
    ]


@pytest.mark.asyncio
async def test_a_deed_already_done_is_recorded_even_when_the_turn_is_cancelled():
    """A record that only survives the happy path is one you cannot trust."""
    session = _session(_deploy_events())
    session._abort_event.set()

    await _drain(session)

    rows = read_actions(paths.conversation_dir(USER, "conv1"))
    assert [r.verb for r in rows] == ["manage_bots:deploy"]


@pytest.mark.asyncio
async def test_a_turn_with_no_conversation_behind_it_records_nothing():
    """Nothing to hang a record off is a reason to keep none, not to fail."""
    session = _session(_deploy_events(), conv="")

    await _drain(session)  # must not raise

    assert not paths.conversations_dir(USER).exists()


@pytest.mark.asyncio
async def test_two_turns_append_to_one_log():
    session = _session(_deploy_events("first"))
    await _drain(session)
    session.client = _FakeClient(_deploy_events("second"))
    await _drain(session)

    rows = read_actions(paths.conversation_dir(USER, "conv1"))
    assert [r.subject for r in rows] == ["first", "second"]


# ── The delegation ──


@pytest.mark.asyncio
async def test_a_delegation_records_its_deeds_under_its_own_record(monkeypatch):
    from condor.agents import delegate

    dt = delegate.DelegateTask(
        task_id="task-7",
        agent_slug="brigado",
        user_id=USER,
        chat_id=USER,
        server_name=None,
        task="deploy the fleet",
    )
    sink = delegate._make_event_sink(dt)
    for event in _deploy_events("brigado-delegated"):
        if isinstance(event, (ToolCallEvent, ToolCallUpdate)):
            sink(event)

    async def _noop(*_a, **_k):
        return None

    # Drive the runner's completion path with the agent stubbed out. Through
    # ``monkeypatch`` so nothing leaks into the rest of the suite.
    import condor.agents.consult as consult_mod

    monkeypatch.setattr(consult_mod, "_run_agent_to_completion", _noop)
    for name in (
        "_persist_transcript",
        "_record_delegation_status",
        "_record_completion_turn",
        "retire_delegation",
    ):
        monkeypatch.setattr(delegate, name, lambda _dt: None)
    monkeypatch.setattr(delegate, "_notify_done", _noop)
    monkeypatch.setattr(delegate, "_show_completion", _noop)

    await delegate._run(dt, bot=None, timeout_s=5)

    (row,) = read_actions(paths.delegation_dir(USER, "task-7"))
    assert row.verb == "manage_bots:deploy"
    assert "brigado-delegated" in row.summary
    (owned,) = read_owned(paths.delegation_dir(USER, "task-7"))
    assert owned.base == "brigado-delegated"


@pytest.mark.asyncio
async def test_the_sink_feeds_the_task_the_calls_the_log_reads():
    """The map the deeds are written from is the one the sink already folds."""
    from condor.agents import delegate

    dt = delegate.DelegateTask(
        task_id="task-8",
        agent_slug="brigado",
        user_id=USER,
        chat_id=USER,
        server_name=None,
        task="x",
    )
    sink = delegate._make_event_sink(dt)
    for event in _deploy_events("some-bot"):
        if isinstance(event, (ToolCallEvent, ToolCallUpdate)):
            sink(event)

    assert dt.tool_calls["1"]["input"]["bot_name"] == "some-bot"
    assert dt.tool_calls["1"]["status"] == "completed"
