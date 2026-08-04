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
    when_to_use,
    description="d",
    body="Steps.",
    references_routine=None,
    shared=False,
):
    """Author a builtin SKILL.md under the right agent home.

    ``shared`` publishes a chat playbook to every agent (FEAT-031); it is the
    frontmatter flag, so it is only meaningful under ``agents/condor``, which is
    where a falsy ``agent_slug`` writes (FEAT-033).
    """
    base = root / "agents" / (agent_slug or "condor") / "skills"
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
    if shared:
        fm.append("shared: true")
    (d / "SKILL.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n")
    return d


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


def test_read_lists_companion_files(project_root):
    """read() surfaces bundled companion files but not SKILL.md or temp files."""
    _write_skill(project_root, None, "pmm_playbook", when_to_use="pick a config")
    skill_dir = SkillStore().skills_dir / "pmm_playbook"
    (skill_dir / "config_aggressive.md").write_text("aggressive body")
    (skill_dir / "config_conservative.md").write_text("conservative body")
    (skill_dir / ".hidden.tmp").write_text("ignore me")

    read = SkillStore().read("pmm_playbook")
    assert read["files"] == ["config_aggressive.md", "config_conservative.md"]


def test_read_omits_files_when_no_companions(project_root):
    _write_skill(project_root, None, "plain", when_to_use="x")
    assert "files" not in SkillStore().read("plain")


def test_read_file_returns_companion_content(project_root):
    _write_skill(project_root, None, "pmm_playbook", when_to_use="pick a config")
    skill_dir = SkillStore().skills_dir / "pmm_playbook"
    (skill_dir / "config_aggressive.md").write_text("tight spreads")

    res = SkillStore().read_file("PMM Playbook", "config_aggressive.md")
    assert res["skill"] == "pmm_playbook"
    assert res["file"] == "config_aggressive.md"
    assert res["content"] == "tight spreads"


def test_read_file_missing_file_lists_available(project_root):
    _write_skill(project_root, None, "pmm_playbook", when_to_use="x")
    skill_dir = SkillStore().skills_dir / "pmm_playbook"
    (skill_dir / "config_balanced.md").write_text("body")

    res = SkillStore().read_file("pmm_playbook", "ghost.md")
    assert "error" in res
    assert res["files"] == ["config_balanced.md"]


def test_read_file_missing_skill_errors(project_root):
    assert "error" in SkillStore().read_file("nope", "x.md")


def test_read_file_rejects_path_traversal(project_root):
    """A companion read must never escape the skill folder."""
    _write_skill(project_root, None, "pmm_playbook", when_to_use="x")
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
        res = s.read_file("pmm_playbook", bad)
        assert "error" in res, bad
        assert "content" not in res, bad


def test_write_file_creates_and_overwrites_companion(project_root):
    """write_file authors a companion beside SKILL.md and read_file reads it back."""
    _write_skill(project_root, None, "pmm_playbook", when_to_use="pick a config")
    s = SkillStore()

    res = s.write_file("PMM Playbook", "config_aggressive.md", "tight spreads")
    assert res["saved"] is True
    assert res["created"] is True
    assert res["skill"] == "pmm_playbook"
    assert res["files"] == ["config_aggressive.md"]
    assert (
        s.read_file("pmm_playbook", "config_aggressive.md")["content"]
        == "tight spreads"
    )

    # Second write to the same name overwrites (created flips to False).
    res2 = s.write_file("pmm_playbook", "config_aggressive.md", "wider spreads")
    assert res2["created"] is False
    assert (
        s.read_file("pmm_playbook", "config_aggressive.md")["content"]
        == "wider spreads"
    )


def test_write_file_missing_skill_errors(project_root):
    assert "error" in SkillStore().write_file("nope", "x.md", "body")


def test_write_file_requires_content(project_root):
    _write_skill(project_root, None, "pmm_playbook", when_to_use="x")
    assert "error" in SkillStore().write_file("pmm_playbook", "x.md", None)


def test_write_file_rejects_skill_md_and_traversal(project_root):
    """A companion write must never clobber SKILL.md or escape the skill folder."""
    _write_skill(project_root, None, "pmm_playbook", when_to_use="x")
    s = SkillStore()
    for bad in ("SKILL.md", "../secret.md", "/etc/passwd", "sub/x.md", ""):
        res = s.write_file("pmm_playbook", bad, "payload")
        assert "error" in res, bad
        assert "saved" not in res, bad
    # The traversal target must not have been created outside the skill folder.
    assert not (s.skills_dir / "secret.md").exists()


# ── inheritance: Condor publishes, agents inherit (FEAT-031) ──


def test_agent_inherits_a_shared_chat_skill(project_root):
    """A `shared: true` chat playbook resolves for an agent, flagged inherited."""
    _write_skill(
        project_root,
        None,
        "routine_cookbook",
        when_to_use="before writing a routine",
        body="The cookbook.",
        shared=True,
    )
    agent = SkillStore("backpack_mm")

    read = agent.read("routine_cookbook")
    assert read is not None
    assert read["inherited"] is True
    assert "The cookbook." in read["body"]
    assert "[routine_cookbook] before writing a routine" in agent.list_index()
    assert [h["name"] for h in agent.search("cookbook")] == ["routine_cookbook"]


def test_unflagged_chat_skill_is_not_found_for_an_agent(project_root):
    """Unpublished is INVISIBLE, not merely unlisted — no body on request."""
    _write_skill(project_root, None, "agent_builder", when_to_use="build an agent")
    agent = SkillStore("backpack_mm")

    assert agent.read("agent_builder") is None
    assert "agent_builder" not in agent.list_index()
    assert agent.search("build an agent") == []
    assert "error" in agent.read_file("agent_builder", "x.md")


def test_chat_inherits_nothing_and_still_reads_its_own(project_root):
    """The chat IS the publisher: its own dir is the whole truth for it."""
    _write_skill(project_root, None, "published", when_to_use="x", shared=True)
    chat = SkillStore()

    assert chat.inherited_dir is None
    assert chat.read("published")["name"] == "published"
    assert "inherited" not in chat.read("published")
    assert chat.list_index().count("[published]") == 1  # not listed twice


def test_local_skill_shadows_the_inherited_one(project_root):
    """Own library wins — that is how an agent specializes a playbook."""
    _write_skill(
        project_root,
        None,
        "routine_cookbook",
        when_to_use="chat trigger",
        body="chat body",
        shared=True,
    )
    _write_skill(
        project_root,
        "backpack_mm",
        "routine_cookbook",
        when_to_use="agent trigger",
        body="agent body",
    )
    agent = SkillStore("backpack_mm")

    read = agent.read("routine_cookbook")
    assert read["body"] == "agent body"
    assert "inherited" not in read
    index = agent.list_index()
    assert "agent trigger" in index
    assert "chat trigger" not in index
    assert index.count("[routine_cookbook]") == 1
    assert [h["body"] for h in agent.search("body")] == ["agent body"]
    # Condor's file on disk is untouched.
    assert SkillStore().read("routine_cookbook")["body"] == "chat body"


def test_inherited_companion_file_is_readable(project_root):
    """Progressive disclosure survives the inherited root."""
    d = _write_skill(
        project_root, None, "routine_cookbook", when_to_use="x", shared=True
    )
    (d / "report_builder.md").write_text("ReportBuilder patterns")

    agent = SkillStore("backpack_mm")
    assert agent.read("routine_cookbook")["files"] == ["report_builder.md"]
    res = agent.read_file("routine_cookbook", "report_builder.md")
    assert res["content"] == "ReportBuilder patterns"


def test_inherited_companion_read_still_rejects_traversal(project_root):
    """The traversal guard anchors on the RESOLVED dir, inherited included."""
    _write_skill(project_root, None, "routine_cookbook", when_to_use="x", shared=True)
    _write_skill(project_root, None, "agent_builder", when_to_use="private")
    chat_root = SkillStore().skills_dir
    (chat_root / "secret.md").write_text("top secret")

    agent = SkillStore("backpack_mm")
    for bad in (
        "../secret.md",
        "../agent_builder/SKILL.md",
        "..%2fsecret.md",
        "/etc/passwd",
        "sub/x.md",
        "SKILL.md",
    ):
        res = agent.read_file("routine_cookbook", bad)
        assert "error" in res, bad
        assert "content" not in res, bad


def test_writes_to_an_inherited_skill_are_refused(project_root):
    """edit/delete/write_file on an inherited-only slug error and write nothing."""
    d = _write_skill(
        project_root,
        None,
        "routine_cookbook",
        when_to_use="chat trigger",
        body="chat body",
        shared=True,
    )
    agent = SkillStore("backpack_mm")

    for res in (
        agent.edit("routine_cookbook", body="hijacked"),
        agent.delete("routine_cookbook"),
        agent.write_file("routine_cookbook", "new.md", "hijacked"),
    ):
        assert "error" in res
        assert "read-only" in res["error"]
        assert "Create a local skill" in res["error"]

    # Condor's library is intact: body unchanged, no companion planted.
    assert (d / "SKILL.md").read_text().count("chat body") == 1
    assert not (d / "new.md").exists()
    assert SkillStore().read("routine_cookbook")["body"] == "chat body"


def test_create_shadows_an_inherited_skill_locally(project_root):
    """create() is the sanctioned way to specialize: it writes to the OWN dir."""
    _write_skill(
        project_root,
        None,
        "routine_cookbook",
        when_to_use="chat",
        body="chat body",
        shared=True,
    )
    agent = SkillStore("backpack_mm")

    res = agent.create("routine_cookbook", "mine", "my trigger", "my body")
    assert res["saved"] is True
    assert "shared" not in res  # an agent cannot publish
    assert agent.read("routine_cookbook")["body"] == "my body"
    assert SkillStore().read("routine_cookbook")["body"] == "chat body"
    # Now that it is local, editing it works again.
    assert agent.edit("routine_cookbook", body="edited")["body"] == "edited"


def test_publishing_is_chat_only_and_survives_overwrite(project_root):
    """shared=True publishes from the chat, is ignored for an agent, and sticks."""
    chat = SkillStore()
    assert chat.create("cookbook", "d", "w", "body", shared=True)["shared"] is True
    assert SkillStore("backpack_mm").read("cookbook") is not None

    # Re-creating without the flag must not silently unpublish it.
    chat.create("cookbook", "d2", "w2", "body2")
    assert SkillStore("backpack_mm").read("cookbook")["body"] == "body2"

    # Explicit unpublish takes it back out of every agent's reach.
    chat.edit("cookbook", shared=False)
    assert SkillStore("backpack_mm").read("cookbook") is None

    # An agent flagging its own skill publishes nothing.
    agent = SkillStore("brigado")
    assert "shared" not in agent.create("local", "d", "w", "b", shared=True)
    assert SkillStore("backpack_mm").read("local") is None


def test_create_requires_all_fields(project_root):
    err = SkillStore().create("only_name", "", "", "")
    assert "error" in err


def test_edit_and_delete_missing_skill(project_root):
    s = SkillStore()
    assert "error" in s.edit("ghost", description="x")
    assert s.delete("ghost") is False


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
