"""Condor is the default agent, not a separate species (FEAT-033).

``assistants/`` is gone: Condor lives at ``agents/condor/`` and is loaded by
``AgentStore`` like every other agent, so a falsy ``agent_slug`` and the literal
``"condor"`` name the same brain everywhere.

The two carve-outs below are the load-bearing ones. Both fail **silently** if the
slug is threaded through naively — no exception, just a chat that answers with
the wrong framing or lists an empty routine catalog — which is why they are
asserted here rather than checked by hand.
"""

from pathlib import Path

import pytest

from condor.agents import agent as agent_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import AgentStore
from condor.memory.paths import CHAT_SLUG

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_agent(root, slug, *, body="Body.", **frontmatter):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / "AGENT.md").write_text(f"---\n{fm}\n---\n\n{body}\n")
    return d


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registry containing Condor and one specialist, like the real one."""
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, CHAT_SLUG, name="Condor", agent_key="claude-acp:sonnet")
    _write_agent(
        tmp_path, "brigado", name="Brigado", when_to_consult="BRL market making"
    )
    return tmp_path


# ── Carve-out 1: the chat's routine library does not move ──


def test_chat_routines_dir_is_the_repo_root_library():
    """A falsy slug AND ``"condor"`` both resolve the general library.

    Condor is an ordinary ``agents/`` entry now, so the naive
    ``agents/<slug>/routines`` rule would point the chat at
    ``agents/condor/routines`` — a dir that does not exist, so every routine
    would vanish from the catalog without a single error.
    """
    from routines.base import assistant_routines_dir

    general = _REPO_ROOT / "routines"
    assert assistant_routines_dir(None) == general
    assert assistant_routines_dir("") == general
    assert assistant_routines_dir(CHAT_SLUG) == general


def test_a_specialist_still_owns_an_isolated_routines_dir():
    from routines.base import assistant_routines_dir

    assert assistant_routines_dir("brigado") == _REPO_ROOT / "agents/brigado/routines"


# ── Carve-out 2: the chat's MCP instructions stay the coordinator's ──


def _instructions(monkeypatch, slug: str) -> str:
    from mcp_servers.condor import server
    from mcp_servers.condor.settings import settings

    monkeypatch.setattr(settings, "agent_slug", slug)
    return server._build_instructions()


@pytest.mark.parametrize("slug", ["", CHAT_SLUG])
def test_chat_instructions_are_the_coordinator_text(registry, monkeypatch, slug):
    """``--agent-slug condor`` is the chat, not a specialist.

    The chat's subprocess now carries its own slug (it needs it to scope memory
    and skills), so keying the branch on the raw slug would hand Condor the
    specialist framing — with ``identity_header``'s "You are NOT Condor" in it.
    """
    text = _instructions(monkeypatch, slug)
    assert text.startswith("Condor exposes reusable **skills**")
    assert "You are NOT Condor" not in text
    assert "[AGENTS — consult for domain work]" in text


def test_specialist_instructions_still_assert_their_own_identity(registry, monkeypatch):
    text = _instructions(monkeypatch, "brigado")
    assert "You are NOT Condor" in text
    assert not text.startswith("Condor exposes reusable **skills**")


def test_settings_specialist_slug_only_names_specialists():
    from mcp_servers.condor.settings import Settings

    def _settings(slug: str) -> Settings:
        return Settings(
            chat_id=1,
            user_id=1,
            bot_token="",
            agent_slug=slug,
            active_server="",
            session_key="",
        )

    assert _settings("").specialist_slug == ""
    assert _settings(CHAT_SLUG).specialist_slug == ""
    assert _settings("brigado").specialist_slug == "brigado"


# ── One registry, one loader ──


def test_condor_is_a_real_record_in_the_agent_store():
    """Not a fixture: the shipped repo must actually load Condor as an agent."""
    agent = AgentStore().get(CHAT_SLUG)
    assert agent is not None
    assert agent.name == "Condor"
    assert agent.agent_dir == _REPO_ROOT / "agents" / CHAT_SLUG
    assert agent.instructions, "AGENT.md body is the chat's system prompt"
    # Not consulted by specialists: it is the coordinator, not a domain expert.
    assert agent.when_to_consult == ""


def test_the_assistants_tree_is_gone():
    assert not (_REPO_ROOT / "assistants").exists()


def test_the_chats_store_and_skills_moved_with_it():
    from condor.memory.paths import builtin_skills_root, store_root

    home = _REPO_ROOT / "agents" / CHAT_SLUG
    assert store_root(42) == home / "store" / "user_42"
    assert store_root(42, CHAT_SLUG) == store_root(42, None)
    assert builtin_skills_root() == home / "skills"
    # The shipped library came across intact rather than being re-created empty.
    assert (home / "skills" / "routine_cookbook" / "SKILL.md").exists()


def test_the_chat_inherits_nothing_from_itself(tmp_path, monkeypatch):
    """``SkillStore("condor")`` is the chat, so it must not shadow itself.

    Inheritance (FEAT-031) reads Condor's shared playbooks into every agent's
    library. Resolved by slug rather than by directory, the chat would inherit
    from its own dir and list every shared playbook twice.
    """
    from condor.memory import paths as paths_module
    from condor.memory.skills import SkillStore

    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    d = tmp_path / "agents" / CHAT_SLUG / "skills" / "published"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: published\nwhen_to_use: always\nshared: true\n---\n\nSteps.\n"
    )

    assert SkillStore(None).inherited_dir is None
    assert SkillStore(CHAT_SLUG).inherited_dir is None
    assert SkillStore(CHAT_SLUG).list_index().count("[published]") == 1


def test_memory_index_lists_the_chat_first_as_the_chat(tmp_path, monkeypatch):
    from condor.memory import paths as paths_module
    from condor.memory.paths import iter_user_stores

    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    for slug in (CHAT_SLUG, "brigado"):
        (tmp_path / "agents" / slug / "store" / "user_7").mkdir(parents=True)

    found = iter_user_stores(7)
    assert [(label, slug) for label, slug, _ in found] == [
        (f"{CHAT_SLUG} (chat)", None),
        ("brigado", "brigado"),
    ]


# ── The coordinator is not a peer ──


def test_a_specialist_is_offered_neither_itself_nor_condor(registry, monkeypatch):
    _write_agent(registry, "other", name="Other", when_to_consult="Other work")
    text = _instructions(monkeypatch, "brigado")
    assert "- [brigado]" not in text
    assert f"- [{CHAT_SLUG}]" not in text
    assert "- [other] Other work" in text


def test_the_chat_is_not_offered_itself_either(registry, monkeypatch):
    """Condor must not appear in its own [AGENTS] index — on either channel."""
    text = _instructions(monkeypatch, "")
    assert f"- [{CHAT_SLUG}]" not in text
    assert "- [brigado] BRL market making" in text

    # The opening context injects the same index through its own call.
    index = AgentStore().list_index(exclude={CHAT_SLUG})
    assert f"- [{CHAT_SLUG}]" not in index
    assert "- [brigado] BRL market making" in index


def test_excluding_by_string_still_drops_exactly_that_slug(registry):
    """The set form is additive; the old single-slug call is unchanged."""
    index = AgentStore().list_index(exclude="brigado")
    assert "- [brigado]" not in index
    assert f"- [{CHAT_SLUG}]" in index, "a bare exclude hides nothing else"


def test_the_reserved_slug_cannot_be_created_or_deleted(registry):
    store = AgentStore()
    with pytest.raises(ValueError, match="reserved"):
        store.create(name="Condor")
    with pytest.raises(ValueError, match="cannot be deleted"):
        store.delete(CHAT_SLUG)
    # Still there afterwards.
    assert store.get(CHAT_SLUG) is not None


def test_identity_header_does_not_contradict_itself_for_condor():
    from condor.agents.agent import identity_header

    header = identity_header(CHAT_SLUG, "Condor")
    assert "You ARE Condor" in header
    assert "You are NOT Condor" not in header
    assert "coordinator" in header


# ── The mode axis is gone, and its leftovers are tolerated ──


def test_no_mode_survives_on_the_session_boundary():
    from condor.runtime.models import SessionInfo, SessionSpec

    assert "mode" not in SessionSpec.model_fields
    assert "mode" not in SessionInfo.model_fields


def test_a_record_written_before_the_mode_axis_died_still_loads():
    """Stale `mode` keys are ignored, never fatal: they are on disk already."""
    from condor.runtime.conversations import ConversationMeta, TurnEntry
    from condor.runtime.models import SessionSpec

    spec = SessionSpec(key="tg:42", agent_key="claude-code", mode="condor")
    assert spec.key == "tg:42"

    meta = ConversationMeta(id="abc", user_id=7, mode="agent_builder")
    assert meta.id == "abc"

    turn = TurnEntry(role="assistant", text="hi", mode="condor")
    assert turn.text == "hi"
