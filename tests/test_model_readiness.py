"""The shared readiness probes (FEAT-040).

Both the ``get_available_models`` MCP tool and the setup wizard ask this module
"can this machine run that model?", so what is asserted here is what every
surface will say. The interesting cases are the ones a fresh implementation gets
wrong: npx exists on any machine with Node (so probing the launcher over-reports
every bridge), and an installed bridge without a login is not a working one.
"""

import asyncio
import subprocess

import pytest

from handlers.agents import readiness
from handlers.agents.readiness import MISSING, READY, UNVERIFIED

# ── npx package resolution ──────────────────────────────────────────────────


def test_npx_packages_finds_local_global_and_cache(tmp_path, monkeypatch):
    cwd = tmp_path / "project"
    (cwd / "node_modules" / "local-pkg").mkdir(parents=True)
    (cwd / "node_modules" / "@scope" / "scoped").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".npm" / "_npx" / "abc123" / "node_modules" / "cached").mkdir(parents=True)
    global_root = tmp_path / "global" / "node_modules"
    (global_root / "@google" / "gemini-cli").mkdir(parents=True)

    monkeypatch.setattr(readiness.Path, "cwd", staticmethod(lambda: cwd))
    monkeypatch.setattr(readiness.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(readiness.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(
        readiness.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, f"{global_root}\n", ""),
    )

    found = readiness.npx_packages_installed()
    assert {"local-pkg", "@scope/scoped", "cached", "@google/gemini-cli"} <= found


def test_npx_packages_survives_unusable_npm(tmp_path, monkeypatch):
    """npm broken = nothing installed via it, not a crash."""
    monkeypatch.setattr(readiness.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(readiness.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(readiness.shutil, "which", lambda name: "/usr/bin/npm")

    def boom(*a, **k):
        raise OSError("npm is a smoking crater")

    monkeypatch.setattr(readiness.subprocess, "run", boom)
    assert readiness.npx_packages_installed() == set()


# ── login detection ─────────────────────────────────────────────────────────


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(readiness.Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_claude_login_from_credentials_file(fake_home):
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / ".credentials.json").write_text("{}")
    assert readiness.acp_login_state("claude-code") is True
    assert readiness.acp_login_state("claude-acp") is True


def test_claude_login_falls_back_to_keychain_on_darwin(fake_home, monkeypatch):
    monkeypatch.setattr(readiness.sys, "platform", "darwin")
    monkeypatch.setattr(readiness, "_keychain_has", lambda service: True)
    assert readiness.acp_login_state("claude-code") is True

    monkeypatch.setattr(readiness, "_keychain_has", lambda service: False)
    assert readiness.acp_login_state("claude-code") is False

    # Keychain unreadable: we cannot tell, and must not claim we can.
    monkeypatch.setattr(readiness, "_keychain_has", lambda service: None)
    assert readiness.acp_login_state("claude-code") is None


def test_claude_login_absent_off_darwin(fake_home, monkeypatch):
    monkeypatch.setattr(readiness.sys, "platform", "linux")
    assert readiness.acp_login_state("claude-code") is False


def test_gemini_login_from_file_or_api_key(fake_home, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert readiness.acp_login_state("gemini") is False

    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    assert readiness.acp_login_state("gemini") is True

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (fake_home / ".gemini").mkdir()
    (fake_home / ".gemini" / "oauth_creds.json").write_text("{}")
    assert readiness.acp_login_state("gemini") is True


def test_codex_login_from_auth_file(fake_home):
    assert readiness.acp_login_state("codex") is False
    (fake_home / ".codex").mkdir()
    (fake_home / ".codex" / "auth.json").write_text("{}")
    assert readiness.acp_login_state("codex") is True


def test_copilot_login_is_honestly_unknown(fake_home):
    """No documented on-disk marker — never claim it is or isn't logged in."""
    assert readiness.acp_login_state("copilot") is None
    assert readiness.acp_login_state("something-new") is None


# ── bridges ─────────────────────────────────────────────────────────────────


def test_acp_bridges_probes_the_package_not_the_launcher(monkeypatch):
    """npx is on every machine with Node; the package is what decides."""
    monkeypatch.setattr(readiness, "npx_packages_installed", lambda: set())
    monkeypatch.setattr(
        readiness.shutil,
        "which",
        lambda name: "/usr/bin/npx" if name == "npx" else None,
    )
    monkeypatch.setattr(readiness, "acp_login_state", lambda base: True)

    by_key = {e["agent_key"]: e for e in readiness.acp_bridges()}
    assert by_key["gemini"]["available"] is False
    assert by_key["codex"]["available"] is False
    # An unavailable bridge has nothing to be logged into.
    assert by_key["gemini"]["logged_in"] is None


def test_acp_bridges_surface_login_state(monkeypatch):
    monkeypatch.setattr(
        readiness, "npx_packages_installed", lambda: {"@google/gemini-cli"}
    )
    monkeypatch.setattr(readiness.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        readiness, "acp_login_state", lambda base: base == "claude-code"
    )

    by_key = {e["agent_key"]: e for e in readiness.acp_bridges()}
    assert by_key["claude-code"] == {
        "agent_key": "claude-code",
        "command": "claude-agent-acp",
        "available": True,
        "logged_in": True,
    }
    assert by_key["gemini"]["available"] is True
    assert by_key["gemini"]["logged_in"] is False


def test_install_command_names_the_package():
    assert readiness.install_command("npx @google/gemini-cli --acp") == (
        "npm install -g @google/gemini-cli"
    )
    assert readiness.install_command("claude-agent-acp") == (
        "npm install -g @agentclientprotocol/claude-agent-acp"
    )
    assert "on PATH" in readiness.install_command("some-other-cli")


# ── probe() ─────────────────────────────────────────────────────────────────


def _probe(base, env=None, bridges=None, servers=None, monkeypatch=None):
    if bridges is not None:
        monkeypatch.setattr(readiness, "acp_bridges", lambda: bridges)
    if servers is not None:

        async def _servers():
            return servers

        monkeypatch.setattr(readiness, "local_servers", _servers)
    return asyncio.run(readiness.probe(base, env or {}))


def test_probe_missing_bridge_names_the_install_command(monkeypatch):
    r = _probe(
        "gemini",
        bridges=[
            {
                "agent_key": "gemini",
                "command": "npx @google/gemini-cli --acp",
                "available": False,
                "logged_in": None,
            }
        ],
        monkeypatch=monkeypatch,
    )
    assert r.state == MISSING
    assert "npm install -g @google/gemini-cli" in r.detail
    assert r.usable is False


def test_probe_installed_but_not_logged_in_is_unverified(monkeypatch):
    r = _probe(
        "claude-acp",
        bridges=[
            {
                "agent_key": "claude-acp",
                "command": "claude-agent-acp",
                "available": True,
                "logged_in": False,
            }
        ],
        monkeypatch=monkeypatch,
    )
    assert r.state == UNVERIFIED
    assert "`claude`" in r.detail  # names the fix
    assert r.usable is True  # an unproven login must never block a pick


def test_probe_unknown_login_is_unverified_not_missing(monkeypatch):
    r = _probe(
        "copilot",
        bridges=[
            {
                "agent_key": "copilot",
                "command": "npx @github/copilot --acp --stdio",
                "available": True,
                "logged_in": None,
            }
        ],
        monkeypatch=monkeypatch,
    )
    assert r.state == UNVERIFIED
    assert "could not be verified" in r.detail


def test_probe_logged_in_bridge_is_ready(monkeypatch):
    r = _probe(
        "claude-code",
        bridges=[
            {
                "agent_key": "claude-code",
                "command": "claude-agent-acp",
                "available": True,
                "logged_in": True,
            }
        ],
        monkeypatch=monkeypatch,
    )
    assert r.state == READY


def test_probe_stopped_local_server_names_the_start_command(monkeypatch):
    r = _probe(
        "ollama",
        servers={
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "reachable": False,
                "models": [],
            }
        },
        monkeypatch=monkeypatch,
    )
    assert r.state == MISSING
    assert "ollama serve" in r.detail


def test_probe_reachable_local_server_lists_its_models(monkeypatch):
    r = _probe(
        "lmstudio",
        servers={
            "lmstudio": {
                "base_url": "http://localhost:1234/v1",
                "reachable": True,
                "models": ["qwen3-32b", "llama3"],
            }
        },
        monkeypatch=monkeypatch,
    )
    assert r.state == READY
    assert r.models == ["qwen3-32b", "llama3"]
    assert "2 model(s)" in r.detail


def test_probe_openrouter_reads_the_passed_env_not_just_the_process(monkeypatch):
    """The wizard runs before anything loads .env, so the key comes in by argument."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert asyncio.run(readiness.probe("openrouter", {})).state == MISSING
    ready = asyncio.run(readiness.probe("openrouter", {"OPENROUTER_API_KEY": "sk-x"}))
    assert ready.state == READY


def test_probe_all_probes_each_source_once(monkeypatch):
    calls = {"bridges": 0, "servers": 0}

    def bridges():
        calls["bridges"] += 1
        return [
            {
                "agent_key": k,
                "command": "claude-agent-acp",
                "available": True,
                "logged_in": True,
            }
            for k in ("claude-code", "claude-acp")
        ]

    async def servers():
        calls["servers"] += 1
        return {
            "ollama": {"base_url": "u", "reachable": True, "models": []},
            "lmstudio": {"base_url": "u", "reachable": False, "models": []},
        }

    monkeypatch.setattr(readiness, "acp_bridges", bridges)
    monkeypatch.setattr(readiness, "local_servers", servers)

    out = asyncio.run(
        readiness.probe_all(
            ["claude-code", "claude-acp", "ollama", "lmstudio", "openrouter"], {}
        )
    )
    assert calls == {"bridges": 1, "servers": 1}
    assert set(out) == {"claude-code", "claude-acp", "ollama", "lmstudio", "openrouter"}


def test_available_models_reports_login_state(monkeypatch):
    """The MCP tool keeps its shape and gains ``logged_in`` per bridge."""
    from mcp_servers.condor.tools import available_models

    monkeypatch.setattr(
        available_models,
        "acp_bridges",
        lambda: [
            {
                "agent_key": "claude-code",
                "command": "claude-agent-acp",
                "available": True,
                "logged_in": False,
            }
        ],
    )

    async def servers():
        return {"ollama": {"base_url": "u", "reachable": False, "models": []}}

    async def no_custom():
        return []

    async def no_openrouter(query, limit):
        return {"key_present": False, "total_matching": 0, "shown": 0, "models": []}

    monkeypatch.setattr(available_models, "local_servers", servers)
    monkeypatch.setattr(available_models, "_custom_endpoints", no_custom)
    monkeypatch.setattr(available_models, "_openrouter", no_openrouter)

    report = asyncio.run(available_models.get_available_models())
    assert set(report) == {
        "acp_clis",
        "cloud_keys",
        "custom_endpoints",
        "local",
        "openrouter",
    }
    assert report["acp_clis"][0]["logged_in"] is False
    assert set(report["cloud_keys"]) == {
        "openrouter",
        "openai",
        "anthropic",
        "groq",
        "google",
    }
