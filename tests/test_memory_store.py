"""Unit tests for condor.memory.store.MemoryStore."""

from pathlib import Path

import pytest

from condor.memory import store as store_module
from condor.memory.store import MemoryStore


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """Point the store's data root at a tmp dir for isolation."""
    root = tmp_path / "memory"
    monkeypatch.setattr(store_module, "_DATA_ROOT", root)
    return root


def test_write_list_read_roundtrip(memory_root):
    s = MemoryStore(user_id=42)
    res = s.write(
        name="Report in USD",
        content="Always report volumes and PnL in USD with thousands separators.",
        description="The user always wants values reported in USD",
        type="preference",
        source="chat",
    )
    assert res["saved"] is True
    assert res["name"] == "report_in_usd"
    assert res["type"] == "preference"

    index = s.list_index()
    assert "report_in_usd" in index
    assert "USD" in index
    assert "preference" in index

    body = s.read("Report in USD")
    assert body is not None
    assert "thousands separators" in body


def test_read_missing_returns_none(memory_root):
    s = MemoryStore(user_id=1)
    assert s.read("does_not_exist") is None


def test_empty_index_is_empty_string(memory_root):
    s = MemoryStore(user_id=7)
    assert s.list_index() == ""


def test_search_matches_body_and_description(memory_root):
    s = MemoryStore(user_id=42)
    s.write(
        "Default exchange",
        "Binance is the default.",
        "Default exchange is Binance",
        type="fact",
    )
    s.write(
        "Risk style",
        "Conservative sizing.",
        "User prefers conservative risk",
        type="preference",
    )

    hits = s.search("binance")
    assert len(hits) == 1
    assert hits[0]["name"] == "default_exchange"

    hits = s.search("conservative")
    assert len(hits) == 1
    assert hits[0]["name"] == "risk_style"

    # Empty query returns everything (capped by limit).
    assert len(s.search("")) == 2


def test_invalid_type_falls_back_to_fact(memory_root):
    s = MemoryStore(user_id=42)
    res = s.write("X", "body", "desc", type="not_a_type")
    assert res["type"] == "fact"


def test_overwrite_preserves_created_date(memory_root):
    s = MemoryStore(user_id=42)
    s.write("Pair", "v1", "desc v1", type="fact")
    path = memory_root / "user_42" / "memories" / "pair.md"
    first = path.read_text()
    created_line = [l for l in first.splitlines() if l.startswith("created:")][0]

    s.write("Pair", "v2 updated", "desc v2", type="preference")
    second = path.read_text()
    assert created_line in second  # created unchanged
    assert "v2 updated" in second
    assert "type: preference" in second


def test_slug_dedup_no_duplicate_files(memory_root):
    s = MemoryStore(user_id=42)
    s.write("Report In USD", "a", "desc", type="fact")
    s.write("report-in-usd", "b", "desc2", type="fact")  # same slug
    files = list((memory_root / "user_42" / "memories").glob("*.md"))
    assert len(files) == 1


def test_delete_removes_and_audits(memory_root):
    s = MemoryStore(user_id=42)
    s.write("Temp", "body", "desc", type="fact", source="chat")
    assert s.delete("Temp", source="user") is True
    assert s.read("Temp") is None
    assert s.delete("Temp") is False  # already gone

    actions = [e["action"] for e in s.audit()]
    assert "write" in actions
    assert "delete" in actions
    delete_entry = [e for e in s.audit() if e["action"] == "delete"][0]
    assert delete_entry["source"] == "user"
    assert delete_entry["target"] == "memory:temp"


def test_audit_records_source(memory_root):
    s = MemoryStore(user_id=42)
    s.write("From agent", "body", "desc", source="agent:grid_scalper")
    entry = s.audit()[-1]
    assert entry["source"] == "agent:grid_scalper"
    assert entry["action"] == "write"


def test_reindex_never_stale(memory_root):
    s = MemoryStore(user_id=42)
    s.write("One", "a", "first", type="fact")
    s.write("Two", "b", "second", type="fact")
    index = s.list_index()
    assert index.count("- [") == 2

    s.delete("One")
    index = s.list_index()
    assert "one" not in index.lower().replace("none", "")
    assert "two" in index.lower()
    assert index.count("- [") == 1


def test_index_self_heals_when_missing(memory_root):
    s = MemoryStore(user_id=42)
    s.write("One", "a", "first", type="fact")
    # Simulate a lost index file.
    s.index_file.unlink()
    index = s.list_index()
    assert "one" in index.lower()


def test_atomic_write_leaves_no_tmp(memory_root):
    s = MemoryStore(user_id=42)
    s.write("One", "a", "first", type="fact")
    tmp_files = list((memory_root / "user_42" / "memories").glob("*.tmp"))
    assert tmp_files == []
