"""Unit tests for condor.memory.skills.SkillStore (editable skill library).

Skills are general to the assistant: a shared playbook library beside the
assistant's definition, NOT learned per user. The store reads, searches, indexes
and edits (create/edit/delete) them.
"""

from pathlib import Path

import pytest

from condor.memory import paths as paths_module
from condor.memory import skills as skills_module
from condor.memory.skills import SkillStore
from condor.memory.store import _atomic_write


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
        lambda name, agent_slug=None: name == "band_scanner",
    )


def _write_skill(
    root,
    agent_slug,
    slug,
    *,
    description="d",
    body="Steps.",
    references_routine=None,
):
    """Author a builtin spec-shaped SKILL.md under the right skills root."""
    import json

    if agent_slug:
        base = root / "agents" / agent_slug / "skills"
    else:
        base = root / "skills"  # host-facing root (refactor-05 Phase 1)
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = [
        f"name: {slug}",
        f"description: {json.dumps(description)}",
    ]
    md = {"condor-source": "builtin"}
    if references_routine:
        md["condor-references-routine"] = references_routine
    fm.append(f"metadata: {json.dumps(md)}")
    (d / "SKILL.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n")


def test_read_returns_builtin_with_flag(project_root, fake_routines):
    _write_skill(
        project_root,
        None,
        "grid-en-band-walk",
        description="Abrir grid. Use when precio toca banda inferior.",
        body="1. Correr band_scanner.\n2. Abrir grid.",
        references_routine="band_scanner",
    )
    s = SkillStore()
    read = s.read("Grid en band walk")
    assert read is not None
    assert read["name"] == "grid-en-band-walk"
    assert "Abrir grid" in read["body"]
    assert read["references_routine"] == "band_scanner"
    assert read["routine_ok"] is True


def test_list_index_lists_builtins(project_root, fake_routines):
    _write_skill(
        project_root,
        None,
        "grid-en-band-walk",
        description="banda inferior",
        references_routine="band_scanner",
    )
    index = SkillStore().list_index()
    assert "[grid-en-band-walk] banda inferior" in index
    assert "→ routine: band_scanner" in index


def test_read_missing_returns_none(project_root):
    assert SkillStore().read("does_not_exist") is None


def test_empty_index_is_empty_string(project_root):
    assert SkillStore().list_index() == ""


def test_broken_routine_reference_marked_not_fatal(project_root, fake_routines):
    _write_skill(
        project_root,
        None,
        "broken-ref",
        references_routine="ghost_routine",
    )
    read = SkillStore().read("broken-ref")
    assert read["references_routine"] == "ghost_routine"
    assert read["routine_ok"] is False  # marked, but read still works


def test_skill_without_reference_has_no_routine_fields(project_root):
    _write_skill(
        project_root, None, "pure-playbook", description="before raising leverage"
    )
    s = SkillStore()
    read = s.read("Pure playbook")
    assert "references_routine" not in read
    assert "routine_ok" not in read
    assert "→ routine:" not in s.list_index()


def test_search_matches_description_and_body(project_root):
    _write_skill(
        project_root,
        None,
        "alpha",
        description="when alpha condition",
        body="body alpha",
    )
    _write_skill(
        project_root,
        None,
        "beta",
        description="when beta condition",
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
    _write_skill(project_root, None, "chat-only", description="chat")
    _write_skill(project_root, "executor_manager", "agent-only", description="agent")

    chat = SkillStore()
    agent = SkillStore("executor_manager")
    assert "chat-only" in chat.list_index()
    assert "agent-only" not in chat.list_index()
    assert "agent-only" in agent.list_index()
    assert chat.read("agent-only") is None
    assert agent.read("agent-only") is not None


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
        description="Abrir grid en banda inferior. Use when precio toca banda.",
        body="1. Correr band_scanner.\n2. Abrir grid.",
        references_routine="band_scanner",
    )
    assert res["saved"] is True
    assert res["name"] == "grid-en-band-walk"
    assert res["routine_ok"] is True
    assert "[grid-en-band-walk] Abrir grid en banda inferior" in s.list_index()

    edited = s.edit("grid-en-band-walk", description="updated", body="new steps")
    assert edited["description"] == "updated"
    assert "new steps" in s.read("grid-en-band-walk")["body"]

    assert s.delete("Grid en band walk")["deleted"] is True
    assert s.read("grid-en-band-walk") is None
    assert s.list_index() == ""


def test_read_lists_companion_files(project_root):
    """read() surfaces bundled companion files but not SKILL.md or temp files."""
    _write_skill(project_root, None, "pmm-playbook", description="pick a config")
    skill_dir = SkillStore().skills_dir / "pmm-playbook"
    (skill_dir / "config_aggressive.md").write_text("aggressive body")
    (skill_dir / "config_conservative.md").write_text("conservative body")
    (skill_dir / ".hidden.tmp").write_text("ignore me")

    read = SkillStore().read("pmm-playbook")
    assert read["files"] == ["config_aggressive.md", "config_conservative.md"]


def test_read_omits_files_when_no_companions(project_root):
    _write_skill(project_root, None, "plain", description="x")
    assert "files" not in SkillStore().read("plain")


def test_read_file_returns_companion_content(project_root):
    _write_skill(project_root, None, "pmm-playbook", description="pick a config")
    skill_dir = SkillStore().skills_dir / "pmm-playbook"
    (skill_dir / "config_aggressive.md").write_text("tight spreads")

    res = SkillStore().read_file("PMM Playbook", "config_aggressive.md")
    assert res["skill"] == "pmm-playbook"
    assert res["file"] == "config_aggressive.md"
    assert res["content"] == "tight spreads"


def test_read_file_missing_file_lists_available(project_root):
    _write_skill(project_root, None, "pmm-playbook", description="x")
    skill_dir = SkillStore().skills_dir / "pmm-playbook"
    (skill_dir / "config_balanced.md").write_text("body")

    res = SkillStore().read_file("pmm-playbook", "ghost.md")
    assert "error" in res
    assert res["files"] == ["config_balanced.md"]


def test_read_file_missing_skill_errors(project_root):
    assert "error" in SkillStore().read_file("nope", "x.md")


def test_read_file_rejects_path_traversal(project_root):
    """A companion read must never escape the skill folder."""
    _write_skill(project_root, None, "pmm-playbook", description="x")
    # Plant a secret beside the skills dir to prove it stays unreachable.
    (SkillStore().skills_dir / "secret.md").write_text("top secret")

    s = SkillStore()
    for bad in (
        "../secret.md",
        "..%2fsecret.md",
        "/etc/passwd",
        "sub/x.md",
        "SKILL.md",
    ):
        res = s.read_file("pmm-playbook", bad)
        assert "error" in res, bad
        assert "content" not in res, bad


def test_write_file_creates_and_overwrites_companion(project_root):
    """write_file authors a companion beside SKILL.md and read_file reads it back."""
    _write_skill(project_root, None, "pmm-playbook", description="pick a config")
    s = SkillStore()

    res = s.write_file("PMM Playbook", "config_aggressive.md", "tight spreads")
    assert res["saved"] is True
    assert res["created"] is True
    assert res["skill"] == "pmm-playbook"
    assert res["files"] == ["config_aggressive.md"]
    assert s.read_file("pmm-playbook", "config_aggressive.md")["content"] == "tight spreads"

    # Second write to the same name overwrites (created flips to False).
    res2 = s.write_file("pmm-playbook", "config_aggressive.md", "wider spreads")
    assert res2["created"] is False
    assert s.read_file("pmm-playbook", "config_aggressive.md")["content"] == "wider spreads"


def test_write_file_missing_skill_errors(project_root):
    assert "error" in SkillStore().write_file("nope", "x.md", "body")


def test_write_file_requires_content(project_root):
    _write_skill(project_root, None, "pmm-playbook", description="x")
    assert "error" in SkillStore().write_file("pmm-playbook", "x.md", None)


def test_write_file_rejects_skill_md_and_traversal(project_root):
    """A companion write must never clobber SKILL.md or escape the skill folder."""
    _write_skill(project_root, None, "pmm-playbook", description="x")
    s = SkillStore()
    for bad in ("SKILL.md", "../secret.md", "/etc/passwd", "sub/x.md", ""):
        res = s.write_file("pmm-playbook", bad, "payload")
        assert "error" in res, bad
        assert "saved" not in res, bad
    # The traversal target must not have been created outside the skill folder.
    assert not (s.skills_dir / "secret.md").exists()


def test_create_requires_all_fields(project_root):
    err = SkillStore().create("only_name", "", "")
    assert "error" in err


def test_edit_and_delete_missing_skill(project_root):
    s = SkillStore()
    assert "error" in s.edit("ghost", description="x")
    assert "error" in s.delete("ghost")


def test_atomic_write_uses_unique_tmp_per_writer(project_root, monkeypatch):
    # Two writes to the same slug must target distinct temp files so concurrent
    # writers (CORR-032) never share — and thus never tear — the temp file.
    seen: list[str] = []
    s = SkillStore()
    target = s.skills_dir / "one" / "SKILL.md"
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


def test_concurrent_writers_never_leave_a_torn_file(project_root):
    # Many threads writing the same skill concurrently: the published file must
    # always parse cleanly, never a torn/interleaved one.
    import threading

    from condor.memory.store import _parse_frontmatter

    s = SkillStore()
    target = s.skills_dir / "shared" / "SKILL.md"
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
    assert meta.get("name") == "shared"
    assert body == "x" * 5000
    assert list(target.parent.glob("*.tmp")) == []


# ── shared tier (refactor-05 Phase 2) ──


def _write_shared(root, slug, *, description="shared d", body="Shared steps."):
    import json

    d = root / "skills" / slug  # the shared tier is the repo-root skills/ library
    d.mkdir(parents=True, exist_ok=True)
    fm = [
        f"name: {slug}",
        f"description: {json.dumps(description)}",
        'metadata: {"condor-source": "shared"}',
    ]
    (d / "SKILL.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n")


def test_agent_reads_shared_tier(project_root):
    _write_shared(project_root, "executor-mechanics")
    _write_skill(project_root, "mm_expert", "local-only", description="local")

    agent = SkillStore("mm_expert")
    assert "executor-mechanics" in agent.list_index()
    assert "local-only" in agent.list_index()
    read = agent.read("executor-mechanics")
    assert read is not None
    assert read["tier"] == "shared"

    # The chat's own library now IS the shared tier (same repo-root skills/)...
    assert "executor-mechanics" in SkillStore().list_index()
    # ...and the scope='shared' management handle points at that same dir.
    shared = SkillStore(scope="shared")
    assert "executor-mechanics" in shared.list_index()


def test_local_shadows_shared_on_name_clash(project_root):
    _write_shared(project_root, "venue-quirks", description="shared version")
    _write_skill(project_root, "mm_expert", "venue-quirks", description="local version")

    agent = SkillStore("mm_expert")
    assert agent.read("venue-quirks")["description"] == "local version"
    assert "local version" in agent.list_index()
    assert "shared version" not in agent.list_index()


def test_shared_tier_read_only_for_agents(project_root):
    _write_shared(project_root, "executor-mechanics")
    agent = SkillStore("mm_expert")

    for res in (
        agent.edit("executor-mechanics", description="hacked"),
        agent.delete("executor-mechanics"),
        agent.write_file("executor-mechanics", "extra.md", "payload"),
    ):
        assert "error" in res
        assert "read-only" in res["error"]
    # And the shared file is untouched.
    assert SkillStore(scope="shared").read("executor-mechanics")["description"] == "shared d"

    # An agent CREATE of the same name lands locally (shadowing, not mutation).
    created = agent.create("executor-mechanics", "local override. Use when local.", "steps")
    assert created["saved"] is True
    assert agent.read("executor-mechanics").get("tier") is None  # local now
    assert SkillStore(scope="shared").read("executor-mechanics")["description"] == "shared d"


def test_shared_scope_is_writable_from_chat(project_root):
    shared = SkillStore(scope="shared")
    res = shared.create("venue-quirks", "d. Use when venue weirdness.", "steps")
    assert res["saved"] is True
    assert "venue-quirks" in SkillStore("any_agent").list_index()
    assert shared.delete("venue-quirks")["deleted"] is True


def test_shared_scope_and_agent_slug_mutually_exclusive(project_root):
    with pytest.raises(ValueError):
        SkillStore(agent_slug="x", scope="shared")
    with pytest.raises(ValueError):
        SkillStore(scope="bogus")


# ── patch (delta edits, refactor-05 Phase 3) ──


def test_patch_applies_delta_with_provenance(project_root):
    _write_skill(project_root, "mm_expert", "grid-rules",
                 description="d", body="Spreads: 0.001 floor.\nLeverage: 5 max.")
    s = SkillStore("mm_expert")
    res = s.patch(
        "grid-rules",
        "Spreads: 0.001 floor.",
        "Spreads: 0.0012 floor (fees ate 0.001 twice).",
        changelog="raise spread floor per fee learnings",
        updated_by="agent:mm_expert",
    )
    assert res["patched"] is True
    body = s.read("grid-rules")["body"]
    assert "0.0012 floor" in body
    assert "Leverage: 5 max." in body  # untouched remainder
    # Provenance stamped in metadata (visible in the raw file)
    raw = (project_root / "agents" / "mm_expert" / "skills" / "grid-rules" / "SKILL.md").read_text()
    assert "condor-updated-by" in raw
    assert "raise spread floor" in raw

    # A second patch appends to the changelog rather than replacing it.
    s.patch("grid-rules", "Leverage: 5 max.", "Leverage: 3 max.",
            changelog="cut leverage", updated_by="agent:mm_expert")
    raw = (project_root / "agents" / "mm_expert" / "skills" / "grid-rules" / "SKILL.md").read_text()
    assert "raise spread floor" in raw and "cut leverage" in raw


def test_patch_guards(project_root):
    _write_skill(project_root, "mm_expert", "grid-rules", description="d",
                 body="alpha beta alpha")
    _write_shared(project_root, "executor-mechanics")
    s = SkillStore("mm_expert")

    assert "error" in s.patch("ghost", "x", "y", changelog="c")
    assert "not found" in s.patch("grid-rules", "zeta", "y", changelog="c")["error"]
    assert "matches 2" in s.patch("grid-rules", "alpha", "y", changelog="c")["error"]
    assert "changelog" in s.patch("grid-rules", "beta", "y", changelog="")["error"]
    assert "read-only" in s.patch("executor-mechanics", "Shared", "x", changelog="c")["error"]


def test_shared_skills_marked_in_index(project_root):
    _write_shared(project_root, "executor-mechanics")
    _write_skill(project_root, "mm_expert", "local-skill", description="d")
    index = SkillStore("mm_expert").list_index()
    assert "[executor-mechanics]" in index and "[shared — read-only]" in index
    local_line = [l for l in index.splitlines() if "local-skill" in l][0]
    assert "[shared" not in local_line
