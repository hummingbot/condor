"""FEAT-038: ``agents/_shared/routines`` is a library every assistant inherits.

The routine mirror of FEAT-031's shared skills, and the same three rules: every
assistant reads its own library **plus** the shared one, the own library
**shadows** a shared name, and only the chat may **write** the shared one.

The merge lives inside ``discover_routines`` on purpose — that keeps
``source="global"`` true for a shared routine, which is what makes
``_store_name``, ``RoutineStore._resolve_routine``, the dock filter and report
attribution work on it with no edits of their own. Several tests below assert
exactly that pass-through.

Isolation note: the shared root and the agent dirs are redirected at a tmp tree,
but the *chat's own* library stays the repo's real ``routines/`` — its modules
are imported as the ``routines`` package, so it cannot be relocated. Assertions
are written against the real catalog rather than a fixed list of names.
"""

import pytest

import condor.memory.paths as paths_mod
import routines.base as base
from condor.memory.paths import CHAT_SLUG
from routines.base import assistant_routines, assistant_routines_dir, discover_routines

ROUTINE_TEMPLATE = '''
from pydantic import BaseModel


class Config(BaseModel):
    """{desc}"""
    value: int = 1


async def run(config, context):
    return "ok"
'''


def _write(dir_path, name, desc="a routine"):
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{name}.py"
    path.write_text(ROUTINE_TEMPLATE.format(desc=desc))
    return path


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Shared root + agent dirs under ``tmp_path``, with all caches emptied.

    Yields ``(tmp_path, baseline)`` where ``baseline`` is the chat's own library
    (the real ``routines/``) as seen with an *absent* shared root — the "nothing
    changes when the shared dir is empty" reference every test measures against.
    """
    monkeypatch.setattr(base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(base, "_routines_cache", None)
    monkeypatch.setattr(base, "_routines_mtimes", {})
    monkeypatch.setattr(base, "_path_caches", {})

    assert not paths_mod.shared_routines_root().exists()
    baseline = set(discover_routines(force_reload=True))
    assert baseline, "the repo's routines/ should not be empty"
    return tmp_path, baseline


def _a_chat_routine(baseline: set[str]) -> str:
    """Any routine from the chat's own library, to test against by name."""
    return sorted(baseline)[0]


def _seat(monkeypatch, slug: str) -> None:
    """Run the MCP tools from ``slug``'s seat — ``CHAT_SLUG`` is the chat.

    ``specialist_slug`` is derived, not stored: the chat's subprocess is
    launched with ``--agent-slug condor`` and the property maps that back to
    "no specialist" (FEAT-033), so the seat is set on ``agent_slug``.
    """
    from mcp_servers.condor.tools import routines as mcp_routines

    monkeypatch.setattr(mcp_routines.settings, "agent_slug", slug)


# ── The general library: root routines/ + shared ──────────────────────────────


class TestGeneralLibrary:
    def test_shared_routine_is_part_of_the_general_library(self, isolated):
        _, baseline = isolated
        _write(paths_mod.shared_routines_root(), "published", desc="Published one")

        found = discover_routines(force_reload=True)

        # Exactly one addition: an absent shared root changed nothing.
        assert set(found) == baseline | {"published"}
        # Bare name + source="global" is what every downstream consumer keys on.
        assert found["published"].name == "published"
        assert found["published"].source == "global"
        assert found["published"].description == "Published one"

    def test_root_routine_shadows_a_shared_one_of_the_same_name(self, isolated):
        _, baseline = isolated
        clash = _a_chat_routine(baseline)
        _write(paths_mod.shared_routines_root(), clash, desc="SHARED VERSION")

        found = discover_routines(force_reload=True)

        assert set(found) == baseline
        assert found[clash].description != "SHARED VERSION"

    def test_unchanged_shared_file_is_not_reimported(self, isolated):
        _write(paths_mod.shared_routines_root(), "cached")

        first = discover_routines(force_reload=True)
        second = discover_routines()

        # The same object back means the shared root rode its mtime cache,
        # exactly like the per-agent dirs do.
        assert second["cached"] is first["cached"]

    def test_deleted_shared_file_is_dropped(self, isolated):
        _, baseline = isolated
        path = _write(paths_mod.shared_routines_root(), "temporary")
        assert "temporary" in discover_routines(force_reload=True)

        path.unlink()
        assert set(discover_routines()) == baseline


# ── An assistant's runnable set ───────────────────────────────────────────────


class TestAssistantRoutines:
    def test_agent_inherits_the_shared_library(self, isolated):
        _write(paths_mod.shared_routines_root(), "published")
        _write(assistant_routines_dir("scalper"), "own")

        available = assistant_routines("scalper")

        assert set(available) == {"published", "own"}
        assert available["published"].source == "global"
        assert available["own"].source == "agent:scalper"

    def test_agent_does_not_inherit_the_chats_own_library(self, isolated):
        _, baseline = isolated
        _write(paths_mod.shared_routines_root(), "published")

        available = assistant_routines("scalper")

        assert set(available) == {"published"}
        assert _a_chat_routine(baseline) not in available

    def test_own_routine_shadows_the_shared_one(self, isolated):
        _write(paths_mod.shared_routines_root(), "probe", desc="SHARED VERSION")
        _write(assistant_routines_dir("scalper"), "probe", desc="specialized")

        available = assistant_routines("scalper")

        assert available["probe"].description == "specialized"
        # The shared file on disk is untouched — specializing is shadowing.
        assert (
            "SHARED VERSION"
            in (paths_mod.shared_routines_root() / "probe.py").read_text()
        )

    def test_chat_slug_resolves_the_general_library(self, isolated):
        _, baseline = isolated
        _write(paths_mod.shared_routines_root(), "published")

        for slug in (None, "", "condor"):
            assert set(assistant_routines(slug, force_reload=True)) == baseline | {
                "published"
            }


# ── _shared is never an agent ─────────────────────────────────────────────────


class TestSharedIsNotAnAgent:
    def test_discover_all_never_yields_a_shared_owner(self, isolated, monkeypatch):
        import condor.routine_store as rs_module
        from condor.routine_store import RoutineStore

        tmp_path, _ = isolated
        _write(paths_mod.shared_routines_root(), "published")
        _write(tmp_path / "agents" / "scalper" / "routines", "own")
        monkeypatch.setattr(
            rs_module, "__file__", str(tmp_path / "condor" / "routine_store.py")
        )

        names = set(RoutineStore()._discover_all())

        assert "scalper/own" in names
        assert not any(n.startswith("_shared") for n in names)
        # A shared routine arrives through the general library instead — un-prefixed.
        assert "published" in names

    def test_store_resolves_a_shared_routine_by_its_bare_name(self, isolated):
        from condor.routine_store import RoutineStore

        _write(paths_mod.shared_routines_root(), "published")

        assert RoutineStore()._resolve_routine("published") is not None


# ── The MCP tools ─────────────────────────────────────────────────────────────


class TestMcpTools:
    def test_agent_resolves_and_lists_a_shared_routine(self, isolated, monkeypatch):
        from mcp_servers.condor.tools import routines as mcp_routines

        _seat(monkeypatch, "scalper")
        _write(paths_mod.shared_routines_root(), "published", desc="Published one")
        _write(assistant_routines_dir("scalper"), "own", desc="Own one")

        assert mcp_routines._resolve_routine("published") is not None

        listed = {r["name"]: r for r in mcp_routines.list_routines()["routines"]}
        assert set(listed) == {"published", "own"}
        assert listed["published"]["scope"] == "shared"
        assert "agent" not in listed["published"]
        assert listed["own"]["scope"] == "agent"
        assert listed["own"]["agent"] == "scalper"

    def test_agent_own_routine_shadows_shared_in_run_and_get_info(
        self, isolated, monkeypatch
    ):
        from mcp_servers.condor.tools import routines as mcp_routines

        _seat(monkeypatch, "scalper")
        _write(paths_mod.shared_routines_root(), "probe", desc="SHARED VERSION")
        _write(assistant_routines_dir("scalper"), "probe", desc="specialized")

        assert mcp_routines._resolve_routine("probe").description == "specialized"
        assert mcp_routines.describe_routine("probe")["description"] == "specialized"

    def test_shared_routine_run_by_an_agent_keeps_its_bare_store_name(
        self, isolated, monkeypatch
    ):
        """The dock and RoutineStore know a shared routine by its bare name, and
        its report is attributed to the agent that ran it, not to condor."""
        from mcp_servers.condor.tools import routines as mcp_routines

        _seat(monkeypatch, "scalper")
        _write(paths_mod.shared_routines_root(), "published")

        routine, owner = mcp_routines._resolve_with_owner("published", None)

        assert routine is not None
        assert mcp_routines._store_name(routine, "published") == "published"
        assert owner == "scalper"

    def test_agent_create_routine_never_lands_in_the_shared_root(
        self, isolated, monkeypatch
    ):
        from mcp_servers.condor.tools import routines as mcp_routines

        _seat(monkeypatch, "scalper")

        result = mcp_routines.create_routine(
            "mine", ROUTINE_TEMPLATE.format(desc="From the agent"), None, shared=True
        )

        assert result.get("created") is True
        assert "shared" not in result
        assert (assistant_routines_dir("scalper") / "mine.py").exists()
        assert not (paths_mod.shared_routines_root() / "mine.py").exists()

    def test_chat_publishes_into_the_shared_root(self, isolated, monkeypatch):
        from mcp_servers.condor.tools import routines as mcp_routines

        tmp_path, baseline = isolated
        _seat(monkeypatch, CHAT_SLUG)

        result = mcp_routines.create_routine(
            "published",
            ROUTINE_TEMPLATE.format(desc="From the chat"),
            None,
            shared=True,
        )

        assert result["created"] is True
        assert result["shared"] is True
        assert (paths_mod.shared_routines_root() / "published.py").exists()
        assert not (tmp_path / "routines" / "published.py").exists()
        # And it is immediately part of the general library.
        assert set(discover_routines(force_reload=True)) == baseline | {"published"}

    def test_chat_create_without_shared_stays_local(self, isolated, monkeypatch):
        from mcp_servers.condor.tools import routines as mcp_routines

        tmp_path, _ = isolated
        _seat(monkeypatch, CHAT_SLUG)

        result = mcp_routines.create_routine(
            "local", ROUTINE_TEMPLATE.format(desc="Chat local"), None
        )

        assert result["created"] is True
        assert "shared" not in result
        assert (tmp_path / "routines" / "local.py").exists()
        assert not (paths_mod.shared_routines_root() / "local.py").exists()

    def test_agent_edit_and_delete_cannot_reach_the_shared_root(
        self, isolated, monkeypatch
    ):
        from mcp_servers.condor.tools import routines as mcp_routines

        _seat(monkeypatch, "scalper")
        shared_file = _write(
            paths_mod.shared_routines_root(), "published", desc="untouched"
        )

        hijack = ROUTINE_TEMPLATE.format(desc="hijacked")
        assert "error" in mcp_routines.edit_routine(
            "published", hijack, None, shared=True
        )
        assert "error" in mcp_routines.delete_routine("published", None, shared=True)

        assert "untouched" in shared_file.read_text()

    def test_agent_can_read_the_source_of_a_shared_routine(self, isolated, monkeypatch):
        from mcp_servers.condor.tools import routines as mcp_routines

        _seat(monkeypatch, "scalper")
        _write(paths_mod.shared_routines_root(), "published", desc="Published one")

        read = mcp_routines.read_routine("published", None)

        assert read["scope"] == "shared"
        assert "Published one" in read["code"]


# ── Downstream consumers of the same resolver ─────────────────────────────────


class TestConsumers:
    def test_shared_routine_appears_in_the_agent_prompt(self, isolated):
        from condor.agents.prompts import _build_routines_section
        from condor.agents.strategy import Strategy

        _write(paths_mod.shared_routines_root(), "published", desc="Published one")
        _write(assistant_routines_dir("scalper"), "own", desc="Own one")

        section = _build_routines_section(Strategy(agent_slug="scalper", name="S"))

        assert "- published: Published one (shared)" in section
        assert "- own: Own one" in section
        assert "(none yet" not in section

    def test_prompt_still_falls_back_when_both_libraries_are_empty(self, isolated):
        from condor.agents.prompts import _build_routines_section
        from condor.agents.strategy import Strategy

        section = _build_routines_section(Strategy(agent_slug="scalper", name="S"))

        assert "(none yet" in section

    def test_a_skill_can_reference_a_shared_routine_from_any_seat(self, isolated):
        from condor.memory.skills import _routine_exists

        _write(paths_mod.shared_routines_root(), "published")

        assert _routine_exists("published", "scalper") is True
        assert _routine_exists("published", None) is True
        assert _routine_exists("nope", "scalper") is False
