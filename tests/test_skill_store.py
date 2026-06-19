"""Unit tests for condor.memory.skills.SkillStore (editable skill library).

Skills are general to the assistant: a shared playbook library beside the
assistant's definition, NOT learned per user. The store reads, searches, indexes
and edits (create/edit/delete) them.
"""

import pytest

from condor.memory import paths as paths_module
from condor.memory import skills as skills_module
from condor.memory.skills import SkillStore


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """Point the project root at a tmp dir so builtin skills resolve under it."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def fake_routines(monkeypatch):
    """Make `band_scanner` a known global routine; nothing else exists."""
    monkeypatch.setattr(
        skills_module,
        "_routine_exists",
        lambda name: name == "band_scanner",
    )


def _write_skill(
    root,
    agent_slug,
    slug,
    *,
    when_to_use,
    description="d",
    body="Steps.",
    references_routine=None,
):
    """Author a builtin SKILL.md under the right assistant home."""
    if agent_slug:
        base = root / "trading_agents" / agent_slug / "skills"
    else:
        base = root / "assistants" / "condor" / "skills"
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = [
        f"name: {slug}",
        f"description: {description}",
        f"when_to_use: {when_to_use}",
        "source: builtin",
    ]
    if references_routine:
        fm.append(f"references_routine: {references_routine}")
    (d / "SKILL.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n")


def test_read_returns_builtin_with_flag(project_root, fake_routines):
    _write_skill(
        project_root,
        None,
        "grid_en_band_walk",
        when_to_use="Precio toca banda inferior",
        body="1. Correr band_scanner.\n2. Abrir grid.",
        references_routine="band_scanner",
    )
    s = SkillStore()
    read = s.read("Grid en band walk")
    assert read is not None
    assert read["name"] == "grid_en_band_walk"
    assert "Abrir grid" in read["body"]
    assert read["references_routine"] == "band_scanner"
    assert read["routine_ok"] is True


def test_list_index_lists_builtins(project_root, fake_routines):
    _write_skill(
        project_root,
        None,
        "grid_en_band_walk",
        when_to_use="banda inferior",
        references_routine="band_scanner",
    )
    index = SkillStore().list_index()
    assert "[grid_en_band_walk] banda inferior" in index
    assert "→ routine: band_scanner" in index


def test_read_missing_returns_none(project_root):
    assert SkillStore().read("does_not_exist") is None


def test_empty_index_is_empty_string(project_root):
    assert SkillStore().list_index() == ""


def test_broken_routine_reference_marked_not_fatal(project_root, fake_routines):
    _write_skill(
        project_root,
        None,
        "broken_ref",
        when_to_use="never",
        references_routine="ghost_routine",
    )
    read = SkillStore().read("broken_ref")
    assert read["references_routine"] == "ghost_routine"
    assert read["routine_ok"] is False  # marked, but read still works


def test_skill_without_reference_has_no_routine_fields(project_root):
    _write_skill(
        project_root, None, "pure_playbook", when_to_use="before raising leverage"
    )
    s = SkillStore()
    read = s.read("Pure playbook")
    assert "references_routine" not in read
    assert "routine_ok" not in read
    assert "→ routine:" not in s.list_index()


def test_search_matches_when_to_use_and_body(project_root):
    _write_skill(
        project_root,
        None,
        "alpha",
        when_to_use="when alpha condition",
        body="body alpha",
    )
    _write_skill(
        project_root,
        None,
        "beta",
        when_to_use="when beta condition",
        body="body beta unique",
    )
    s = SkillStore()

    hits = s.search("alpha")
    assert len(hits) == 1
    assert hits[0]["name"] == "alpha"

    hits = s.search("unique")
    assert len(hits) == 1
    assert hits[0]["name"] == "beta"

    assert len(s.search("")) == 2


def test_per_agent_libraries_are_isolated(project_root):
    """A trading agent's skills are separate from the chat's."""
    _write_skill(project_root, None, "chat_only", when_to_use="chat")
    _write_skill(project_root, "executor_manager", "agent_only", when_to_use="agent")

    chat = SkillStore()
    agent = SkillStore("executor_manager")
    assert "chat_only" in chat.list_index()
    assert "agent_only" not in chat.list_index()
    assert "agent_only" in agent.list_index()
    assert chat.read("agent_only") is None
    assert agent.read("agent_only") is not None


def test_assistant_without_skills_is_empty(project_root):
    """A trading agent that ships no skills dir indexes to nothing."""
    s = SkillStore("some_agent_with_no_skills")
    assert s.list_index() == ""
    assert s.search("anything") == []
    assert s.read("anything") is None


def test_create_edit_delete_roundtrip(project_root, fake_routines):
    """The library is editable at runtime: create -> edit -> delete."""
    s = SkillStore()  # chat condor library

    res = s.create(
        "Grid en band walk",
        description="Abrir grid en banda inferior",
        when_to_use="Precio toca banda inferior",
        body="1. Correr band_scanner.\n2. Abrir grid.",
        references_routine="band_scanner",
    )
    assert res["saved"] is True
    assert res["name"] == "grid_en_band_walk"
    assert res["routine_ok"] is True
    assert "[grid_en_band_walk] Precio toca banda inferior" in s.list_index()

    edited = s.edit("grid_en_band_walk", description="updated", body="new steps")
    assert edited["description"] == "updated"
    assert "new steps" in s.read("grid_en_band_walk")["body"]

    assert s.delete("Grid en band walk") is True
    assert s.read("grid_en_band_walk") is None
    assert s.list_index() == ""


def test_create_requires_all_fields(project_root):
    err = SkillStore().create("only_name", "", "", "")
    assert "error" in err


def test_edit_and_delete_missing_skill(project_root):
    s = SkillStore()
    assert "error" in s.edit("ghost", description="x")
    assert s.delete("ghost") is False
