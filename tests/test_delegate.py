"""Unit tests for DELEGATE -- fire-and-forget background agent tasks (FEAT-006).

Covers the lifecycle (running -> done/error/stopped), result capture, transcript
persistence, completion notification, and that the runner drives the shared consult
engine with ``permission_callback=None`` (auto-approve).
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegate import (
    get_all_delegations,
    get_delegation,
    start_delegation,
    stop_delegation,
)
from condor.web.models import WebUser

# The events route is ownership-gated (SEC-081), so calling it as a plain
# function needs a caller. Every delegation built below is owned by user 1.
_CALLER = WebUser(id=1, role="user")


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


async def _drain(dt):
    """Await the background task to completion (ignoring cancellation)."""
    if dt._task is not None:
        try:
            await dt._task
        except asyncio.CancelledError:
            pass


def test_delegation_runs_to_done_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    seen = {}

    async def fake_run(*, permission_callback, **kw):
        seen.update(kw)
        seen["permission_callback"] = permission_callback
        return "scan complete: 3 pools"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan SOL pools",
            bot=bot,
        )
        # Returns immediately, still running before we await it.
        assert dt.status == "running"
        assert get_delegation(dt.task_id) is dt
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    # Lifecycle + result capture.
    assert dt.status == "done"
    assert dt.result == "scan complete: 3 pools"
    # Auto-approve: the runner drives consult with NO permission callback.
    assert seen["permission_callback"] is None
    assert seen["task"] == "scan SOL pools"
    # Transcript written under agents/{slug}/delegations/{task_id}.md.
    transcript = tmp_path / "scout" / "delegations" / f"{dt.task_id}.md"
    assert transcript.exists()
    assert "scan complete: 3 pools" in transcript.read_text()
    # Notification delivered.
    assert any("done" in m for m in bot.messages)


def test_delegation_captures_error(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    async def boom(**kw):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", boom)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="do thing",
            bot=bot,
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "error"
    assert "model exploded" in dt.error
    assert any("failed" in m for m in bot.messages)


def test_stop_cancels_running_delegation(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    async def slow(**kw):
        await asyncio.sleep(60)
        return "never"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", slow)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="long task",
            bot=bot,
        )
        await asyncio.sleep(0)  # let the runner start
        assert dt.task_id in get_all_delegations()
        stopped = await stop_delegation(dt.task_id)
        await _drain(dt)
        return dt, stopped

    dt, stopped = asyncio.run(scenario())

    assert stopped is True
    assert dt.status == "stopped"
    # A stopped task does not spam a completion notification.
    assert bot.messages == []


def test_stop_unknown_returns_false():
    assert asyncio.run(stop_delegation("nope-delegate-x")) is False


def test_delegation_carries_conversation_provenance(tmp_path, monkeypatch):
    """The conversation that started the task is stamped on it and on disk.

    This is what lets a chat show *its own* delegations rather than everyone's
    (FEAT-021): two web conversations by the same user produce byte-identical
    delegation metadata otherwise.
    """
    import json

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")
    # The task now writes its outcome back to the conversation (ARCH-087), so
    # keep that write inside tmp_path rather than in the real store.
    _isolate_conversations(tmp_path, monkeypatch)

    async def fake_run(**kw):
        return "ok"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan",
            bot=_FakeBot(),
            conversation_id="conv-abc",
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.conversation_id == "conv-abc"
    assert dt.to_dict()["conversation_id"] == "conv-abc"
    # Elapsed time is computable by a watcher that arrived late.
    assert dt.to_dict()["started_at"] > 0
    # Survives on disk, so a restart-interrupted task keeps its provenance.
    status = json.loads(
        (tmp_path / "scout" / "delegations" / f"{dt.task_id}.status.json").read_text()
    )
    assert status["conversation_id"] == "conv-abc"


def test_delegation_without_conversation_is_empty_not_error():
    """A consult- or tick-started delegation has no conversation; that is honest."""
    from condor.agents.delegate import DelegateTask

    dt = DelegateTask(
        task_id="x",
        agent_slug="scout",
        user_id=1,
        chat_id=42,
        server_name=None,
        task="t",
    )
    assert dt.conversation_id == ""
    assert dt.to_dict()["conversation_id"] == ""


def _isolate_conversations(tmp_path, monkeypatch):
    """Point the conversation store at a throwaway dir and return its reader."""
    from condor.runtime import conversations

    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    return conversations


def _run_delegation(monkeypatch, *, conversation_id, result=None, boom=None):
    """Drive one delegation to completion and return (task, bot)."""

    async def fake_run(**kw):
        if boom is not None:
            raise RuntimeError(boom)
        return result

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan",
            bot=bot,
            conversation_id=conversation_id,
        )
        await _drain(dt)
        return dt

    return asyncio.run(scenario()), bot


def test_completed_delegation_lands_in_its_conversation(tmp_path, monkeypatch):
    """The answer reaches the chat that asked for it, not just Telegram (ARCH-087).

    Without this the conversation ends on "I started a background task" and the
    resumed session replays that same unanswered story.
    """
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path / "agents", "scout")
    conversations = _isolate_conversations(tmp_path, monkeypatch)
    meta = conversations.new_conversation(1, "web")

    dt, bot = _run_delegation(
        monkeypatch, conversation_id=meta.id, result="3 pools worth watching"
    )

    turns = conversations.read_transcript(1, meta.id)
    system = [t for t in turns if t.role == "system"]
    assert len(system) == 1
    assert system[0].kind == "delegation"
    assert "3 pools worth watching" in system[0].text
    # One helper, one story: the transcript line IS the Telegram text.
    assert system[0].text == bot.messages[-1]
    # A system note replays as a parenthetical, not as the agent's own words.
    assert f"({system[0].text})" in conversations.replay_context(1, meta.id)
    assert dt.status == "done"


def test_delegation_without_conversation_records_nothing(tmp_path, monkeypatch):
    """A consult- or tick-started task has no conversation: no write, no raise."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path / "agents", "scout")
    _isolate_conversations(tmp_path, monkeypatch)

    dt, bot = _run_delegation(monkeypatch, conversation_id="", result="ok")

    assert dt.status == "done"
    assert bot.messages  # still notified
    assert not (tmp_path / "conversations").exists()


def test_failed_delegation_records_the_same_failure_it_pushes(tmp_path, monkeypatch):
    """An error is reported to the conversation exactly as it is to the chat."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path / "agents", "scout")
    conversations = _isolate_conversations(tmp_path, monkeypatch)
    meta = conversations.new_conversation(1, "web")

    dt, bot = _run_delegation(
        monkeypatch, conversation_id=meta.id, boom="model exploded"
    )

    assert dt.status == "error"
    system = [
        t for t in conversations.read_transcript(1, meta.id) if t.role == "system"
    ]
    assert len(system) == 1
    assert system[0].kind == "delegation"
    assert "model exploded" in system[0].text
    assert system[0].text == bot.messages[-1]


def test_stopped_delegation_records_nothing(tmp_path, monkeypatch):
    """A cancelled task stays silent in the transcript, as it does in the chat."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path / "agents", "scout")
    conversations = _isolate_conversations(tmp_path, monkeypatch)
    meta = conversations.new_conversation(1, "web")

    async def hang(**kw):
        await asyncio.sleep(60)

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", hang)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan",
            bot=bot,
            conversation_id=meta.id,
        )
        await asyncio.sleep(0)
        await stop_delegation(dt.task_id)
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "stopped"
    assert not bot.messages
    assert [
        t for t in conversations.read_transcript(1, meta.id) if t.role == "system"
    ] == []


def test_completion_text_clips_a_long_result(tmp_path, monkeypatch):
    """The shared helper truncates, so a huge answer cannot bloat the replay."""
    from condor.agents.delegate import DelegateTask, _completion_text

    dt = DelegateTask(
        task_id="x",
        agent_slug="scout",
        user_id=1,
        chat_id=42,
        server_name=None,
        task="t",
        status="done",
        result="y" * 5000,
    )
    text = _completion_text(dt)
    assert text.endswith("…")
    assert len(text) < 1600


def test_session_key_resolution_never_raises():
    """A missing/malformed/dead key resolves to "" -- never an exception.

    The delegate route must not fail because provenance could not be resolved.
    """
    from condor.web.routes.agents import _conversation_for_session

    assert asyncio.run(_conversation_for_session("")) == ""
    assert asyncio.run(_conversation_for_session("not-a-key")) == ""
    # Well-formed but no such session.
    assert asyncio.run(_conversation_for_session("web:999999:ghost")) == ""


def test_delegation_persists_full_session_transcript(tmp_path, monkeypatch):
    """The runner feeds streamed events to an event_sink and the transcript
    captures reasoning, tool calls (input/output), and the final result."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import TextChunk, ThoughtChunk, ToolCallEvent, ToolCallUpdate

    async def fake_run(*, event_sink, **kw):
        # Simulate the agent streaming reasoning, a tool call, and a final answer.
        event_sink(ThoughtChunk(text="I should scan the pools first."))
        event_sink(
            ToolCallEvent(
                tool_call_id="t1",
                title="get_market_data",
                status="in_progress",
                kind="other",
                input={"connector": "binance", "pair": "SOL-USDC"},
            )
        )
        event_sink(
            ToolCallUpdate(
                tool_call_id="t1", status="completed", output="3 pools found"
            )
        )
        event_sink(TextChunk(text="Done: 3 pools."))
        return "Done: 3 pools."

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan SOL pools",
            bot=_FakeBot(),
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "done"
    # Events captured in order on the task.
    assert [e["type"] for e in dt.events] == ["thought", "tool", "text"]
    tool_ev = dt.events[1]
    assert tool_ev["name"] == "get_market_data"
    assert tool_ev["status"] == "completed"  # patched by the ToolCallUpdate
    assert tool_ev["output"] == "3 pools found"

    # Transcript renders the full session, not just the result.
    text = (tmp_path / "scout" / "delegations" / f"{dt.task_id}.md").read_text()
    assert "## Session" in text
    assert "I should scan the pools first." in text
    assert "get_market_data" in text
    assert "3 pools found" in text
    assert "**Tool calls:** 1" in text
    assert "Done: 3 pools." in text


# ── The wire projection (FEAT-022) ──


def test_events_for_wire_preserves_the_three_shapes():
    """thought/text pass through; a tool keeps everything a transcript renders."""
    from condor.agents.delegate import events_for_wire

    events = [
        {"type": "thought", "text": "let me look"},
        {
            "type": "tool",
            "id": "t1",
            "name": "get_market_data",
            "status": "completed",
            "kind": "other",
            "input": {"connector": "binance"},
            "output": "ok",
        },
        {"type": "text", "text": "done"},
    ]

    wire = events_for_wire(events)

    assert [e["type"] for e in wire] == ["thought", "tool", "text"]
    assert wire[0] == {"type": "thought", "text": "let me look"}
    assert wire[2] == {"type": "text", "text": "done"}
    assert wire[1] == {
        "type": "tool",
        "id": "t1",
        "name": "get_market_data",
        "status": "completed",
        "kind": "other",
        "input": {"connector": "binance"},
        "output": "ok",
    }


def test_events_for_wire_handles_an_in_flight_tool_call():
    """A call the fold has created but not yet patched has no input/output yet."""
    from condor.agents.delegate import events_for_wire

    wire = events_for_wire(
        [{"type": "tool", "id": "t1", "name": "scan", "status": "in_progress"}]
    )

    assert wire[0]["status"] == "in_progress"
    assert wire[0]["input"] is None
    assert wire[0]["output"] is None
    assert wire[0]["kind"] == ""


def test_events_for_wire_clips_output_like_the_disk_transcript():
    """The wire and the .md cut at the same boundary with the same marker."""
    from condor.agents.delegate import MAX_TOOL_OUTPUT, _render_session, events_for_wire

    huge = "x" * (MAX_TOOL_OUTPUT + 500)
    events = [
        {
            "type": "tool",
            "id": "t1",
            "name": "scan",
            "status": "completed",
            "output": huge,
        }
    ]

    clipped = events_for_wire(events)[0]["output"]

    assert clipped == "x" * MAX_TOOL_OUTPUT + "\n… (truncated)"
    # Same string the on-disk transcript embeds -- the two must never disagree.
    assert clipped in _render_session(events)


def test_events_for_wire_returns_a_copy():
    """Serializing must not hand out the live entries the sink patches in place."""
    from condor.agents.delegate import events_for_wire

    events = [
        {
            "type": "tool",
            "id": "t1",
            "name": "scan",
            "status": "in_progress",
            "input": {"pair": "SOL-USDC"},
        }
    ]

    wire = events_for_wire(events)
    wire[0]["status"] = "completed"
    wire[0]["input"]["pair"] = "MUTATED"
    wire.append({"type": "text", "text": "injected"})

    assert events[0]["status"] == "in_progress"
    assert events[0]["input"] == {"pair": "SOL-USDC"}
    assert len(events) == 1


def test_events_for_wire_makes_input_json_safe():
    """A tool input can hold anything; the response must still serialize."""
    import json

    from condor.agents.delegate import events_for_wire

    class Opaque:
        def __repr__(self):
            return "<opaque>"

    wire = events_for_wire(
        [
            {
                "type": "tool",
                "id": "t1",
                "name": "scan",
                "status": "completed",
                "input": {"obj": Opaque()},
            }
        ]
    )

    assert json.dumps(wire)  # would raise if the projection leaked the object
    assert wire[0]["input"] == {"obj": "<opaque>"}


def test_to_dict_still_omits_events():
    """The MCP tool's payload must not grow the session stream (FEAT-022 alt B)."""
    from condor.agents.delegate import DelegateTask

    dt = DelegateTask(
        task_id="x",
        agent_slug="scout",
        user_id=1,
        chat_id=42,
        server_name=None,
        task="t",
    )
    dt.events.append({"type": "text", "text": "noise"})

    assert "events" not in dt.to_dict()


def test_events_route_shows_a_running_delegation_live(tmp_path, monkeypatch):
    """The point of the feature: two polls of a *running* task differ.

    The first sees a tool call in flight; the second sees the same call (same
    `id`, so the client updates the row instead of remounting it) completed with
    its output — without the task having finished.
    """
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ThoughtChunk, ToolCallEvent, ToolCallUpdate
    from condor.web.routes.agents import get_delegation_events

    gate_started = asyncio.Event()
    gate_release = asyncio.Event()

    async def fake_run(*, event_sink, **kw):
        event_sink(ThoughtChunk(text="scanning"))
        event_sink(
            ToolCallEvent(
                tool_call_id="t1",
                title="get_market_data",
                status="in_progress",
                kind="other",
                input={"pair": "SOL-USDC"},
            )
        )
        gate_started.set()
        await gate_release.wait()  # hold the delegation open mid-flight
        event_sink(
            ToolCallUpdate(tool_call_id="t1", status="completed", output="3 pools")
        )
        return "done"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan",
            bot=_FakeBot(),
        )
        await gate_started.wait()
        first = await get_delegation_events(dt.task_id, user=_CALLER)
        gate_release.set()
        await _drain(dt)
        second = await get_delegation_events(dt.task_id, user=_CALLER)
        return first, second

    first, second = asyncio.run(scenario())

    # Mid-flight: the task is running and the tool call is visibly in progress.
    assert first["status"] == "running"
    assert [e["type"] for e in first["events"]] == ["thought", "tool"]
    assert first["events"][1]["status"] == "in_progress"
    assert first["events"][1]["output"] is None

    # Same call, patched in place -- the id is what keeps the row identity.
    assert second["status"] == "done"
    assert second["events"][1]["id"] == first["events"][1]["id"] == "t1"
    assert second["events"][1]["status"] == "completed"
    assert second["events"][1]["output"] == "3 pools"
    # The earlier snapshot was a copy, so it did not mutate underneath us.
    assert first["events"][1]["status"] == "in_progress"


def test_events_route_404s_on_unknown_task():
    from fastapi import HTTPException

    from condor.web.routes.agents import get_delegation_events

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_delegation_events("nope-delegate-x", user=_CALLER))
    assert exc.value.status_code == 404


def test_events_route_returns_status_alongside_the_transcript():
    """`status` rides along so a poller knows when to stop without a 2nd request."""
    from condor.agents.delegate import DelegateTask
    from condor.web.routes.agents import get_delegation_events

    dt = DelegateTask(
        task_id="scout-delegate-abc",
        agent_slug="scout",
        user_id=1,
        chat_id=42,
        server_name=None,
        task="t",
    )
    dt.events.append({"type": "thought", "text": "thinking"})
    delegate_module._delegations[dt.task_id] = dt

    payload = asyncio.run(get_delegation_events(dt.task_id, user=_CALLER))

    assert payload["task_id"] == dt.task_id
    assert payload["status"] == "running"
    assert payload["events"] == [{"type": "thought", "text": "thinking"}]
