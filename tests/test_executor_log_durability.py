"""Phase 3 acceptance (§3): executor JSONL durability — torn-tail recovery,
permissions, fsync-per-transition posture."""

import json
import stat

from condor.executors.log import ExecutorLog


def _opener(eid, slug="acme", status="ACTIVE"):
    return {
        "ts": 1.0,
        "event": "opened",
        "id": eid,
        "type": "order_spot",
        "agent_slug": slug,
        "agent_id": f"{slug}_1",
        "strategy": "",
        "status": status,
        "close_reason": None,
        "config": {"type": "order_spot"},
        "state": {"state": "RESTING", "open_ref": "sig1"},
    }


def test_torn_final_line_ignored_on_fold_and_truncated_on_append(tmp_path):
    store = ExecutorLog(tmp_path)
    store._append("acme", _opener("e1"))
    path = tmp_path / "acme" / "executors.jsonl"

    # Simulate a crash mid-append: partial JSON with no trailing newline.
    with path.open("a") as fh:
        fh.write('{"ts": 2.0, "event": "closed", "id": "e1", "sta')

    # Fold tolerates the torn tail — e1 remains ACTIVE (the torn close never
    # happened durably).
    rec = store.load("e1")
    assert rec is not None and rec.status == "ACTIVE"

    # The next append truncates the torn tail first — the file stays
    # well-formed and the new event lands cleanly.
    store._append("acme", {"ts": 3.0, "event": "closed", "id": "e1",
                           "status": "CLOSED", "close_reason": "done"})
    lines = path.read_text().splitlines()
    assert all(json.loads(ln) for ln in lines)
    assert store.load("e1").status == "CLOSED"


def test_torn_middle_line_still_raises(tmp_path):
    """Only the FINAL line may be torn (crash mid-append); corruption anywhere
    else is a real store fault and must fail loudly, never be skipped."""
    import pytest

    store = ExecutorLog(tmp_path)
    path = tmp_path / "acme" / "executors.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"broken\n' + json.dumps(_opener("e1")) + "\n")
    with pytest.raises(json.JSONDecodeError):
        store.load("e1")


def test_log_file_permissions(tmp_path):
    store = ExecutorLog(tmp_path)
    store._append("acme", _opener("e1"))
    dir_mode = stat.S_IMODE((tmp_path / "acme").stat().st_mode)
    file_mode = stat.S_IMODE((tmp_path / "acme" / "executors.jsonl").stat().st_mode)
    assert dir_mode == 0o700
    assert file_mode == 0o600


def test_append_survives_partial_write_then_recovery(tmp_path):
    """Kill −9 simulation across the critical append categories: after each
    category's last DURABLE line, a torn partial of the next event must fold
    back to the durable state."""
    store = ExecutorLog(tmp_path)
    path = tmp_path / "acme" / "executors.jsonl"

    # Category 1: opener (SUBMITTING reservation)
    op = _opener("e1", status="ACTIVE")
    op["state"] = {"state": "SUBMITTING", "open_ref": None}
    store._append("acme", op)
    with path.open("a") as fh:
        fh.write('{"partial')
    rec = ExecutorLog(tmp_path).load("e1")
    assert rec.state["state"] == "SUBMITTING"

    # Category 2: landed order (open_ref durable)
    landed = dict(op)
    landed["ts"] = 2.0
    landed["state"] = {"state": "RESTING", "open_ref": "oid-99"}
    store._append("acme", landed)  # truncates the torn tail first
    with path.open("a") as fh:
        fh.write('{"partial2')
    rec = ExecutorLog(tmp_path).load("e1")
    assert rec.state["open_ref"] == "oid-99"

    # Category 3: terminal close
    store._append("acme", {"ts": 3.0, "event": "closed", "id": "e1",
                           "status": "CLOSED", "close_reason": "filled"})
    rec = ExecutorLog(tmp_path).load("e1")
    assert rec.status == "CLOSED" and rec.state["open_ref"] == "oid-99"
