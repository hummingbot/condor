"""Unit tests for condor.setup_llm (setup-time AI model selection).

Covers the pure parts: .env read/update, menu construction from
AGENT_OPTIONS, choice persistence, and model-list parsing. Interactive flows
and network probes are exercised only through their parsing helpers.
"""

from pathlib import Path

from condor import setup_llm

# ── .env read/update ─────────────────────────────────


def test_read_env_parses_values_and_skips_noise(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nTELEGRAM_TOKEN=abc:123\n\nbroken line\nWEB_URL=http://x:8088\n"
    )
    assert setup_llm.read_env(env) == {
        "TELEGRAM_TOKEN": "abc:123",
        "WEB_URL": "http://x:8088",
    }


def test_read_env_missing_file(tmp_path):
    assert setup_llm.read_env(tmp_path / ".env") == {}


def test_update_env_var_creates_file(tmp_path):
    env = tmp_path / ".env"
    setup_llm.update_env_var(env, "CONDOR_DEFAULT_AGENT", "gemini")
    assert env.read_text() == "CONDOR_DEFAULT_AGENT=gemini\n"


def test_update_env_var_replaces_in_place_preserving_others(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_TOKEN=abc\nCONDOR_DEFAULT_AGENT=claude-code\nWEB_URL=http://x\n"
    )
    setup_llm.update_env_var(env, "CONDOR_DEFAULT_AGENT", "ollama:qwen3:32b")
    assert env.read_text() == (
        "TELEGRAM_TOKEN=abc\nCONDOR_DEFAULT_AGENT=ollama:qwen3:32b\nWEB_URL=http://x\n"
    )


def test_update_env_var_appends_when_absent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_TOKEN=abc\n")
    setup_llm.update_env_var(env, "OPENROUTER_API_KEY", "sk-or-x")
    assert env.read_text() == "TELEGRAM_TOKEN=abc\nOPENROUTER_API_KEY=sk-or-x\n"


# ── menu construction ────────────────────────────────


def test_menu_offers_every_startable_option_and_openrouter():
    from handlers.agents._shared import AGENT_OPTIONS

    keys = [k for k, _ in setup_llm.menu_options()]
    assert "custom:" not in keys  # needs per-user prefs; runtime-only
    assert "openrouter:" in keys  # picker the wizard can complete itself
    for key, meta in AGENT_OPTIONS.items():
        if not meta.get("picker"):
            assert key in keys


def test_current_default_prefers_env_file_over_fallback():
    assert setup_llm.current_default({"CONDOR_DEFAULT_AGENT": "gemini"}) == "gemini"
    from handlers.agents._shared import DEFAULT_AGENT

    assert setup_llm.current_default({}) == DEFAULT_AGENT


# ── persistence ──────────────────────────────────────


def test_persist_choice_writes_agent_and_extra_env(tmp_path):
    env = tmp_path / ".env"
    setup_llm.persist_choice(
        "openrouter:anthropic/claude-sonnet-4-5",
        extra_env={"OPENROUTER_API_KEY": "sk-or-x"},
        path=env,
    )
    assert setup_llm.read_env(env) == {
        "OPENROUTER_API_KEY": "sk-or-x",
        "CONDOR_DEFAULT_AGENT": "openrouter:anthropic/claude-sonnet-4-5",
    }


# ── model-list parsing ───────────────────────────────


def test_parse_openai_models():
    payload = {
        "data": [{"id": "llama3.1:8b"}, {"id": "qwen3:32b"}, {"object": "noise"}]
    }
    assert setup_llm.parse_openai_models(payload) == ["llama3.1:8b", "qwen3:32b"]


def test_parse_openai_models_empty():
    assert setup_llm.parse_openai_models({}) == []


# ── detection states ─────────────────────────────────


def test_detect_openrouter_needs_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    state, _ = setup_llm.detect("openrouter", {})
    assert state == setup_llm.MISSING
    state, _ = setup_llm.detect("openrouter", {"OPENROUTER_API_KEY": "sk-or-x"})
    assert state == setup_llm.READY


def test_detect_local_provider_down(monkeypatch):
    monkeypatch.setattr(setup_llm, "probe_local_models", lambda provider: None)
    state, detail = setup_llm.detect("ollama", {})
    assert state == setup_llm.MISSING
    assert "ollama serve" in detail


def test_detect_local_provider_up(monkeypatch):
    monkeypatch.setattr(setup_llm, "probe_local_models", lambda provider: ["qwen3:32b"])
    state, detail = setup_llm.detect("lmstudio", {})
    assert state == setup_llm.READY
    assert "1 model(s)" in detail


def test_detect_claude_missing_bridge(monkeypatch):
    monkeypatch.setattr(setup_llm, "which", lambda cmd: None)
    state, detail = setup_llm.detect("claude-code", {})
    assert state == setup_llm.MISSING
    assert "claude-agent-acp" in detail
