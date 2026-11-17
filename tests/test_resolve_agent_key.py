"""``agent_key: auto`` defers to the install-wide default (CORR-1160).

A bundled specialist agent used to pin a literal backend in its own
``AGENT.md``, ignoring ``CONDOR_DEFAULT_AGENT`` entirely. ``auto`` is the one
sentinel ``resolve_agent_key`` (and ``options.resolve_auto_agent_key``, the
helper every other resolution point shares) treats specially — a run config
or strategy override still wins outright.
"""

from types import SimpleNamespace

import pytest

from condor.agents.engine import resolve_agent_key
from condor.llm import options as llm_options


@pytest.fixture(autouse=True)
def _no_condor_agent_md(monkeypatch):
    """Isolate ``_default_agent``'s fallback from whatever AGENT.md is on disk."""
    monkeypatch.setattr(llm_options, "_chat_agent", lambda: None)


def test_resolve_auto_agent_key_reads_env(monkeypatch):
    monkeypatch.setenv("CONDOR_DEFAULT_AGENT", "gemini")
    assert llm_options.resolve_auto_agent_key("auto") == "gemini"


def test_resolve_auto_agent_key_last_resort(monkeypatch):
    monkeypatch.delenv("CONDOR_DEFAULT_AGENT", raising=False)
    assert llm_options.resolve_auto_agent_key("auto") == "claude-code"


def test_resolve_auto_agent_key_passes_through_non_auto():
    assert llm_options.resolve_auto_agent_key("claude-acp:opus") == "claude-acp:opus"
    assert llm_options.resolve_auto_agent_key("") == ""


def test_engine_resolves_auto_agent_to_install_default(monkeypatch):
    monkeypatch.setenv("CONDOR_DEFAULT_AGENT", "codex")
    strategy = SimpleNamespace(agent_key="")
    agent = SimpleNamespace(agent_key="auto")

    assert resolve_agent_key({}, strategy, agent) == "codex"


def test_engine_config_override_wins_over_auto(monkeypatch):
    monkeypatch.setenv("CONDOR_DEFAULT_AGENT", "codex")
    strategy = SimpleNamespace(agent_key="")
    agent = SimpleNamespace(agent_key="auto")

    assert (
        resolve_agent_key({"agent_key": "claude-acp:opus"}, strategy, agent)
        == "claude-acp:opus"
    )


def test_engine_strategy_override_wins_over_auto(monkeypatch):
    monkeypatch.setenv("CONDOR_DEFAULT_AGENT", "codex")
    strategy = SimpleNamespace(agent_key="ollama:qwen3:32b")
    agent = SimpleNamespace(agent_key="auto")

    assert resolve_agent_key({}, strategy, agent) == "ollama:qwen3:32b"


def test_engine_pinned_agent_is_unaffected(monkeypatch):
    """An explicit, non-"auto" pin is returned literally — untouched."""
    monkeypatch.setenv("CONDOR_DEFAULT_AGENT", "codex")
    strategy = SimpleNamespace(agent_key="")
    agent = SimpleNamespace(agent_key="claude-acp:opus")

    assert resolve_agent_key({}, strategy, agent) == "claude-acp:opus"
