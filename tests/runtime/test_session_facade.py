"""Tests for the session runtime facade (FEAT-008).

The load-bearing one is ``test_hot_reload_does_not_clear_registry``: session
state used to live in ``handlers/agents/session.py``, which ``reload_handlers()``
re-executes on every handler edit, so editing any handler reset the registry to
``{}`` and orphaned every live agent subprocess.
"""

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from condor.runtime import SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import sessions as session_module

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeClient:
    """Stand-in for ACPClient that counts lifecycle calls instead of spawning."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.alive = True
        self.start_calls = 0
        self.stop_calls = 0
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        self.start_calls += 1

    async def stop(self):
        self.stop_calls += 1
        self.alive = False

    async def prompt(self, text):
        self.prompts.append(text)


@pytest.fixture
def registry(monkeypatch):
    """Empty registry with the subprocess + context machinery stubbed out."""
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _FakeClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    # Lives in condor.runtime.binding now; patch it at the source so both the
    # bound and unbound resolution paths see the stub.
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


def _spec(key: SessionKey, agent_key: str = "claude-code") -> SessionSpec:
    return SessionSpec(key=str(key), agent_key=agent_key, chat_id=1234, user_id=42)


# ── Keys ──


def test_key_roundtrip():
    """Every surface survives str() -> parse() unchanged."""
    for key in (
        SessionKey.telegram(12345),
        SessionKey.telegram(-100987),
        SessionKey.web(42, "ab12"),
        SessionKey.mcp(42),
    ):
        assert SessionKey.parse(str(key)) == key

    # Legacy shapes still resolve to the same session.
    assert SessionKey.legacy_from("web_42_ab12") == SessionKey.web(42, "ab12")
    assert SessionKey.legacy_from(12345) == SessionKey.telegram(12345)
    assert SessionKey.legacy_from("12345") == SessionKey.telegram(12345)
    assert SessionKey.legacy_from("tg:12345") == SessionKey.telegram(12345)

    # Only Telegram keys carry a chat id.
    assert SessionKey.telegram(555).telegram_chat_id == 555
    assert SessionKey.web(42, "s1").telegram_chat_id is None

    with pytest.raises(ValueError):
        SessionKey.parse("nonsense")


# ── Registry lifecycle ──


def test_create_reuses_alive_session(registry):
    """Provisioning the same spec twice starts exactly one client."""
    key = SessionKey.telegram(1234)

    async def scenario():
        first = await runtime.create_session(_spec(key))
        second = await runtime.create_session(_spec(key))
        return first, second

    first, second = asyncio.run(scenario())

    assert first.key == second.key == str(key)
    assert _FakeClient.last.start_calls == 1
    assert _FakeClient.last.stop_calls == 0
    assert len(registry._sessions) == 1


def test_session_key_reaches_the_subprocess_env(registry):
    """The spawned client is told which session it belongs to (FEAT-021).

    The MCP child inherits this env, and the condor server posts it back on
    ``delegate(action="start")`` so a delegation can be attributed to the
    conversation that asked for it. If it were missing the field would be
    silently empty, which is why it is asserted at the spawn boundary.
    """
    key = SessionKey.web(42, "slot-1")

    asyncio.run(runtime.create_session(_spec(key)))

    env = _FakeClient.last.kwargs["extra_env"]
    assert env["CONDOR_SESSION_KEY"] == str(key) == "web:42:slot-1"


def test_create_replaces_on_agent_key_change(registry):
    """A different agent_key tears the old session down exactly once."""
    key = SessionKey.telegram(1234)

    async def scenario():
        await runtime.create_session(_spec(key, "claude-code"))
        old_client = registry.get_session(key).client
        await runtime.create_session(_spec(key, "gemini"))
        return old_client, registry.get_session(key)

    old_client, current = asyncio.run(scenario())

    assert old_client.stop_calls == 1
    assert current.agent_key == "gemini"
    assert current.client is not old_client
    assert len(registry._sessions) == 1


def test_destroy_and_list(registry):
    """list_sessions filters by owner; destroy reports whether it hit anything."""
    tg = SessionKey.telegram(1234)
    web = SessionKey.web(99, "slot1")

    async def scenario():
        await runtime.create_session(_spec(tg))
        await runtime.create_session(
            SessionSpec(key=str(web), agent_key="claude-code", user_id=99)
        )
        mine = await runtime.list_sessions(user_id=42)
        everyone = await runtime.list_sessions()
        destroyed = await runtime.destroy(tg)
        missing = await runtime.destroy(SessionKey.telegram(404))
        return mine, everyone, destroyed, missing

    mine, everyone, destroyed, missing = asyncio.run(scenario())

    assert [info.key for info in mine] == [str(tg)]
    assert len(everyone) == 2
    assert destroyed is True
    assert missing is False
    assert str(tg) not in registry._sessions


# ── The regression this feature exists for ──


def test_hot_reload_does_not_clear_registry(registry):
    """Reloading a handler module must not orphan live sessions.

    ``reload_handlers()`` re-executes every module in its list. While the
    registry lived in ``handlers/agents/session.py`` that wiped ``_sessions``
    and left the agent subprocesses running with nothing pointing at them.
    """
    key = SessionKey.telegram(1234)
    asyncio.run(runtime.create_session(_spec(key)))
    assert len(asyncio.run(runtime.list_sessions())) == 1

    # Simulate reload_handlers(): re-execute the handler modules that used to
    # own session state. Import first — under a narrow pytest selection they
    # may not have been pulled in yet.
    for name in ("handlers.agents.session", "handlers.agents.menu"):
        importlib.reload(importlib.import_module(name))
    assert "handlers.agents.session" in sys.modules

    still_there = asyncio.run(runtime.list_sessions())
    assert [info.key for info in still_there] == [str(key)]
    assert registry.get_session(key).client.alive


# ── Import discipline ──


def test_no_direct_registry_imports():
    """Only condor/runtime/ may import the registry's symbols directly.

    Everything else goes through ``condor.runtime.client`` so that swapping the
    transport later is a config flip instead of a rewrite.
    """
    # Assembled at runtime so this file does not match its own search string.
    pattern = "from condor.runtime." + "sessions import"
    result = subprocess.run(
        ["git", "grep", "-l", pattern, "--", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    offenders = [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith("condor/runtime/")
    ]
    assert (
        offenders == []
    ), f"Must import from condor.runtime.client instead: {offenders}"
