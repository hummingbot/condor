"""Unit tests for delegation retention -- the memory bounds (CORR-143) and the
disk bound (PERF-222).

``_delegations`` used to be append-only and every entry kept the full,
untruncated event stream for the life of the process. These tests pin both
bounds and, just as importantly, what must NOT be lost to them: a recent
delegation is still fully retrievable, an evicted one is still readable from
disk, and an in-flight one is never evicted at any count.

The second half is the same question one level down: nothing ever removed a
record *directory*, so a history listing walked every delegation the install had
run. The sweep bounds that too, and the tests pin what it must never take --
anything still running, and anything past the cap that is not terminal.
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
from condor.agents.delegation_history import (
    list_history,
    read_history,
    read_history_events,
)


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
    record = read_history(7, first.task_id)
    assert record is not None
    assert record["status"] == "done"
    assert record["result"] == "done ok"
    events, _markdown = read_history_events(7, first.task_id)
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


# ── The disk bound (PERF-222) ──────────────────────────────────────────────


def _write_record(user_id, task_id, *, state="done", started_at=0.0):
    """One delegation record on disk, in the shape ``_run``'s finally writes."""
    from condor import paths
    from condor.runtime.registry_file import write_status

    record_dir = paths.delegation_dir(user_id, task_id)
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "transcript.md").write_text(f"# {task_id}\n")
    (record_dir / "events.json").write_text("[]")
    write_status(
        record_dir,
        state=state,
        task_id=task_id,
        agent_slug="scout",
        user_id=user_id,
        started_at=started_at,
        task=f"task {task_id}",
    )
    return record_dir


def _record_ids(user_id):
    from condor import paths

    return sorted(p.name for p in paths.delegations_dir(user_id).iterdir())


def test_terminal_records_on_disk_are_bounded_and_evicted_oldest_first(monkeypatch):
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 5)

    for i in range(9):  # started_at ascending: 0 is the oldest
        _write_record(7, f"scout-delegate-{i:02d}", started_at=1000.0 + i)

    assert delegate_module.prune_delegation_records(7) == 4
    # The four oldest are gone, the five newest survive.
    assert _record_ids(7) == [f"scout-delegate-{i:02d}" for i in range(4, 9)]
    # A second sweep has nothing left to do.
    assert delegate_module.prune_delegation_records(7) == 0


def test_eviction_removes_the_whole_record_directory(monkeypatch):
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 1)

    old = _write_record(7, "scout-delegate-old", started_at=1.0)
    _write_record(7, "scout-delegate-new", started_at=2.0)

    assert delegate_module.prune_delegation_records(7) == 1
    # No orphan status, events or transcript left behind...
    assert not old.exists()
    # ...and a reader gets nothing rather than half a record.
    assert read_history(7, "scout-delegate-old") is None
    assert read_history(7, "scout-delegate-new") is not None


def test_a_record_that_is_not_terminal_is_never_evicted_nor_counted(monkeypatch):
    """On-disk state and the live registry both grant immunity."""
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 2)

    # Older than everything, and still running as far as its status file knows.
    _write_record(7, "scout-delegate-running", state="running", started_at=1.0)
    # Terminal on disk, but still live in this process (its task is on the loop).
    live = DelegateTask(
        task_id="scout-delegate-finishing",
        agent_slug="scout",
        user_id=7,
        chat_id=42,
        server_name=None,
        task="t",
        status="done",
    )

    async def _never():
        await asyncio.Event().wait()

    async def scenario():
        live._task = asyncio.ensure_future(_never())
        delegate_module._delegations[live.task_id] = live
        _write_record(7, live.task_id, started_at=2.0)
        for i in range(6):
            _write_record(7, f"scout-delegate-{i:02d}", started_at=10.0 + i)
        evicted = delegate_module.prune_delegation_records(7)
        live._task.cancel()
        return evicted

    # Six terminal candidates, cap two: four go, and neither exempt record is
    # among them even though both are older than every candidate.
    assert asyncio.run(scenario()) == 4
    assert _record_ids(7) == [
        "scout-delegate-04",
        "scout-delegate-05",
        "scout-delegate-finishing",
        "scout-delegate-running",
    ]


def test_retention_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 0)

    for i in range(6):
        _write_record(7, f"scout-delegate-{i:02d}", started_at=float(i))

    assert delegate_module.prune_delegation_records(7) == 0
    assert len(_record_ids(7)) == 6


def test_finishing_a_delegation_sweeps_its_own_owners_directory(tmp_path, monkeypatch):
    """The end-to-end bound: a listing never walks more than the cap again."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 3)

    ids = [_run_delegation(monkeypatch, task=f"task {i}").task_id for i in range(8)]

    # The cap, plus the one whose own finally is running the sweep -- it is a
    # live task, so it is exempt from eviction and from the count.
    on_disk = _record_ids(7)
    assert len(on_disk) == 4
    assert ids[-1] in on_disk
    for task_id in ids[:4]:
        assert read_history(7, task_id) is None
    assert read_history(7, ids[-1]) is not None
    # And the listing that used to walk all eight now walks what is left.
    assert len(list_history(user_id=7, limit=100)) == 4


def test_a_retention_failure_does_not_break_the_final_status_write(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(delegate_module, "prune_delegation_records", boom)

    dt = _run_delegation(monkeypatch, task="survives retention")

    record = read_history(7, dt.task_id)
    assert record is not None
    assert record["status"] == "done"
    assert record["result"] == "done ok"
