"""Notifications durability (§4.1): stable ids, fsynced appends, id-based
relay cursors persisted atomically after delivery — a crash between (a)
append and delivery or (b) delivery and cursor advance recovers with no
lost notifications and id-based dedup for the (single) re-delivery."""

import asyncio
import json
import sys

import pytest

import integrations.notify_relay.relay as relay
from condor import notifications


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    path = tmp_path / "notifications.jsonl"
    monkeypatch.setattr(notifications, "OUTBOX_PATH", path)
    return path


def _emit(text, **kw):
    return asyncio.run(notifications.notify(text, **kw))


def test_entries_carry_stable_unique_ids(outbox):
    e1 = _emit("one")
    e2 = _emit("two")
    assert e1["id"] and e2["id"] and e1["id"] != e2["id"]
    on_disk = [json.loads(l) for l in outbox.read_text().splitlines()]
    assert [e["id"] for e in on_disk] == [e1["id"], e2["id"]]


def test_read_since_id(outbox):
    e1 = _emit("one")
    _emit("two")
    got = notifications.read_notifications(since_id=e1["id"])
    assert [e["text"] for e in got] == ["two"]
    # Unknown id → everything (caller caps replay).
    got = notifications.read_notifications(since_id="NOPE")
    assert len(got) == 2


def test_torn_tail_skipped_on_read(outbox):
    _emit("good")
    with outbox.open("a") as f:
        f.write('{"id": "X", "text": "torn')  # no newline, crash mid-append
    got = notifications.read_notifications()
    assert [e["text"] for e in got] == ["good"]


def _delivering_relay(tmp_path, outbox, sink, cursor):
    template = [
        sys.executable, "-c",
        f"import sys;open(r'{sink}','a').write(sys.argv[1]+'\\n')",
        "{json}",
    ]

    async def one_pass():
        await relay.run(template, outbox=outbox, once=True, state_path=cursor)

    return one_pass


def test_crash_between_append_and_delivery_loses_nothing(tmp_path, outbox):
    """(a) entries appended while the relay is dead are delivered on the
    next start — the cursor still points before them."""
    sink = tmp_path / "sink.txt"
    cursor = tmp_path / "cursor"
    one_pass = _delivering_relay(tmp_path, outbox, sink, cursor)

    e1 = _emit("first")
    # Fresh start anchors AT the tail (history is skippable via pull);
    # deliver nothing but persist e1 as the anchor.
    asyncio.run(one_pass())
    assert json.loads(cursor.read_text())["last_id"] == e1["id"]

    # Relay dead; two entries appended ("the crash window").
    e2 = _emit("second")
    e3 = _emit("third")
    asyncio.run(one_pass())
    delivered = [json.loads(l) for l in sink.read_text().splitlines()]
    assert [d["id"] for d in delivered] == [e2["id"], e3["id"]]  # nothing lost
    assert json.loads(cursor.read_text())["last_id"] == e3["id"]


def test_crash_between_delivery_and_cursor_advance_dedups_by_id(tmp_path, outbox):
    """(b) delivered-but-cursor-not-advanced → exactly that entry is
    re-delivered with the SAME id; id-dedup yields no duplicate display."""
    sink = tmp_path / "sink.txt"
    cursor = tmp_path / "cursor"
    one_pass = _delivering_relay(tmp_path, outbox, sink, cursor)

    e1 = _emit("anchor")
    asyncio.run(one_pass())  # anchor at e1

    e2 = _emit("payload")
    asyncio.run(one_pass())  # delivers e2, advances cursor to e2

    # Simulate the crash window: roll the cursor BACK to e1, as if the
    # process died after delivering e2 but before persisting.
    cursor.write_text(json.dumps({"last_id": e1["id"]}))
    asyncio.run(one_pass())

    delivered = [json.loads(l) for l in sink.read_text().splitlines()]
    ids = [d["id"] for d in delivered]
    assert ids == [e2["id"], e2["id"]]  # re-delivered once, same id
    assert len(set(ids)) == 1  # id-based dedup collapses the duplicate
    assert json.loads(cursor.read_text())["last_id"] == e2["id"]


def test_cursor_persist_is_atomic(tmp_path):
    cursor = tmp_path / "cursor"
    relay._persist_cursor(cursor, "ID1")
    assert json.loads(cursor.read_text()) == {"last_id": "ID1"}
    # No tmp litter left behind.
    assert list(tmp_path.glob("cursor.tmp*")) == []


def test_mcp_subprocess_writes_via_socket_not_file():
    """§4.1 single-writer rule: the MCP notification tool must not import a
    direct file-append path anymore."""
    import inspect

    from mcp_servers.condor.tools import notification

    src = inspect.getsource(notification)
    assert "_append_outbox" not in src
    assert "notify.emit" in src
