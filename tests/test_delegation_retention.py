"""Unit tests for delegation retention -- the memory bounds (CORR-143).

``_delegations`` used to be append-only and every entry kept the full,
untruncated event stream for the life of the process. These tests pin both
bounds and, just as importantly, what must NOT be lost to them: a recent
delegation is still fully retrievable, an evicted one is still readable from
disk, and an in-flight one is never evicted at any count.
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegate import (
    MAX_EVENTS_PER_DELEGATION,
    MAX_FINISHED_DELEGATIONS,
    MAX_TOOL_OUTPUT,
    DelegateTask,
    events_for_wire,
    get_delegation,
    retire_delegation,
    start_delegation,
)
from condor.agents.delegation_history import read_history, read_history_events


class _FakeBot:
    async def send_message(self, *a, **kw):
        pass


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


def _run_delegation(monkeypatch, task="scan SOL pools", emit=None, result="done ok"):
    """Drive one delegation to completion, optionally emitting events."""

    async def fake_run(*, event_sink, **kw):
        if emit is not None:
            emit(event_sink)
        return result

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=7,
            chat_id=42,
            server_name="local",
            task=task,
            bot=_FakeBot(),
        )
        await dt._task
        return dt

    return asyncio.run(scenario())


# ── The registry bound ─────────────────────────────────────────────────────


def test_finished_delegations_are_bounded_and_evicted_oldest_first(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    ids = [
        _run_delegation(monkeypatch, task=f"task {i}").task_id
        for i in range(MAX_FINISHED_DELEGATIONS + 5)
    ]

    assert len(delegate_module._delegations) == MAX_FINISHED_DELEGATIONS
    # The five oldest are gone, the newest survive -- oldest-first eviction.
    for task_id in ids[:5]:
        assert get_delegation(task_id) is None
    for task_id in ids[5:]:
        assert get_delegation(task_id) is not None


def test_an_evicted_delegation_is_still_readable_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ThoughtChunk

    def emit(sink):
        sink(ThoughtChunk(text="weighing the pools"))

    first = _run_delegation(monkeypatch, task="the first one", emit=emit)
    for i in range(MAX_FINISHED_DELEGATIONS + 1):
        _run_delegation(monkeypatch, task=f"filler {i}")

    # Evicted from memory...
    assert get_delegation(first.task_id) is None
    # ...but nothing was lost: the record and the transcript are on disk, which
    # is what the /agents/delegations/{task_id} routes fall through to.
    record = read_history(first.task_id)
    assert record is not None
    assert record["status"] == "done"
    assert record["result"] == "done ok"
    events, _markdown = read_history_events(first.task_id)
    assert any(e["type"] == "thought" for e in events)


def test_a_recent_delegation_is_still_fully_retrievable(tmp_path, monkeypatch):
    """The collect-later contract: a poll right after completion hits memory."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ToolCallEvent, ToolCallUpdate

    def emit(sink):
        sink(
            ToolCallEvent(
                tool_call_id="tc-1",
                title="get_market_data",
                status="in_progress",
                input={"pair": "SOL-USDC"},
            )
        )
        sink(ToolCallUpdate(tool_call_id="tc-1", status="completed", output="3 pools"))

    dt = _run_delegation(monkeypatch, emit=emit, result="three pools worth watching")

    live = get_delegation(dt.task_id)
    assert live is dt
    assert live.to_dict()["result"] == "three pools worth watching"
    wire = events_for_wire(live.events)
    assert [e["type"] for e in wire] == ["tool"]
    assert wire[0]["output"] == "3 pools"
    assert wire[0]["input"] == {"pair": "SOL-USDC"}


def test_an_in_flight_delegation_is_never_evicted(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    running = DelegateTask(
        task_id="scout-delegate-inflight",
        agent_slug="scout",
        user_id=7,
        chat_id=42,
        server_name="local",
        task="a long one",
    )
    delegate_module._delegations[running.task_id] = running

    for i in range(MAX_FINISHED_DELEGATIONS * 2):
        _run_delegation(monkeypatch, task=f"filler {i}")

    assert running.status == "running"
    assert get_delegation(running.task_id) is running
    # The bound counts terminal entries only, so the live one rides above it.
    assert len(delegate_module._delegations) == MAX_FINISHED_DELEGATIONS + 1


def test_retiring_a_dropped_delegation_does_not_resurrect_it():
    """A second retire of an already-evicted task must not re-register it."""
    dt = DelegateTask(
        task_id="scout-delegate-gone",
        agent_slug="scout",
        user_id=7,
        chat_id=42,
        server_name=None,
        task="t",
        status="done",
    )
    assert retire_delegation(dt) == 0
    assert get_delegation(dt.task_id) is None


# ── The per-entry event bound ──────────────────────────────────────────────


def test_the_event_stream_of_one_delegation_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ToolCallEvent

    overflow = 50

    def emit(sink):
        for i in range(MAX_EVENTS_PER_DELEGATION + overflow):
            sink(
                ToolCallEvent(
                    tool_call_id=f"tc-{i}",
                    title=f"tool_{i}",
                    status="completed",
                    input={"i": i},
                )
            )

    dt = _run_delegation(monkeypatch, emit=emit)

    assert len(dt.events) == MAX_EVENTS_PER_DELEGATION
    # The head is what gets dropped, and the cut is recorded rather than silent.
    marker = dt.events[0]
    assert marker["type"] == delegate_module.DROPPED_EVENT_TYPE
    assert marker["count"] == overflow + 1  # +1: the marker took a slot too
    assert dt.events[-1]["name"] == f"tool_{MAX_EVENTS_PER_DELEGATION + overflow - 1}"
    # The marker reaches readers as a plain text note, not a new wire type.
    wire = events_for_wire(dt.events)
    assert wire[0]["type"] == "text"
    assert str(marker["count"]) in wire[0]["text"]


def test_a_huge_tool_output_is_clipped_in_memory_not_only_on_the_wire(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ToolCallEvent, ToolCallUpdate

    huge = "x" * (MAX_TOOL_OUTPUT * 50)

    def emit(sink):
        sink(
            ToolCallEvent(
                tool_call_id="tc-1",
                title="get_market_data",
                status="in_progress",
                input={"dump": huge},
            )
        )
        sink(ToolCallUpdate(tool_call_id="tc-1", status="completed", output=huge))

    dt = _run_delegation(monkeypatch, emit=emit)

    entry = dt.events[-1]
    assert len(entry["output"]) < MAX_TOOL_OUTPUT + 100
    assert entry["output"].endswith(delegate_module.TRUNCATION_MARKER)
    # A tool input can carry a whole file (an Edit/Write call), so it is bounded
    # too -- degraded to its clipped string form only when it overflows.
    assert len(entry["input"]) < MAX_TOOL_OUTPUT + 100
    # Clipping twice must not stack a second marker: _clip_output is idempotent.
    assert events_for_wire(dt.events)[-1]["output"] == entry["output"]


def test_a_long_reasoning_run_rolls_into_new_events_instead_of_one_string(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import ThoughtChunk

    chunk = "y" * 500

    def emit(sink):
        for _ in range(40):  # 20k chars of one uninterrupted thought
            sink(ThoughtChunk(text=chunk))

    dt = _run_delegation(monkeypatch, emit=emit)

    assert len(dt.events) > 1
    assert all(e["type"] == "thought" for e in dt.events)
    assert all(
        len(e["text"]) <= delegate_module.MAX_CHUNK_CHARS + len(chunk)
        for e in dt.events
    )
    # Nothing was lost by rolling: it is the same reasoning, in more entries.
    assert "".join(e["text"] for e in dt.events) == chunk * 40
