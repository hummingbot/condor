"""Unit tests for condor.memory.skills.SkillStore."""

import pytest

from condor.memory import skills as skills_module
from condor.memory import store as store_module
from condor.memory.skills import SkillStore
from condor.memory.store import MemoryStore


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """Point both stores' data root at a tmp dir for isolation."""
    root = tmp_path / "memory"
    monkeypatch.setattr(store_module, "_DATA_ROOT", root)
    # skills.py imported _DATA_ROOT by value, so patch its binding too.
    monkeypatch.setattr(skills_module, "_DATA_ROOT", root)
    return root


@pytest.fixture
def fake_routines(monkeypatch):
    """Make `band_scanner` a known global routine; nothing else exists."""
    monkeypatch.setattr(
        skills_module,
        "_routine_exists",
        lambda name: name == "band_scanner",
    )


def test_create_list_read_roundtrip(memory_root, fake_routines):
    s = SkillStore(user_id=42)
    res = s.create(
        name="Grid en band walk",
        description="Cómo abrir un grid cuando el precio camina la banda inferior",
        when_to_use="Precio toca banda inferior 2+ velas y volatilidad < umbral",
        body="1. Correr band_scanner.\n2. Abrir grid.\n3. Journal.",
        references_routine="band_scanner",
    )
    assert res["saved"] is True
    assert res["name"] == "grid_en_band_walk"
    assert res["routine_ok"] is True

    index = s.list_index()
    assert "grid_en_band_walk" in index
    assert "banda inferior" in index
    assert "→ routine: band_scanner" in index

    read = s.read("Grid en band walk")
    assert read is not None
    assert "Abrir grid" in read["body"]
    assert read["references_routine"] == "band_scanner"
    assert read["routine_ok"] is True


def test_read_missing_returns_none(memory_root):
    s = SkillStore(user_id=1)
    assert s.read("does_not_exist") is None


def test_empty_index_is_empty_string(memory_root):
    s = SkillStore(user_id=7)
    assert s.list_index() == ""


def test_broken_routine_reference_marked_not_fatal(memory_root, fake_routines):
    s = SkillStore(user_id=42)
    res = s.create(
        name="Broken ref",
        description="references a routine that does not exist",
        when_to_use="never",
        body="do stuff",
        references_routine="ghost_routine",
    )
    assert res["saved"] is True
    assert res["routine_ok"] is False

    read = s.read("Broken ref")
    assert read["references_routine"] == "ghost_routine"
    assert read["routine_ok"] is False  # marked, but read still works


def test_skill_without_reference_has_no_routine_fields(memory_root):
    s = SkillStore(user_id=42)
    s.create(
        name="Pure playbook",
        description="checklist, no execution",
        when_to_use="before raising leverage",
        body="check 1, check 2",
    )
    read = s.read("Pure playbook")
    assert "references_routine" not in read
    assert "routine_ok" not in read
    assert "→ routine:" not in s.list_index()


def test_search_matches_when_to_use_and_body(memory_root):
    s = SkillStore(user_id=42)
    s.create("Alpha", "desc a", "when alpha condition", "body alpha")
    s.create("Beta", "desc b", "when beta condition", "body beta unique")

    hits = s.search("alpha")
    assert len(hits) == 1
    assert hits[0]["name"] == "alpha"

    hits = s.search("unique")
    assert len(hits) == 1
    assert hits[0]["name"] == "beta"

    assert len(s.search("")) == 2


def test_edit_patches_fields_and_clears_reference(memory_root, fake_routines):
    s = SkillStore(user_id=42)
    s.create(
        "Editable",
        "v1 desc",
        "v1 when",
        "v1 body",
        references_routine="band_scanner",
    )
    edited = s.edit(
        "Editable",
        when_to_use="v2 when",
        body="v2 body",
        references_routine="",  # clear it
    )
    assert edited["when_to_use"] == "v2 when"
    assert "v2 body" in edited["body"]
    assert "references_routine" not in edited
    # description untouched
    assert edited["description"] == "v1 desc"


def test_create_preserves_created_date_on_overwrite(memory_root):
    s = SkillStore(user_id=42)
    s.create("Skill", "d1", "w1", "body1")
    path = memory_root / "user_42" / "skills" / "skill" / "SKILL.md"
    first = path.read_text()
    created_line = [l for l in first.splitlines() if l.startswith("created:")][0]

    s.create("Skill", "d2", "w2", "body2 updated")
    second = path.read_text()
    assert created_line in second  # created unchanged
    assert "body2 updated" in second


def test_delete_removes_folder_and_audits(memory_root):
    s = SkillStore(user_id=42)
    s.create("Temp", "d", "w", "body", source="chat")
    skill_dir = memory_root / "user_42" / "skills" / "temp"
    assert skill_dir.exists()

    assert s.delete("Temp", source="user") is True
    assert s.read("Temp") is None
    assert not skill_dir.exists()  # folder cleaned up
    assert s.delete("Temp") is False  # already gone


def test_audit_shared_with_memory_store(memory_root):
    """Skill writes/deletes land in the SAME audit.log as memories."""
    mem = MemoryStore(user_id=42)
    skl = SkillStore(user_id=42)
    assert skl.audit_file == mem.audit_file

    mem.write("A memory", "body", "a memory desc", source="chat")
    skl.create("A skill", "a skill desc", "when", "steps", source="agent:grid")
    skl.delete("A skill", source="user")

    entries = mem.audit(limit=10)
    targets = [e["target"] for e in entries]
    assert "memory:a_memory" in targets
    assert "skill:a_skill" in targets
    skill_writes = [e for e in entries if e["target"] == "skill:a_skill"]
    actions = {e["action"] for e in skill_writes}
    assert actions == {"write", "delete"}
    write_entry = [e for e in skill_writes if e["action"] == "write"][0]
    assert write_entry["source"] == "agent:grid"


def test_reindex_never_stale(memory_root):
    s = SkillStore(user_id=42)
    s.create("One", "d1", "w1", "b1")
    s.create("Two", "d2", "w2", "b2")
    assert s.list_index().count("- [") == 2

    s.delete("One")
    index = s.list_index()
    assert index.count("- [") == 1
    assert "two" in index.lower()


def test_index_self_heals_when_missing(memory_root):
    s = SkillStore(user_id=42)
    s.create("One", "d", "w", "b")
    s.index_file.unlink()
    assert "one" in s.list_index().lower()


def test_atomic_write_leaves_no_tmp(memory_root):
    s = SkillStore(user_id=42)
    s.create("One", "d", "w", "b")
    tmp_files = list((memory_root / "user_42" / "skills").rglob("*.tmp"))
    assert tmp_files == []
