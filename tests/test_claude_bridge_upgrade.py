"""The Claude ACP bridge: picking its model in either shape, and keeping it current.

@zed-industries/claude-agent-acp (deprecated, frozen at 0.23) bundles a Claude
CLI that bills Sonnet 4.6 at Opus rates. Its successor,
@agentclientprotocol/claude-agent-acp, moved model selection from a ``models``
block to a ``configOptions`` entry — reading only the old block would silently
leave every pinned brain on the default model.
"""

import asyncio
import copy
import json
import os
from types import SimpleNamespace

import pytest

from condor import setup_llm
from condor.acp import client as acp_client
from condor.acp.client import (
    ACPClient,
    ModelPinError,
    advertised_models,
    resolve_acp,
    resolve_model_id,
)
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

# The session/new a bridge 0.79 hands back when nothing pins its model:
# measured 2026-10-18 with ANTHROPIC_MODEL unset. With the pin on the env the
# bridge appended it to these options and reported it as currentValue, so every
# pin matched itself and the switch was never sent (CORR-746).
UNPINNED_SESSION = {
    "sessionId": "s",
    "configOptions": [
        {
            "id": "model",
            "category": "model",
            "currentValue": "default",
            "options": [
                {"value": "default", "name": "Default (recommended)"},
                {"value": "opus[1m]", "name": "Opus 5"},
                {"value": "claude-fable-5-1[1m]", "name": "Fable 5.1"},
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


def _client_with_fake_peer(model: str, refuse: Exception | None = None):
    """A client whose peer records requests instead of sending them.

    ``returncode``/``cancel_all`` are what ``stop()`` reads: the failure paths
    kill the bridge, and a fake that cannot be stopped would fail on the
    teardown rather than on what the test is about.
    """
    sent = []

    async def send_request(method, params, stdin, **kw):
        sent.append((method, params))
        if refuse is not None:
            raise refuse
        return {}

    client = ACPClient("claude-agent-acp", model=model)
    client._session_id = "s"
    client._process = SimpleNamespace(stdin=None, returncode=0)
    client._peer = SimpleNamespace(send_request=send_request, cancel_all=lambda: None)
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


# ── A pin the bridge cannot apply fails the session (issue #221) ──


def _recorder(log: list):
    async def stop():
        log.append(True)

    return stop


def test_an_unoffered_pin_fails_the_session_naming_what_is_offered():
    client, sent = _client_with_fake_peer("fable")

    with pytest.raises(ModelPinError) as err:
        asyncio.run(client._select_model(CONFIG_OPTIONS_SESSION))

    message = str(err.value)
    assert "'fable'" in message
    # What to pin instead, and the default it is NOT silently running on.
    assert "default, opus[1m], sonnet, haiku" in message
    assert "opus[1m]" in message
    assert sent == []  # nothing was switched, so nothing was sent


def test_a_refused_switch_fails_the_session_instead_of_keeping_the_default():
    client, sent = _client_with_fake_peer("haiku", refuse=RuntimeError("nope"))

    with pytest.raises(ModelPinError) as err:
        asyncio.run(client._select_model(CONFIG_OPTIONS_SESSION))

    assert "session/set_config_option" in str(err.value)
    assert "nope" in str(err.value)
    assert sent  # it did try
    # Never reported as the effective model: the session is not running on it.
    assert client.active_model_id == "opus[1m]"


def test_a_failed_pin_stops_the_bridge_rather_than_orphaning_it():
    client, _ = _client_with_fake_peer("fable")
    stopped = []
    client.stop = _recorder(stopped)

    with pytest.raises(ModelPinError):
        asyncio.run(client._select_model(CONFIG_OPTIONS_SESSION))

    assert stopped == [True]


def test_an_unpinned_bridge_is_actually_asked_to_switch():
    """The catalogue is honest, so the pin differs from current and is sent."""
    client, sent = _client_with_fake_peer("haiku")

    asyncio.run(client._select_model(UNPINNED_SESSION))

    assert sent == [
        (
            "session/set_config_option",
            {"sessionId": "s", "configId": "model", "value": "haiku"},
        )
    ]
    assert client.active_model_id == "haiku"


def test_an_unpinned_bridge_refuses_a_pin_it_does_not_offer():
    """With the pin off the env, a bogus one cannot match itself any more."""
    client, sent = _client_with_fake_peer("gpt-4o")

    with pytest.raises(ModelPinError) as err:
        asyncio.run(client._select_model(UNPINNED_SESSION))

    message = str(err.value)
    assert "'gpt-4o'" in message
    assert "default, opus[1m], claude-fable-5-1[1m], sonnet, haiku" in message
    assert sent == []
    # Never reported as the effective model on /agents (FEAT-136's audit field).
    assert client.active_model_id != "gpt-4o"


def _session_as_bridge_079_answers(env: dict[str, str]) -> dict:
    """``UNPINNED_SESSION``, plus whatever ``ANTHROPIC_MODEL`` does to it.

    Measured 2026-10-18 against @agentclientprotocol/claude-agent-acp 0.79.0:
    the variable's value is appended to the advertised options and reported as
    ``currentValue``, unchecked — ``gpt-4o`` and ``sonnet-typo-xyz`` came back
    exactly like a real alias.
    """
    session = copy.deepcopy(UNPINNED_SESSION)
    pinned = env.get("ANTHROPIC_MODEL")
    if pinned:
        option = session["configOptions"][0]
        option["options"].append({"value": pinned, "name": pinned})
        option["currentValue"] = pinned
    return session


def test_a_bogus_pin_cannot_smuggle_itself_in_through_the_environment():
    """The pin travels only on the protocol, so the bridge cannot echo it back.

    While resolve_acp also exported it, the bridge advertised the pin and
    reported it as current: target == current, nothing sent, no raise, and the
    typo went on to /agents as the effective model (CORR-746).
    """
    _, env, pref = resolve_acp("claude-acp:gpt-4o")
    client, sent = _client_with_fake_peer(pref)

    with pytest.raises(ModelPinError) as err:
        asyncio.run(client._select_model(_session_as_bridge_079_answers(env)))

    assert "'gpt-4o'" in str(err.value)
    assert sent == []
    assert client.active_model_id != "gpt-4o"


def test_an_unpinned_session_is_untouched_by_the_failure_path():
    client, sent = _client_with_fake_peer("")
    asyncio.run(client._select_model(CONFIG_OPTIONS_SESSION))
    assert sent == []
    assert client.active_model_id == "opus[1m]"


# ── The doctor probe, which asks the bridge what it really offers ──


def _patch_probe_client(monkeypatch, start, offers=("default", "sonnet", "haiku")):
    """Replace the client ``probe_model_pin`` launches.

    Returns ``(stopped, launched)``: whether the bridge was stopped, and the
    ``(command, kwargs)`` it was launched with — the probe must launch it
    *unpinned*, since an ANTHROPIC_MODEL handed to the bridge would make it
    advertise the very pin under test.
    """
    stopped, launched = [], []

    class _FakeClient:
        def __init__(self, command, **kw):
            launched.append((command, kw))
            self.available_models = [{"modelId": m, "name": m} for m in offers]
            self.active_model_id = offers[0] if offers else None

        async def start(self):
            await start()

        async def stop(self):
            stopped.append(True)

    monkeypatch.setattr(acp_client, "ACPClient", _FakeClient)
    return stopped, launched


async def _started():
    return None


def test_probe_model_pin_passes_a_pin_the_bridge_offers(monkeypatch):
    stopped, launched = _patch_probe_client(monkeypatch, _started)

    ok, detail = asyncio.run(acp_client.probe_model_pin("claude-acp:haiku"))

    assert ok is True
    assert "'haiku'" in detail
    assert stopped == [True]


def test_probe_model_pin_asks_the_bridge_unpinned(monkeypatch):
    """The pin must not be handed to the bridge: it would echo it back as offered."""
    _, launched = _patch_probe_client(monkeypatch, _started)

    asyncio.run(acp_client.probe_model_pin("claude-acp:haiku"))

    ((command, kwargs),) = launched
    assert command == "claude-agent-acp"
    assert not kwargs  # no model=, and no ANTHROPIC_MODEL in an extra_env


def test_probe_model_pin_fails_a_pin_the_bridge_does_not_offer(monkeypatch):
    _patch_probe_client(monkeypatch, _started)

    ok, detail = asyncio.run(acp_client.probe_model_pin("claude-acp:gpt-4o"))

    assert ok is False
    assert "'gpt-4o'" in detail
    assert "default, sonnet, haiku" in detail  # what to pin instead


def test_probe_model_pin_fails_a_bridge_that_offers_nothing(monkeypatch):
    _patch_probe_client(monkeypatch, _started, offers=())

    ok, detail = asyncio.run(acp_client.probe_model_pin("claude-acp:haiku"))

    assert ok is False  # a pin nothing can apply is not a pass
    assert "offers: nothing" in detail


def test_probe_model_pin_reports_a_silent_bridge_as_not_checkable(monkeypatch):
    async def never():
        await asyncio.sleep(5)

    stopped, _ = _patch_probe_client(monkeypatch, never)

    ok, detail = asyncio.run(
        acp_client.probe_model_pin("claude-acp:haiku", timeout=0.05)
    )

    assert ok is None  # not a pass: nothing was verified
    assert "did not answer" in detail
    assert stopped == [True]


def test_probe_model_pin_reports_a_bridge_that_will_not_launch(monkeypatch):
    async def dead():
        raise ConnectionError("ACP agent exited")

    _patch_probe_client(monkeypatch, dead)

    ok, detail = asyncio.run(acp_client.probe_model_pin("claude-acp:haiku"))

    assert ok is None
    assert "ACP agent exited" in detail


def test_probe_model_pin_has_nothing_to_check_without_a_pin(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("no bridge should be launched for an unpinned key")

    monkeypatch.setattr(acp_client, "ACPClient", fail)

    ok, detail = asyncio.run(acp_client.probe_model_pin("claude-acp"))

    assert ok is True
    assert "no model pinned" in detail


# ── Matching a pin against a catalogue of bare aliases (CORR-745) ──
#
# The installed bridge answers an unpinned session/new with short aliases, not
# full dated ids, so the matcher's reverse direction is exercised only here.
# Unanchored, that direction accepted any string merely *containing* an alias,
# and the doctor row that exists to catch a mistyped pin passed one.

#: What @agentclientprotocol/claude-agent-acp 0.79.0 really advertises for an
#: unpinned session, measured 2026-10-18 over one initialize + session/new
#: handshake. A literal on purpose: the gate needs no bridge installed and no
#: login, and a later bridge advertising other aliases cannot red it.
MEASURED_ALIAS_CATALOGUE = [
    {"modelId": "default", "name": "Default (recommended)"},
    {"modelId": "opus[1m]", "name": "Opus 5"},
    {"modelId": "claude-fable-5-1[1m]", "name": "Fable 5.1"},
    {"modelId": "sonnet", "name": "Sonnet 5"},
    {"modelId": "haiku", "name": "Haiku 4.5"},
]


@pytest.mark.parametrize("pin", ["sonnet-typo-xyz", "no-sonnet", "haiku!!", "SONNETT"])
def test_a_pin_merely_containing_an_alias_resolves_to_nothing(pin):
    assert resolve_model_id(pin, MEASURED_ALIAS_CATALOGUE) is None


@pytest.mark.parametrize(
    "pin,expected",
    [
        ("haiku", "haiku"),
        ("sonnet", "sonnet"),
        ("opus", "opus[1m]"),
        ("claude-sonnet-4-6", "sonnet"),
        ("claude-haiku-4-5-20251001", "haiku"),
        # An advertised family carrying a context suffix: 'opus[1m]' is not a
        # substring of this pin in either direction, so only the anchored
        # family-plus-version reading resolves it.
        ("claude-opus-4-6", "opus[1m]"),
    ],
)
def test_an_alias_or_a_versioned_form_of_one_still_resolves(pin, expected):
    assert resolve_model_id(pin, MEASURED_ALIAS_CATALOGUE) == expected


@pytest.mark.parametrize("pin", ["sonnet-typo-xyz", "no-sonnet"])
def test_the_doctor_probe_fails_a_typo_of_an_advertised_alias(monkeypatch, pin):
    """The row added so a mistyped pin is caught must not report it as fine."""
    _patch_probe_client(
        monkeypatch, _started, offers=[m["modelId"] for m in MEASURED_ALIAS_CATALOGUE]
    )

    ok, detail = asyncio.run(acp_client.probe_model_pin(f"claude-acp:{pin}"))

    assert ok is False
    assert f"{pin!r}" in detail
    assert "default, opus[1m], claude-fable-5-1[1m], sonnet, haiku" in detail


def test_the_doctor_probe_still_passes_a_pin_that_catalogue_offers(monkeypatch):
    _patch_probe_client(
        monkeypatch, _started, offers=[m["modelId"] for m in MEASURED_ALIAS_CATALOGUE]
    )

    ok, detail = asyncio.run(acp_client.probe_model_pin("claude-acp:haiku"))

    assert ok is True
    assert "'haiku' resolves to 'haiku'" in detail


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
