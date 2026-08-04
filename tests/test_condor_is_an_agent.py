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
