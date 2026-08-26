"""A session that nobody comes back to is detached (PERF-226).

Every other deadline in ``condor.runtime.timeouts`` bounds a *turn*; this one
bounds a *session*. Without it the only exits were the user pressing X, the
subprocess dying, the LRU, or a restart — so five slots filled with agent trees
held open for conversations abandoned days earlier.

These pin the four properties the sweep is worth nothing without: it reaps what
is idle, it never touches what is busy, it respects the boundary, and it is
switched off by a zero. Plus the two that make the detach safe to do at all:
the conversation resumes, and the pending approvals go with it.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import sessions as session_module
from condor.runtime.conversations import read_transcript
from condor.runtime.timeouts import ENV_PREFIX, TimeoutPolicy

USER = 77
TTL = 3600


class _ScriptedClient:
    def __init__(self, **kwargs):
        self.alive = True

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "summary"

    async def prompt_stream(self, text):
        yield TextChunk(text="the answer")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def registry(monkeypatch, isolated_conversation_root):
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _ScriptedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    _set_ttl(monkeypatch, TTL)
    return session_module


def _set_ttl(monkeypatch, seconds: int) -> None:
    """Retune the session TTL the way a deployment's env var would."""
    monkeypatch.setattr(
        session_module,
        "TIMEOUTS",
        replace(session_module.TIMEOUTS, session_idle=seconds),
    )


async def _start(slot: str) -> str:
    info = await runtime.create_session(
        SessionSpec(
            key=str(SessionKey.web(USER, slot)),
            agent_key="claude-code",
            user_id=USER,
        )
    )
    return info.conversation_id


async def _chat(key: SessionKey, text: str) -> None:
    async for _ in runtime.prompt(key, PromptRequest(text=text)):
        pass


def _age(slot: str, seconds: float) -> None:
    """Backdate the slot's last prompt so it reads as ``seconds`` idle."""
    session = session_module._sessions[str(SessionKey.web(USER, slot))]
    session.last_prompt_at = session_module._utcnow() - timedelta(seconds=seconds)


def _live() -> set[str]:
    return set(session_module._sessions)


# ── The sweep ──


def test_an_idle_session_is_detached_and_a_warm_one_is_not(registry):
    """The whole point: four days of silence costs the slot, a minute does not."""

    async def scenario():
        for slot in ("cold", "warm"):
            await _start(slot)
            await _chat(SessionKey.web(USER, slot), "hi")
        _age("cold", TTL * 96)
        _age("warm", 60)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.web(USER, "warm")) in _live()
    assert str(SessionKey.web(USER, "cold")) not in _live(), "the idle one went"


def test_a_busy_session_is_never_reaped(registry):
    """``last_prompt_at`` is stamped when a turn *starts*, so age proves nothing.

    A five-hour answer is the exact case a naive age check would kill mid-write.
    """

    async def scenario():
        await _start("thinking")
        session = session_module._sessions[str(SessionKey.web(USER, "thinking"))]
        session.is_busy = True
        _age("thinking", TTL * 100)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.web(USER, "thinking")) in _live()


def test_the_boundary_is_strictly_older_than_the_ttl(registry, monkeypatch):
    """At the TTL it stays; a second past it goes.

    The clock is frozen for this one, because a boundary measured against a
    moving ``now`` is a coin flip on the microseconds between the two reads.
    """

    async def scenario():
        await _start("on-the-line")
        await _start("just-over")
        now = session_module._utcnow()
        monkeypatch.setattr(session_module, "_utcnow", lambda: now)
        _age("on-the-line", TTL)
        _age("just-over", TTL + 1)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.web(USER, "on-the-line")) in _live()
    assert str(SessionKey.web(USER, "just-over")) not in _live()


def test_zero_disables_the_sweep(registry, monkeypatch):
    """An installation that wants sessions to live forever says so with a 0."""
    _set_ttl(monkeypatch, 0)

    async def scenario():
        await _start("ancient")
        _age("ancient", 86400 * 30)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.web(USER, "ancient")) in _live()


def test_a_never_prompted_session_ages_from_its_creation(registry):
    """A prewarm nobody typed into has no ``last_prompt_at`` — and still expires."""

    async def scenario():
        await _start("prewarmed")
        session = session_module._sessions[str(SessionKey.web(USER, "prewarmed"))]
        assert session.last_prompt_at is None
        session.created_at = session_module._utcnow() - timedelta(seconds=TTL + 1)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.web(USER, "prewarmed")) not in _live()


def test_the_reap_stops_the_subprocess(registry):
    """A detach that leaves the process tree running would fix nothing."""
    clients = []

    async def scenario():
        await _start("cold")
        clients.append(
            session_module._sessions[str(SessionKey.web(USER, "cold"))].client
        )
        _age("cold", TTL + 1)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert clients[0].alive is False


# ── What makes the detach safe ──


def test_the_detached_conversation_resumes_with_its_full_transcript(registry):
    """A detach, not a loss: the next session picks the transcript back up."""

    async def scenario():
        conv_id = await _start("cold")
        await _chat(SessionKey.web(USER, "cold"), "my favourite pair is SOL-USDC")
        _age("cold", TTL + 1)
        await session_module._sweep_sessions()

        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(USER, "cold")),
                agent_key="claude-code",
                user_id=USER,
                conversation_id=conv_id,
            )
        )
        await _chat(SessionKey.web(USER, "cold"), "what was it?")
        return conv_id

    conv_id = asyncio.run(scenario())

    assert [t.text for t in read_transcript(USER, conv_id)] == [
        "my favourite pair is SOL-USDC",
        "the answer",
        "what was it?",
        "the answer",
    ]


def test_the_reap_denies_the_session_pending_confirmations(registry, monkeypatch):
    """It goes out through ``_destroy_session_internal``, so the taps expire."""
    denied: list[str] = []

    class _Reg:
        def deny_pending_for_session(self, raw_key):
            denied.append(raw_key)
            return 1

    monkeypatch.setattr(
        session_module, "get_confirmation_registry", lambda: _Reg(), raising=True
    )

    async def scenario():
        await _start("cold")
        _age("cold", TTL + 1)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert denied == [str(SessionKey.web(USER, "cold"))]


def test_an_idle_telegram_chat_is_detached_silently(registry, monkeypatch):
    """Nothing failed, so nothing is announced — unlike a dead subprocess.

    The chat respawns onto its recorded conversation on the next message; a
    "session ended unexpectedly" here would be a false alarm.
    """
    sent: list[tuple[int, str]] = []

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))

    monkeypatch.setattr(session_module, "_health_bot", _Bot())

    async def scenario():
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.telegram(USER)),
                agent_key="claude-code",
                chat_id=USER,
                user_id=USER,
            )
        )
        session = session_module._sessions[str(SessionKey.telegram(USER))]
        session.created_at = session_module._utcnow() - timedelta(seconds=TTL + 1)
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.telegram(USER)) not in _live()
    assert sent == [], "an idle detach is not the death notice"


def test_a_dead_session_is_still_reaped_as_a_fault(registry, monkeypatch):
    """The branch this sweep was extended from still says what it said."""
    sent: list[tuple[int, str]] = []

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))

    monkeypatch.setattr(session_module, "_health_bot", _Bot())

    async def scenario():
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.telegram(USER)),
                agent_key="claude-code",
                chat_id=USER,
                user_id=USER,
            )
        )
        session = session_module._sessions[str(SessionKey.telegram(USER))]
        session.client.alive = False
        session.is_busy = True
        await session_module._sweep_sessions()

    asyncio.run(scenario())

    assert str(SessionKey.telegram(USER)) not in _live()
    assert len(sent) == 1
    assert "unexpectedly" in sent[0][1]


# ── The knob ──


def test_the_ttl_is_retunable_by_env(monkeypatch):
    """Same ``CONDOR_TIMEOUT_*`` contract as every other deadline."""
    assert TimeoutPolicy().session_idle == 3600

    monkeypatch.setenv(f"{ENV_PREFIX}SESSION_IDLE", "900")
    assert TimeoutPolicy.load().session_idle == 900

    monkeypatch.setenv(f"{ENV_PREFIX}SESSION_IDLE", "0")
    assert TimeoutPolicy.load().session_idle == 0

    # A typo must not stop the bot; the default stands.
    monkeypatch.setenv(f"{ENV_PREFIX}SESSION_IDLE", "an hour")
    assert TimeoutPolicy.load().session_idle == 3600
