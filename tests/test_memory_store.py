"""Unit tests for condor.memory.store.MemoryStore."""

from pathlib import Path

import pytest

from condor.memory import paths as paths_module
from condor.memory.paths import store_root
from condor.memory.store import MemoryStore, _atomic_write


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """Point the project root at a tmp dir so stores resolve under it."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    # The chat store (agent_slug=None) is what these per-user tests exercise.
    return tmp_path / "assistants" / "condor" / "store"


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
    assert "create" in actions
    assert "delete" in actions
    delete_entry = [e for e in s.audit() if e["action"] == "delete"][0]
    assert delete_entry["source"] == "user"
    assert delete_entry["target"] == "memory:temp"


def test_audit_log_is_bounded_and_keeps_newest(memory_root, monkeypatch):
    # PERF-043: writing far more than the cap must leave audit.log bounded and
    # preserve the newest entries (the only ones audit() ever returns).
    from condor.memory import store as store_module

    monkeypatch.setattr(store_module, "_AUDIT_CAP", 10)
    s = MemoryStore(user_id=42)
    total = 55  # >> 2 * cap, forces several trims
    for i in range(total):
        s.write("Pair", f"body {i}", f"desc {i}", type="fact")

    lines = s.audit_file.read_text().splitlines()
    # Never grows past the rewrite threshold (2 * cap).
    assert len(lines) <= 2 * store_module._AUDIT_CAP
    # The most recent entry survived the trimming.
    newest = s.audit(limit=1)[-1]
    assert newest["summary"] == f"desc {total - 1}"
    assert newest["action"] == "update"


def test_audit_records_source(memory_root):
    s = MemoryStore(user_id=42)
    s.write("From agent", "body", "desc", source="agent:grid_scalper")
    entry = s.audit()[-1]
    assert entry["source"] == "agent:grid_scalper"
    assert entry["action"] == "create"


def test_audit_distinguishes_create_and_update(memory_root):
    s = MemoryStore(user_id=42)
    s.write("Pair", "v1", "desc v1", type="fact")
    s.write("Pair", "v2", "desc v2", type="fact")  # same slug -> overwrite
    actions = [e["action"] for e in s.audit() if e["target"] == "memory:pair"]
    assert actions == ["create", "update"]


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


def test_atomic_write_uses_unique_tmp_per_writer(memory_root, monkeypatch):
    # Two writes to the same slug must target distinct temp files so concurrent
    # writers (CORR-032) never share — and thus never tear — the temp file.
    seen: list[str] = []
    s = MemoryStore(user_id=42)
    target = s.memories_dir / "one.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    orig = Path.write_text

    def spy(self, text, *args, **kwargs):
        if self.name.endswith(".tmp"):
            seen.append(self.name)
        return orig(self, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    _atomic_write(target, "a")
    _atomic_write(target, "b")

    assert len(seen) == 2
    assert seen[0] != seen[1]  # unique per writer
    assert all(name.endswith(".tmp") for name in seen)


def test_concurrent_writers_never_leave_a_torn_file(memory_root):
    # Many threads writing the same slug concurrently: the published file must
    # always parse cleanly (frontmatter + body), never a torn/interleaved one.
    import threading

    from condor.memory.store import _parse_frontmatter

    s = MemoryStore(user_id=42)
    target = s.memories_dir / "shared.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    payloads = [
        f"---\nname: shared\ncreated: {i}\n---\n\n{'x' * 5000}\n" for i in range(40)
    ]
    barrier = threading.Barrier(len(payloads))

    def writer(text):
        barrier.wait()
        for _ in range(10):
            _atomic_write(target, text)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    meta, body = _parse_frontmatter(target.read_text())
    # The surviving file is exactly one writer's payload, not a blend.
    assert meta.get("name") == "shared"
    assert body == "x" * 5000
    # No temp files survive the storm.
    assert list(target.parent.glob("*.tmp")) == []


# -- per-assistant resolver + isolation (FEAT-003) ----------------------------


def test_resolver_distinct_roots_per_assistant(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    chat = store_root(42, None)
    grid = store_root(42, "grid_scalper")
    ema = store_root(42, "ema_trend_follower")
    assert chat != grid != ema
    assert chat == tmp_path / "assistants" / "condor" / "store" / "user_42"
    assert grid == tmp_path / "agents" / "grid_scalper" / "store" / "user_42"
    # Same (slug, user) is stable across calls.
    assert store_root(42, "grid_scalper") == grid


def test_resolver_user_isolation_within_assistant(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    assert store_root(1, "grid_scalper") != store_root(2, "grid_scalper")


def test_memory_isolated_between_assistants(tmp_path, monkeypatch):
    """A memory written by one assistant is invisible to another."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    grid = MemoryStore(user_id=42, agent_slug="grid_scalper")
    chat = MemoryStore(user_id=42, agent_slug=None)

    grid.write("Grid fact", "only grid knows this", "grid-only", type="fact")

    assert "grid_fact" in grid.list_index()
    assert chat.list_index() == ""  # chat sees nothing
    assert chat.read("Grid fact") is None
    # Round-trip stays inside the grid store.
    assert "only grid knows this" in (grid.read("Grid fact") or "")


def test_audit_logs_are_per_assistant(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    grid = MemoryStore(user_id=42, agent_slug="grid_scalper")
    ema = MemoryStore(user_id=42, agent_slug="ema_trend_follower")

    grid.write("G", "b", "d", source="agent:grid_scalper")
    ema.write("E", "b", "d", source="agent:ema_trend_follower")

    assert grid.audit_file != ema.audit_file
    assert [e["target"] for e in grid.audit()] == ["memory:g"]
    assert [e["target"] for e in ema.audit()] == ["memory:e"]
