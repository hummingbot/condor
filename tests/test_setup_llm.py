"""The setup model picker (FEAT-040).

What matters here is that the wizard cannot mislead: the menu is derived from
``AGENT_OPTIONS`` (so a new provider can never silently go missing), a sentinel
can never be written as a default, a model that isn't ready can't be picked, and
``--status`` leaves ``.env`` byte-identical.
"""

import asyncio

import pytest

from condor import setup_llm
from handlers.agents._shared import AGENT_OPTIONS, selectable_agent_options
from handlers.agents.openrouter_models import OpenRouterModel
from handlers.agents.readiness import MISSING, READY, UNVERIFIED, Readiness


def scripted(answers):
    """An ``input`` that replays a list of answers."""
    it = iter(answers)

    def ask(prompt=""):
        return next(it)

    return ask


def recorder():
    lines = []
    return lines, lambda text="": lines.append(str(text))


READY_STATES = {
    "claude-code": Readiness(READY, "installed and logged in"),
    "claude-acp": Readiness(UNVERIFIED, "installed; run `claude` once and log in"),
    "gemini": Readiness(MISSING, "not installed — npm install -g @google/gemini-cli"),
    "copilot": Readiness(MISSING, "not installed"),
    "codex": Readiness(MISSING, "not installed"),
    "ollama": Readiness(READY, "2 model(s) available", ["qwen3:32b", "llama3"]),
    "lmstudio": Readiness(MISSING, "not reachable — start LM Studio"),
    "openrouter": Readiness(MISSING, "needs an API key (you can enter one here)"),
}


# ── .env I/O ────────────────────────────────────────────────────────────────


def test_read_env_skips_comments_and_strips_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        'TELEGRAM_TOKEN="123:abc"\n'
        "ADMIN_USER_ID=42\n"
        "\n"
        "not a var line\n"
        "# CONDOR_DEFAULT_AGENT=commented\n"
        "export WEB_URL='http://localhost:8088'\n"
    )
    env = setup_llm.read_env(env_file)
    assert env == {
        "TELEGRAM_TOKEN": "123:abc",
        "ADMIN_USER_ID": "42",
        "WEB_URL": "http://localhost:8088",
    }


def test_read_env_of_a_missing_file_is_empty(tmp_path):
    assert setup_llm.read_env(tmp_path / "nope") == {}


def test_update_env_var_appends_below_a_commented_example(tmp_path):
    env_file = tmp_path / ".env"
    original = "# ── Required ──\nTELEGRAM_TOKEN=abc\n\n# CONDOR_DEFAULT_AGENT=\n"
    env_file.write_text(original)

    setup_llm.update_env_var(env_file, "CONDOR_DEFAULT_AGENT", "ollama:qwen3:32b")

    text = env_file.read_text()
    assert text == original + "CONDOR_DEFAULT_AGENT=ollama:qwen3:32b\n"
    assert "# CONDOR_DEFAULT_AGENT=" in text  # the comment is left alone


def test_update_env_var_replaces_in_place_without_duplicating(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# top\nCONDOR_DEFAULT_AGENT=claude-code\nTELEGRAM_TOKEN=abc\n# bottom\n"
    )

    setup_llm.update_env_var(env_file, "CONDOR_DEFAULT_AGENT", "ollama:")

    text = env_file.read_text()
    assert text == "# top\nCONDOR_DEFAULT_AGENT=ollama:\nTELEGRAM_TOKEN=abc\n# bottom\n"
    assert text.count("CONDOR_DEFAULT_AGENT=") == 1


def test_update_env_var_ends_the_file_with_a_newline(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TOKEN=abc")  # no trailing newline

    setup_llm.update_env_var(env_file, "CONDOR_DEFAULT_AGENT", "claude-code")

    assert (
        env_file.read_text() == "TELEGRAM_TOKEN=abc\nCONDOR_DEFAULT_AGENT=claude-code\n"
    )


# ── the menu is AGENT_OPTIONS ───────────────────────────────────────────────


def test_menu_is_derived_from_agent_options():
    """A provider added to AGENT_OPTIONS must appear here without touching this file."""
    options = setup_llm.menu_options()
    assert set(selectable_agent_options()) <= set(options)
    assert "openrouter:" in options  # the one picker the wizard can complete
    assert "custom:" not in options  # per-user preferences don't exist at install time
    assert all(AGENT_OPTIONS[k]["label"] == v["label"] for k, v in options.items())


def test_base_of_agent_keys():
    assert setup_llm.base_of("claude-acp:opus") == "claude-acp"
    assert setup_llm.base_of("claude-code") == "claude-code"
    assert setup_llm.base_of("ollama:") == "ollama"
    assert setup_llm.base_of("ollama:qwen3:32b") == "ollama"
    assert setup_llm.base_of("openrouter:z-ai/glm-4.6") == "openrouter"
    assert setup_llm.base_of("custom@venice:llama") == "custom"


# ── sentinels are never written ─────────────────────────────────────────────


@pytest.mark.parametrize("sentinel", ["openrouter:", "custom:", ""])
def test_sentinels_are_not_writable_keys(sentinel, tmp_path):
    assert setup_llm.is_writable_key(sentinel) is False
    env_file = tmp_path / ".env"
    with pytest.raises(ValueError):
        setup_llm.persist_choice(env_file, sentinel)
    assert not env_file.exists()  # nothing written


@pytest.mark.parametrize(
    "key", ["claude-code", "ollama:", "ollama:qwen3:32b", "openrouter:z-ai/glm-4.6"]
)
def test_startable_keys_are_writable(key):
    assert setup_llm.is_writable_key(key) is True


def test_persist_choice_writes_the_key_before_the_default(tmp_path):
    """A half-write must never leave a key-less openrouter default behind."""
    env_file = tmp_path / ".env"
    setup_llm.persist_choice(
        env_file, "openrouter:z-ai/glm-4.6", {"OPENROUTER_API_KEY": "sk-or-1"}
    )
    lines = env_file.read_text().splitlines()
    assert lines.index("OPENROUTER_API_KEY=sk-or-1") < lines.index(
        "CONDOR_DEFAULT_AGENT=openrouter:z-ai/glm-4.6"
    )


# ── current default ─────────────────────────────────────────────────────────


def test_current_default_prefers_env_over_agent_md():
    assert setup_llm.current_default({"CONDOR_DEFAULT_AGENT": "ollama:x"}) == "ollama:x"


def test_current_default_falls_back_to_agent_md(monkeypatch):
    import condor.agents.agent as agent_mod

    class FakeAgent:
        agent_key = "claude-acp:sonnet"

    class FakeStore:
        def get(self, slug):
            return FakeAgent()

    monkeypatch.setattr(agent_mod, "AgentStore", FakeStore)
    assert setup_llm.current_default({}) == "claude-acp:sonnet"


def test_current_default_last_resort(monkeypatch):
    import condor.agents.agent as agent_mod

    class EmptyStore:
        def get(self, slug):
            return None

    monkeypatch.setattr(agent_mod, "AgentStore", EmptyStore)
    assert setup_llm.current_default({}) == "claude-code"


# ── the prompt loop ─────────────────────────────────────────────────────────


def test_skip_keeps_the_current_default():
    for answer in ("s", "", "skip"):
        picked = setup_llm.choose(
            setup_llm.menu_options(),
            READY_STATES,
            "claude-acp:sonnet",
            {},
            ask=scripted([answer]),
            say=lambda *a: None,
        )
        assert picked is None


def test_menu_marks_the_current_default_and_badges_each_row():
    text = setup_llm.render_menu(setup_llm.menu_options(), READY_STATES, "claude-code")
    assert "← current default" in text
    assert "[✓ installed and logged in]" in text
    assert "[? installed; run `claude` once and log in]" in text
    assert "[✗ not installed — npm install -g @google/gemini-cli]" in text
    assert "s. Skip" in text


def test_an_unready_model_cannot_be_picked():
    options = setup_llm.menu_options()
    gemini = list(options).index("gemini") + 1
    claude = list(options).index("claude-code") + 1
    lines, say = recorder()

    picked = setup_llm.choose(
        options,
        READY_STATES,
        "claude-code",
        {},
        ask=scripted([str(gemini), str(claude)]),
        say=say,
    )

    assert picked == ("claude-code", {})
    assert any("isn't ready" in line for line in lines)


def test_invalid_input_reprompts():
    options = setup_llm.menu_options()
    claude = list(options).index("claude-code") + 1
    lines, say = recorder()

    picked = setup_llm.choose(
        options,
        READY_STATES,
        "claude-code",
        {},
        ask=scripted(["banana", "999", str(claude)]),
        say=say,
    )

    assert picked == ("claude-code", {})
    assert sum("Not one of the options" in line for line in lines) == 2


def test_local_drilldown_picks_a_loaded_model():
    options = setup_llm.menu_options()
    ollama = list(options).index("ollama:") + 1

    picked = setup_llm.choose(
        options,
        READY_STATES,
        "claude-code",
        {},
        ask=scripted([str(ollama), "1"]),
        say=lambda *a: None,
    )
    assert picked == ("ollama:qwen3:32b", {})


def test_local_drilldown_enter_keeps_the_backend_default():
    options = setup_llm.menu_options()
    ollama = list(options).index("ollama:") + 1

    picked = setup_llm.choose(
        options,
        READY_STATES,
        "claude-code",
        {},
        ask=scripted([str(ollama), ""]),
        say=lambda *a: None,
    )
    assert picked == ("ollama:", {})  # resolved by the backend at session start


# ── OpenRouter drill-down ───────────────────────────────────────────────────


CATALOG = [
    OpenRouterModel("z-ai/glm-4.6", "GLM 4.6", 200_000, 0.4, 1.6),
    OpenRouterModel("anthropic/claude-sonnet-4-5", "Sonnet 4.5", 200_000, 3.0, 15.0),
]


@pytest.fixture
def openrouter_catalog(monkeypatch):
    async def fetch():
        return list(CATALOG)

    monkeypatch.setattr(setup_llm, "fetch_models", fetch)


def _openrouter_index():
    return list(setup_llm.menu_options()).index("openrouter:") + 1


def test_invalid_openrouter_key_is_rejected_before_anything_is_written(
    monkeypatch, openrouter_catalog
):
    async def rejects(key):
        return False

    monkeypatch.setattr(setup_llm, "validate_openrouter_key", rejects)
    lines, say = recorder()

    picked = setup_llm.choose(
        setup_llm.menu_options(),
        READY_STATES,
        "claude-code",
        {},
        ask=scripted([str(_openrouter_index()), "s"]),
        say=say,
        ask_secret=scripted(["sk-or-bogus"]),
    )

    assert picked is None  # back to the menu, then skipped
    assert any("rejected that key" in line for line in lines)


def test_unreachable_openrouter_does_not_write_a_default(
    monkeypatch, openrouter_catalog
):
    async def unreachable(key):
        return None

    monkeypatch.setattr(setup_llm, "validate_openrouter_key", unreachable)
    lines, say = recorder()

    picked = setup_llm.choose(
        setup_llm.menu_options(),
        READY_STATES,
        "claude-code",
        {},
        ask=scripted([str(_openrouter_index()), "s"]),
        say=say,
        ask_secret=scripted(["sk-or-1"]),
    )

    assert picked is None
    assert any("Could not reach OpenRouter" in line for line in lines)


def test_openrouter_pick_offers_the_tool_capable_catalog_cheapest_first(
    monkeypatch, openrouter_catalog
):
    async def accepts(key):
        return True

    monkeypatch.setattr(setup_llm, "validate_openrouter_key", accepts)
    lines, say = recorder()

    picked = setup_llm.choose(
        setup_llm.menu_options(),
        READY_STATES,
        "claude-code",
        {},
        ask=scripted([str(_openrouter_index()), "1"]),
        say=say,
        ask_secret=scripted(["sk-or-1"]),
    )

    # fetch_models() is already filtered to tool-calling models; cheapest first.
    assert picked == ("openrouter:z-ai/glm-4.6", {"OPENROUTER_API_KEY": "sk-or-1"})
    listed = [line for line in lines if "/Mtok in" in line]
    assert listed[0].strip().startswith("1. z-ai/glm-4.6")


def test_openrouter_reuses_a_saved_key_without_rewriting_it(
    monkeypatch, openrouter_catalog
):
    async def accepts(key):
        assert key == "sk-or-saved"
        return True

    monkeypatch.setattr(setup_llm, "validate_openrouter_key", accepts)

    picked = setup_llm.choose(
        setup_llm.menu_options(),
        READY_STATES,
        "claude-code",
        {"OPENROUTER_API_KEY": "sk-or-saved"},
        ask=scripted([str(_openrouter_index()), "2"]),
        say=lambda *a: None,
        ask_secret=scripted([""]),  # Enter keeps the saved key
    )
    assert picked == ("openrouter:anthropic/claude-sonnet-4-5", {})


def test_openrouter_key_is_validated_against_the_key_endpoint(monkeypatch):
    """/models is public — a bogus key still gets a 200 there."""
    seen = {}

    class FakeResponse:
        status_code = 401

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(setup_llm.httpx, "AsyncClient", FakeClient)

    assert asyncio.run(setup_llm.validate_openrouter_key("sk-bad")) is False
    assert seen["url"] == "https://openrouter.ai/api/v1/key"
    assert seen["headers"]["Authorization"] == "Bearer sk-bad"


# ── main() ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# top\nTELEGRAM_TOKEN=abc\nCONDOR_DEFAULT_AGENT=ollama:x\n")
    monkeypatch.setattr(setup_llm, "ENV_PATH", env_file)

    async def states(bases, env=None):
        return {setup_llm.base_of(k): v for k, v in READY_STATES.items()}

    monkeypatch.setattr(setup_llm.readiness, "probe_all", states)
    return env_file


def test_status_prints_the_report_and_writes_nothing(fake_env_file, capsys):
    before = fake_env_file.read_bytes()

    assert setup_llm.main(["--status"]) == 0

    assert fake_env_file.read_bytes() == before
    out = capsys.readouterr().out
    assert "Default model: ollama:x" in out
    assert "make pick-model" in out


def test_no_tty_falls_back_to_status(fake_env_file, monkeypatch, capsys):
    monkeypatch.setattr(setup_llm.sys.stdin, "isatty", lambda: False)
    before = fake_env_file.read_bytes()

    assert setup_llm.main([]) == 0

    assert fake_env_file.read_bytes() == before
    assert "Default model: ollama:x" in capsys.readouterr().out


def test_an_interrupted_wizard_leaves_setup_running(fake_env_file, monkeypatch, capsys):
    monkeypatch.setattr(setup_llm.sys.stdin, "isatty", lambda: True)

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_llm, "choose", interrupted)
    before = fake_env_file.read_bytes()

    assert setup_llm.main([]) == 0  # never fails setup

    assert fake_env_file.read_bytes() == before
    assert "Skipped" in capsys.readouterr().out


def test_a_pick_is_written_and_reported(fake_env_file, monkeypatch, capsys):
    monkeypatch.setattr(setup_llm.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(setup_llm, "choose", lambda *a, **k: ("ollama:qwen3:32b", {}))

    assert setup_llm.main([]) == 0

    env = setup_llm.read_env(fake_env_file)
    assert env["CONDOR_DEFAULT_AGENT"] == "ollama:qwen3:32b"
    assert env["TELEGRAM_TOKEN"] == "abc"  # every other line preserved
    assert "# top" in fake_env_file.read_text()
    out = capsys.readouterr().out
    assert "Saved CONDOR_DEFAULT_AGENT=ollama:qwen3:32b" in out
    assert "Default model: ollama:qwen3:32b" in out
