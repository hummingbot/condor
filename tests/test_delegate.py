"""Unit tests for DELEGATE -- fire-and-forget background agent tasks (FEAT-006).

Covers the lifecycle (running -> done/error/stopped), result capture, transcript
persistence, completion notification, and that the runner drives the shared consult
engine with ``permission_callback=None`` (auto-approve).
"""

import asyncio

import pytest

from condor import paths
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
    # Transcript written under the user who asked, not under the agent.
    transcript = paths.delegation_dir(1, dt.task_id) / "transcript.md"
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
        (paths.delegation_dir(1, dt.task_id) / "status.json").read_text()
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
    """The conversation store reader (the root is isolated suite-wide)."""
    from condor.runtime import conversations

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
    text = (paths.delegation_dir(1, dt.task_id) / "transcript.md").read_text()
    assert "## Session" in text
    assert "I should scan the pools first." in text
    assert "get_market_data" in text
    assert "3 pools found" in text
    assert "**Tool calls:** 1" in text
    assert "Done: 3 pools." in text


# ── Credential redaction on the persisted transcript (SEC-332) ──


def test_delegation_transcript_redacts_credential_arguments(tmp_path, monkeypatch):
    """A delegated agent's tool arguments never reach disk with a secret in them.

    The delegate path auto-approves its own tool calls, and at least one tool in
    the toolset takes a credential directly, so a verbatim argument set would
    put a plaintext password into every durable projection at once. All three
    are asserted here -- the events sidecar, the markdown transcript and the
    wire the dashboard's Input pane reads -- because they are separate writers
    of the same folded entry and a redaction that only covers one is no
    redaction at all.
    """
    import json

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ToolCallEvent, ToolCallUpdate
    from condor.agents.delegate import events_for_wire

    async def fake_run(*, event_sink, **kw):
        event_sink(
            ToolCallEvent(
                tool_call_id="t1",
                title="configure_server",
                status="in_progress",
                kind="other",
                input={
                    "name": "brigado",
                    "password": "hunter2",
                    "api_key": "sk-live-abcdef",
                    "trading_pair": "SOL-USDC",
                    "nested": {"wallet_private_key": "5Kd3…", "amount": 12.5},
                },
            )
        )
        event_sink(ToolCallUpdate(tool_call_id="t1", status="completed", output="ok"))
        return "configured"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="configure the server",
            bot=_FakeBot(),
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())
    assert dt.status == "done"

    secrets = ("hunter2", "sk-live-abcdef", "5Kd3…")
    record_dir = paths.delegation_dir(1, dt.task_id)
    sidecar = (record_dir / "events.json").read_text()
    transcript = (record_dir / "transcript.md").read_text()
    wire = json.dumps(events_for_wire(dt.events))
    live = json.dumps(dt.events, default=str)

    for blob in (sidecar, transcript, wire, live):
        for secret in secrets:
            assert secret not in blob
        # The non-secret arguments survive: a redactor that eats the trading
        # pair has made the transcript useless rather than safe.
        assert "SOL-USDC" in blob
        assert "brigado" in blob
        assert "[redacted]" in blob

    # And the shape is untouched -- keys stay, only the values are replaced.
    inp = events_for_wire(dt.events)[0]["input"]
    assert inp["password"] == "[redacted]"
    assert inp["api_key"] == "[redacted]"
    assert inp["nested"]["wallet_private_key"] == "[redacted]"
    assert inp["nested"]["amount"] == 12.5


def test_oversized_tool_input_is_redacted_before_it_is_clipped():
    """The size clip must not be a way for a secret to survive redaction.

    An argument set too large to keep whole degrades to its serialized string
    form; if the clip ran first, that string would be a verbatim credential
    dump with an ellipsis on the end.
    """
    from condor.agents.delegate import MAX_TOOL_OUTPUT, _bound_tool_payloads

    tc = {
        "id": "t1",
        "name": "configure_server",
        "input": {
            "password": "hunter2",
            "notes": "x" * (MAX_TOOL_OUTPUT + 100),
        },
    }
    _bound_tool_payloads(tc)

    assert isinstance(tc["input"], str)  # degraded to the clipped form
    assert "hunter2" not in tc["input"]
    assert "[redacted]" in tc["input"]


def test_delegate_redaction_reuses_the_chat_transcript_hint_list():
    """One hint list, two paths: a hint added for chat covers delegate too.

    Asserted by patching the shared tuple rather than by comparing two copies,
    which is the only way to catch a divergent second list being introduced.
    """
    from condor.agents.delegate import _bound_tool_payloads
    from condor.runtime import conversations

    original = conversations._SECRET_KEY_HINTS
    try:
        conversations._SECRET_KEY_HINTS = original + ("vault_pin",)
        tc = {"input": {"vault_pin": "4321", "pair": "SOL-USDC"}}
        _bound_tool_payloads(tc)
    finally:
        conversations._SECRET_KEY_HINTS = original

    assert tc["input"] == {"vault_pin": "[redacted]", "pair": "SOL-USDC"}


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
    wire[0]["input"] = {"pair": "MUTATED"}
    wire.append({"type": "text", "text": "injected"})

    assert events[0]["status"] == "in_progress"
    assert events[0]["input"] == {"pair": "SOL-USDC"}
    assert len(events) == 1


def test_events_for_wire_does_not_reserialize_the_input(monkeypatch):
    """The 2-second poll must not re-encode arguments the sink already coerced.

    The projection is a *read* path: a transcript left open re-runs it over the
    whole bounded stream every two seconds, on the loop that is also streaming
    the live chat, so a JSON round-trip per tool call there is paid once per
    reader per poll for work the write side already did (PERF-329). Asserted by
    making ``json`` itself fail, which is the only way to catch a round-trip
    being reintroduced.
    """
    import json

    from condor.agents import delegate as delegate_module

    def explode(*a, **kw):  # pragma: no cover - the point is that it is not hit
        raise AssertionError("events_for_wire must not serialize on the read path")

    monkeypatch.setattr(json, "dumps", explode)
    monkeypatch.setattr(json, "loads", explode)

    wire = delegate_module.events_for_wire(
        [
            {
                "type": "tool",
                "id": "t1",
                "name": "scan",
                "status": "completed",
                "input": {"pair": "SOL-USDC"},
                "output": "ok",
            }
        ]
    )

    assert wire[0]["input"] == {"pair": "SOL-USDC"}


def test_folded_tool_input_is_stored_json_safe():
    """A tool input can hold anything; what the sink stores must still serialize.

    The guarantee the wire used to buy with a per-poll round-trip, moved to the
    single write-side choke point every folded entry passes through -- so the
    events sidecar, the markdown transcript and the HTTP response all inherit it
    from one coercion instead of repeating it.
    """
    import json

    from condor.agents.delegate import _bound_tool_payloads, events_for_wire

    class Opaque:
        def __repr__(self):
            return "<opaque>"

    tc = {"type": "tool", "id": "t1", "name": "scan", "input": {"obj": Opaque()}}
    _bound_tool_payloads(tc)

    assert tc["input"] == {"obj": "<opaque>"}
    assert json.dumps(events_for_wire([tc]))  # would raise if the object leaked


def test_bound_tool_payloads_skips_redaction_when_the_input_did_not_change():
    """An output-only patch must not re-walk an argument set nothing touched.

    A tool call is folded several times and only one of those events carries
    arguments; redacting on the others allocated a fresh copy of the payload per
    event for no change (PERF-329). Skipping is safe only because it is
    idempotent -- the entry stays exactly as the redacting call left it.
    """
    from condor.agents.delegate import _bound_tool_payloads

    tc = {"input": {"password": "hunter2", "pair": "SOL-USDC"}, "output": "ok"}
    _bound_tool_payloads(tc)
    redacted = tc["input"]

    tc["output"] = "later output"
    _bound_tool_payloads(tc, redact_input=False)

    assert tc["output"] == "later output"  # the output bound still runs
    assert tc["input"] is redacted  # untouched, not re-allocated
    assert tc["input"] == {"password": "[redacted]", "pair": "SOL-USDC"}


def test_late_arguments_on_an_update_are_still_redacted():
    """The skip must key off what the fold *did*, not off the event's shape.

    ``claude-agent-acp`` announces a call with no arguments and supplies them on
    a later update, so a bound that only redacted on the announcement would put
    a plaintext credential in every projection of the call that matters.
    """
    import json

    from condor.acp.client import ToolCallEvent, ToolCallUpdate
    from condor.agents.delegate import _make_event_sink

    class _DT:
        events: list = []
        tool_calls: dict = {}

    dt = _DT()
    dt.events, dt.tool_calls = [], {}
    sink = _make_event_sink(dt)

    sink(ToolCallEvent(tool_call_id="t1", title="configure_server", status="pending"))
    sink(
        ToolCallUpdate(
            tool_call_id="t1",
            status="in_progress",
            input={"password": "hunter2", "pair": "SOL-USDC"},
        )
    )
    sink(ToolCallUpdate(tool_call_id="t1", status="completed", output="ok"))

    assert "hunter2" not in json.dumps(dt.events, default=str)
    assert dt.events[0]["input"]["password"] == "[redacted]"
    # Same object in both projections: the action log reads the map (FEAT-105).
    assert dt.tool_calls["t1"] is dt.events[0]


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
