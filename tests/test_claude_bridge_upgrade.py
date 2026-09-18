"""The Claude ACP bridge: picking its model in either shape, and keeping it current.

@zed-industries/claude-agent-acp (deprecated, frozen at 0.23) bundles a Claude
CLI that bills Sonnet 4.6 at Opus rates. Its successor,
@agentclientprotocol/claude-agent-acp, moved model selection from a ``models``
block to a ``configOptions`` entry — reading only the old block would silently
leave every pinned brain on the default model.
"""

import asyncio
import json
import os
from types import SimpleNamespace

from condor import setup_llm
from condor.acp.client import ACPClient, advertised_models
from condor.llm import readiness
from condor.llm.readiness import MISSING, READY, UNVERIFIED, Readiness
from utils import updater

CONFIG_OPTIONS_SESSION = {
    "sessionId": "s",
    "configOptions": [
        {"id": "mode", "category": "mode", "currentValue": "default", "options": []},
        {
            "id": "model",
            "category": "model",
            "currentValue": "opus[1m]",
            "options": [
                {"value": "default", "name": "Default (recommended)"},
                {"value": "opus[1m]", "name": "Opus 5"},
                {"value": "sonnet", "name": "Sonnet 5"},
                {"value": "haiku", "name": "Haiku 4.5"},
            ],
        },
    ],
}

LEGACY_SESSION = {
    "sessionId": "s",
    "models": {
        "currentModelId": "default",
        "availableModels": [
            {"modelId": "default", "name": "Default (recommended)"},
            {"modelId": "sonnet", "name": "Sonnet"},
        ],
    },
}


# ── Model selection ──


def test_advertised_models_reads_config_options():
    models, current, config_id = advertised_models(CONFIG_OPTIONS_SESSION)
    assert [m["modelId"] for m in models] == ["default", "opus[1m]", "sonnet", "haiku"]
    assert current == "opus[1m]"
    assert config_id == "model"


def test_advertised_models_still_reads_the_legacy_block():
    models, current, config_id = advertised_models(LEGACY_SESSION)
    assert [m["modelId"] for m in models] == ["default", "sonnet"]
    assert current == "default"
    assert config_id is None


def _client_with_fake_peer(model: str):
    sent = []

    async def send_request(method, params, stdin, **kw):
        sent.append((method, params))
        return {}

    client = ACPClient("claude-agent-acp", model=model)
    client._session_id = "s"
    client._process = SimpleNamespace(stdin=None)
    client._peer = SimpleNamespace(send_request=send_request)
    return client, sent


def test_select_model_uses_set_config_option_on_the_new_bridge():
    client, sent = _client_with_fake_peer("sonnet")
    asyncio.run(client._select_model(CONFIG_OPTIONS_SESSION))
    assert sent == [
        (
            "session/set_config_option",
            {"sessionId": "s", "configId": "model", "value": "sonnet"},
        )
    ]
    assert client.active_model_id == "sonnet"


def test_select_model_uses_set_model_on_the_legacy_bridge():
    client, sent = _client_with_fake_peer("sonnet")
    asyncio.run(client._select_model(LEGACY_SESSION))
    assert sent == [("session/set_model", {"sessionId": "s", "modelId": "sonnet"})]


def test_select_model_sends_nothing_when_already_on_it():
    client, sent = _client_with_fake_peer("opus")
    asyncio.run(client._select_model(CONFIG_OPTIONS_SESSION))
    assert sent == []
    assert client.active_model_id == "opus[1m]"


# ── Detecting an outdated install ──


def _npm_install(tmp_path, name: str, version: str) -> str:
    """A global npm prefix holding ``name`` with its bin linked onto PATH."""
    prefix = tmp_path / "prefix"
    pkg = prefix / "lib" / "node_modules" / name
    (pkg / "dist").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": name, "version": version}))
    (pkg / "dist" / "index.js").write_text("")
    (prefix / "bin").mkdir()
    (prefix / "bin" / "npm").write_text("")
    link = prefix / "bin" / "claude-agent-acp"
    os.symlink(pkg / "dist" / "index.js", link)
    return str(link)


def test_the_deprecated_package_is_outdated_and_uninstalled_first(
    tmp_path, monkeypatch
):
    link = _npm_install(tmp_path, "@zed-industries/claude-agent-acp", "0.21.0")
    monkeypatch.setattr(readiness.shutil, "which", lambda name: link)
    npm = str(tmp_path.resolve() / "prefix" / "bin" / "npm")

    out = readiness.outdated_bridge("claude-agent-acp")

    assert out["installed"] == "@zed-industries/claude-agent-acp 0.21.0"
    # The npm of the prefix that owns the bin, not whichever npm is on PATH.
    assert out["fix"] == (
        f"{npm} uninstall -g @zed-industries/claude-agent-acp && "
        f"{npm} install -g @agentclientprotocol/claude-agent-acp@latest"
    )


def test_an_old_release_of_the_current_package_only_reinstalls(tmp_path, monkeypatch):
    link = _npm_install(tmp_path, readiness.CLAUDE_BRIDGE_PACKAGE, "0.65.0")
    monkeypatch.setattr(readiness.shutil, "which", lambda name: link)

    out = readiness.outdated_bridge("claude-agent-acp")

    assert "uninstall" not in out["fix"]
    assert out["fix"].endswith(
        "install -g @agentclientprotocol/claude-agent-acp@latest"
    )


def test_a_current_release_is_not_outdated(tmp_path, monkeypatch):
    link = _npm_install(tmp_path, readiness.CLAUDE_BRIDGE_PACKAGE, "0.79.0")
    monkeypatch.setattr(readiness.shutil, "which", lambda name: link)
    assert readiness.outdated_bridge("claude-agent-acp") is None


def test_only_the_claude_bridge_is_checked_and_absence_is_not_outdated(monkeypatch):
    monkeypatch.setattr(readiness.shutil, "which", lambda name: None)
    assert readiness.outdated_bridge("claude-agent-acp") is None
    assert readiness.outdated_bridge("npx @google/gemini-cli --acp") is None


def test_an_outdated_bridge_is_unverified_and_carries_its_fix():
    state = readiness._bridge_readiness(
        {
            "agent_key": "claude-acp",
            "command": "claude-agent-acp",
            "available": True,
            "logged_in": True,
            "outdated": {
                "installed": "@zed-industries/claude-agent-acp 0.21.0",
                "fix": "npm x",
            },
        }
    )
    assert state.state == UNVERIFIED
    assert state.usable
    assert "outdated (@zed-industries/claude-agent-acp 0.21.0) — npm x" in state.detail
    assert state.upgrade == "npm x"


# ── Upgrading it ──


def test_setup_upgrades_an_outdated_default_bridge(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("CONDOR_DEFAULT_AGENT=claude-acp:sonnet\n")
    monkeypatch.setattr(setup_llm, "ENV_PATH", env_file)
    monkeypatch.setattr(setup_llm.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(setup_llm, "choose", lambda *a, **k: None)  # picker skipped
    outdated = Readiness(UNVERIFIED, "outdated", upgrade="npm up")

    async def probe_all(bases, env=None):
        return {b: outdated for b in bases}

    async def probe(base, env=None):
        return Readiness(READY, "installed and logged in")

    ran = []
    monkeypatch.setattr(setup_llm.readiness, "probe_all", probe_all)
    monkeypatch.setattr(setup_llm.readiness, "probe", probe)
    monkeypatch.setattr(
        setup_llm.subprocess,
        "run",
        lambda cmd, shell=False: ran.append((cmd, shell))
        or SimpleNamespace(returncode=0),
    )

    assert setup_llm.main([]) == 0
    assert ran == [("npm up", True)]
    assert "installed and logged in" in capsys.readouterr().out


def test_setup_leaves_a_missing_bridge_to_the_installer():
    assert setup_llm._upgrade_bridge(Readiness(MISSING, "not installed")) is False


def test_the_condor_update_upgrades_an_outdated_bridge(monkeypatch):
    ran = []

    async def run_cmd(*args, **kw):
        ran.append(args)
        return 0, ""

    monkeypatch.setattr(updater, "_run_cmd", run_cmd)
    monkeypatch.setattr(
        readiness,
        "outdated_bridge",
        lambda cmd: {
            "installed": "@zed-industries/claude-agent-acp 0.21.0",
            "fix": "npm up",
        },
    )

    ok, output = asyncio.run(updater.install_dependencies())

    assert ok
    assert ran == [("uv", "sync"), ("sh", "-c", "npm up")]
    assert (
        "Upgraded the Claude bridge (was @zed-industries/claude-agent-acp 0.21.0)"
        in output
    )


def test_a_failed_bridge_upgrade_does_not_fail_the_update(monkeypatch):
    async def run_cmd(*args, **kw):
        return (0, "synced") if args[0] == "uv" else (1, "EACCES")

    monkeypatch.setattr(updater, "_run_cmd", run_cmd)
    monkeypatch.setattr(
        readiness,
        "outdated_bridge",
        lambda cmd: {"installed": "old 0.1", "fix": "npm up"},
    )

    ok, output = asyncio.run(updater.install_dependencies())

    assert ok
    assert "could not be upgraded — run `npm up`" in output
    assert "EACCES" in output


def test_a_current_bridge_is_left_alone_by_the_update(monkeypatch):
    ran = []

    async def run_cmd(*args, **kw):
        ran.append(args)
        return 0, "synced"

    monkeypatch.setattr(updater, "_run_cmd", run_cmd)
    monkeypatch.setattr(readiness, "outdated_bridge", lambda cmd: None)

    assert asyncio.run(updater.install_dependencies()) == (True, "synced")
    assert ran == [("uv", "sync")]
