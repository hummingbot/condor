"""RunStore (§7.1) acceptance: tick events → one organized ``{tick}.md``
per tick, lifecycle jsonl with torn-tail recovery, monotonic seq under
concurrent emitters, secret redaction, the >16KB spill backstop for
non-tick events, interrupted sweep with pending-permission voiding."""

import json
import os
import threading

import pytest

from condor.agents.runstore import (
    CorruptRunError,
    MAX_EVENT_BYTES,
    REDACTED,
    RunStore,
    UnknownRunError,
    is_run_id,
    new_ulid,
)


@pytest.fixture
def store(tmp_path):
    return RunStore(root=tmp_path)


def test_ulid_shape_and_monotonic_prefix():
    a, b = new_ulid(), new_ulid()
    assert is_run_id(a) and is_run_id(b)
    assert a != b
    assert len(a) == 26


def test_start_emit_end_roundtrip(store):
    run_id = store.start_run(
        "alpha",
        "session",
        payload={"model": "claude-code", "source_spec_hash": "s1"},
    )
    store.emit(run_id, "tick_started", {"n": 1}, tick=1)
    store.emit(run_id, "tick_completed", {"actions": 2}, tick=1)
    store.end_run(run_id, "stopped", "max_ticks")

    # The folder carries the run id; the stream inside is plain store.jsonl.
    assert store.find_run_path(run_id).name == "store.jsonl"
    # The jsonl is the machine stream — tick events went to 1.md instead.
    events = store.read_events("alpha", run_id)
    assert [e["type"] for e in events] == ["run_started", "run_ended"]
    # One seq counter across both files.
    assert [e["seq"] for e in events] == [1, 4]
    assert events[0]["payload"]["agent_slug"] == "alpha"
    assert events[0]["payload"]["kind"] == "session"
    assert events[0]["payload"]["display_seq"] == 1
    assert events[-1]["payload"] == {"status": "stopped", "reason": "max_ticks"}

    tick_md = (store.run_dir("alpha", run_id) / "1.md").read_text()
    assert "## Tick started" in tick_md
    assert "## Tick completed" in tick_md

    meta = store.run_meta("alpha", run_id)
    assert meta["status"] == "stopped"
    assert meta["kind"] == "session"


def test_emit_after_end_rejected(store):
    run_id = store.start_run("alpha", "session")
    store.end_run(run_id, "stopped")
    with pytest.raises(UnknownRunError):
        store.emit(run_id, "error", {"message": "late"})


def test_unknown_kind_rejected(store):
    with pytest.raises(ValueError):
        store.start_run("alpha", "cron")


def test_permissions_of_run(store):
    run_id = store.start_run("alpha", "session")
    store.emit(
        run_id,
        "permission",
        {"approval_id": "ap1", "status": "pending", "summary": "create order"},
        tool_call_id="tc1",
    )
    assert store._pending_permissions("alpha", run_id) != []
    store.emit(
        run_id,
        "permission",
        {"approval_id": "ap1", "decision": "approved", "channel": "web"},
    )
    assert store._pending_permissions("alpha", run_id) == []


def test_concurrent_emitters_strictly_monotonic_seq(store):
    run_id = store.start_run("alpha", "session")
    n_threads, per_thread = 8, 50

    def emit_many():
        for i in range(per_thread):
            store.emit(run_id, "tool_call", {"i": i})

    threads = [threading.Thread(target=emit_many) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.end_run(run_id, "stopped")

    events = store.read_events("alpha", run_id)
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, n_threads * per_thread + 3))


def test_torn_tail_ignored_on_read_and_truncated_on_append(store, tmp_path):
    run_id = store.start_run("alpha", "session")
    store.emit(run_id, "tick_started", {}, tick=1)
    store.end_run(run_id, "stopped")
    path = store.run_path("alpha", run_id)

    # Simulate a crash mid-append: partial JSON with no newline.
    with open(path, "a") as f:
        f.write('{"v": 1, "seq": 99, "type": "tool_ca')

    events = store.read_events("alpha", run_id)
    assert [e["type"] for e in events] == ["run_started", "run_ended"]

    # Reopening for append truncates the torn tail; seq resumes correctly.
    from condor.agents.runstore import _RunWriter

    w = _RunWriter(path, run_id, "alpha")
    ev = w.emit("error", {"message": "post-recovery"})
    w.close()
    assert ev["seq"] == 4
    events = store.read_events("alpha", run_id)
    assert events[-1]["type"] == "error"


def test_midfile_corruption_raises(store):
    run_id = store.start_run("alpha", "session")
    store.end_run(run_id, "stopped")
    path = store.run_path("alpha", run_id)
    lines = path.read_text().splitlines()
    lines.insert(1, "NOT JSON")
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(CorruptRunError):
        store.read_events("alpha", run_id)


def test_secret_redaction(store):
    run_id = store.start_run("alpha", "session")
    evm_key = "0x" + "ab" * 32
    b58_blob = "5" * 87
    ev = store.emit(
        run_id,
        "tool_call",
        {
            "input": {"private_key": "hunter2", "api_secret": "x", "size": "1.5"},
            "output": f"signed with {evm_key} and {b58_blob} ok",
        },
    )
    store.end_run(run_id, "stopped")
    assert ev["payload"]["input"]["private_key"] == REDACTED
    assert ev["payload"]["input"]["api_secret"] == REDACTED
    assert ev["payload"]["input"]["size"] == "1.5"
    assert evm_key not in ev["payload"]["output"]
    assert b58_blob not in ev["payload"]["output"]
    raw = store.run_path("alpha", run_id).read_text()
    assert "hunter2" not in raw
    assert evm_key not in raw


def test_tick_events_write_one_organized_md_per_tick(store):
    """Every event carrying a tick number lands as an organized section of
    that tick's ``{tick}.md`` — tick started, tool calls, state snapshot,
    tick completion — full fidelity (no 16KB spill), redacted, and off the
    jsonl machine stream."""
    run_id = store.start_run("alpha", "session")
    big = "x" * (MAX_EVENT_BYTES * 2)
    store.emit(
        run_id,
        "tick_started",
        {"prompt_suffix": "[TICK INFO] tick 1", "prompt_sha256": "abc123"},
        tick=1,
    )
    store.emit(
        run_id,
        "tool_call",
        {
            "name": "order_spot",
            "status": "completed",
            "input": {"private_key": "hunter2", "size": "1.5"},
            "output": big,
            "mutating": True,
        },
        tick=1,
        tool_call_id="tc1",
    )
    store.emit(
        run_id,
        "state_snapshot",
        {"decision": "ENTER FLEA", "state": "Last tick: #1 | pnl=+0"},
        tick=1,
    )
    store.emit(run_id, "tick_completed", {"actions": 1, "duration": 2.0}, tick=1)
    store.emit(run_id, "tick_started", {}, tick=2)
    store.end_run(run_id, "stopped")

    run_dir = store.run_dir("alpha", run_id)
    md = (run_dir / "1.md").read_text()
    assert md.startswith(f"# Tick 1 — alpha run {run_id}")
    for section in (
        "## Tick started",
        "## Tool call — order_spot (completed)",
        "## State snapshot",
        "## Tick completed",
    ):
        assert section in md
    assert "[TICK INFO] tick 1" in md  # prompt suffix verbatim
    assert "`abc123`" in md
    assert "ENTER FLEA" in md
    # Full fidelity — the oversized output is in the file, with NO
    # timestamped spill artifact created.
    assert big in md
    assert not list(run_dir.glob("*Z-*.md"))
    # The redactor applies to tick markdown exactly as to jsonl payloads.
    assert "hunter2" not in md
    assert REDACTED in md

    # One file per tick, in order, via the reader exports use.
    assert (run_dir / "2.md").exists()
    assert [n for n, _ in store.tick_files("alpha", run_id)] == [1, 2]

    # The jsonl kept only the machine stream.
    events = store.read_events("alpha", run_id)
    assert [e["type"] for e in events] == ["run_started", "run_ended"]


def test_oversized_payload_spills_to_markdown_artifact(store):
    import hashlib
    import re

    run_id = store.start_run("alpha", "session")
    big = "x" * (MAX_EVENT_BYTES + 1000)
    ev = store.emit(run_id, "tool_call", {"output": big, "meta": {"tick": 3}})
    store.end_run(run_id, "stopped")
    payload = ev["payload"]
    assert "artifact" in payload
    assert payload["bytes"] > MAX_EVENT_BYTES

    # The spill is a UTC-stamped markdown file named for the event time + type,
    # sitting in the run's own folder next to the jsonl (bare filename ref).
    file_name = payload["artifact"]
    assert "/" not in file_name
    assert re.fullmatch(r"\d{2}-\d{2}-\d{2}Z-tool_call\.md", file_name)

    artifact_path = store.run_dir("alpha", run_id) / payload["artifact"]
    text = artifact_path.read_text()
    assert text.startswith("# tool_call — ")
    assert big in text  # string values verbatim
    assert '"tick": 3' in text  # non-strings as fenced JSON
    # Integrity hash covers the markdown exactly as written.
    assert payload["sha256"] == hashlib.sha256(text.encode()).hexdigest()

    # The stream line itself stays under the cap + envelope overhead.
    raw_last = [
        l
        for l in store.run_path("alpha", run_id).read_text().splitlines()
        if '"tool_call"' in l
    ][0]
    assert len(raw_last.encode()) < MAX_EVENT_BYTES


def test_financial_events_are_fsynced_types(store):
    # Sanity: the type sets used by the durability policy exist as documented.
    from condor.agents import runstore

    assert {"run_started", "run_ended", "permission"} <= runstore._FSYNC_TYPES


def test_permissions_0600(store):
    run_id = store.start_run("alpha", "session")
    store.end_run(run_id, "stopped")
    path = store.run_path("alpha", run_id)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"


def test_sweep_interrupted_synthesizes_run_ended_and_voids_approvals(tmp_path):
    store = RunStore(root=tmp_path)
    run_id = store.start_run("alpha", "session")
    store.emit(
        run_id,
        "permission",
        {"approval_id": "ap1", "status": "pending", "summary": "create"},
        executor_id="ex1",
    )
    # Simulate process death: forget the writer without run_ended.
    store._writers[run_id].close()
    store._writers.clear()

    fresh = RunStore(root=tmp_path)
    voided = fresh.sweep_interrupted()
    assert [v["approval_id"] for v in voided] == ["ap1"]
    assert voided[0]["run_id"] == run_id

    events = fresh.read_events("alpha", run_id)
    assert events[-1]["type"] == "run_ended"
    assert events[-1]["payload"]["status"] == "interrupted"
    void_ev = [e for e in events if e["type"] == "permission"][-1]
    assert void_ev["payload"]["decision"] == "interrupted_void"
    assert void_ev["executor_id"] == "ex1"
    # Sweep is idempotent.
    assert fresh.sweep_interrupted() == []
    meta = fresh.run_meta("alpha", run_id)
    assert meta["status"] == "interrupted"


def test_list_runs_newest_first_with_kind_filter(store):
    r1 = store.start_run("alpha", "session")
    store.end_run(r1, "stopped")
    r2 = store.start_run("alpha", "scheduled", payload={"task": "scan"})
    store.end_run(r2, "completed")
    os.utime(store.run_path("alpha", r2, "scheduled"))

    runs = store.list_runs("alpha")
    assert {r["run_id"] for r in runs} == {r1, r2}
    assert runs[0]["run_id"] == r2  # newest first
    only_sched = store.list_runs("alpha", kind="scheduled")
    assert [r["run_id"] for r in only_sched] == [r2]
    assert only_sched[0]["task"] == "scan"
    assert only_sched[0]["display_seq"] == 2


def test_running_status_while_open(store):
    run_id = store.start_run("alpha", "session")
    assert store.run_meta("alpha", run_id)["status"] == "running"
    store.end_run(run_id, "stopped")
    assert store.run_meta("alpha", run_id)["status"] == "stopped"
