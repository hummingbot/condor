"""A message sent mid-answer is not dropped (FEAT-030).

The queue was already there — ``Session._lock`` is FIFO and has been since
FEAT-008 — but both surfaces rejected on ``is_busy`` *before* they ever reached
it, so it was built and unreachable. These tests pin the three policies that
replace that early return: ``reject`` (unchanged), ``queue`` (wait your turn)
and ``steer`` (stop the turn ahead, keep its context).
"""

import asyncio
from dataclasses import replace

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.confirmations import ConfirmationRegistry, ConfirmationStatus
from condor.runtime.events import EventType

KEY = SessionKey.telegram(7)


class _GatedClient:
    """A client whose turn hangs until it is released — or cancelled.

    Lets a test hold one turn open while a second prompt arrives, which is the
    only interesting state this feature has.
    """

    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        self.finished: list[str] = []
        self.aborts = 0
        self._release = asyncio.Event()
        self._cancelled = False
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def abort_prompt(self):
        """What FEAT-029 made real: the turn in flight actually ends."""
        self.aborts += 1
        self._cancelled = True
        self._release.set()

    def finish_turn(self):
        self._release.set()

    async def prompt_stream(self, text):
        self.prompts.append(text)
        self._release.clear()
        self._cancelled = False
        yield TextChunk(text=f"answering {text}")
        await self._release.wait()
        if self._cancelled:
            yield PromptDone(stop_reason="cancelled")
        else:
            self.finished.append(text)
            yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _GatedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


async def _open_session() -> _GatedClient:
    await runtime.create_session(
        SessionSpec(key=str(KEY), agent_key="claude-code", chat_id=7, user_id=7)
    )
    return _GatedClient.last


async def _collect(text: str, **kwargs) -> list:
    return [e async for e in runtime.prompt(KEY, PromptRequest(text=text), **kwargs)]


async def _until_busy() -> None:
    """Wait for the session to actually hold the lock."""
    for _ in range(200):
        session = session_module.get_session(KEY)
        if session and session.is_busy:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("session never became busy")


def _types(events) -> list:
    return [e.type for e in events]


# ── queue ──


def test_two_queued_prompts_are_answered_in_the_order_they_were_sent(registry):
    async def scenario():
        client = await _open_session()

        first = asyncio.create_task(_collect("first", on_busy="queue"))
        await _until_busy()

        second = asyncio.create_task(_collect("second", on_busy="queue"))
        third = asyncio.create_task(_collect("third", on_busy="queue"))
        await asyncio.sleep(0.05)

        # Both are waiting on the lock, not running: the agent has only ever
        # been asked the first question.
        assert client.prompts == ["first"]

        client.finish_turn()
        events_first = await asyncio.wait_for(first, timeout=5)

        await _until_busy()
        client.finish_turn()
        events_second = await asyncio.wait_for(second, timeout=5)

        await _until_busy()
        client.finish_turn()
        events_third = await asyncio.wait_for(third, timeout=5)

        return client, events_first, events_second, events_third

    client, first, second, third = asyncio.run(scenario())

    assert client.finished == ["first", "second", "third"]
    # The one that ran immediately was never queued...
    assert _types(first) == [EventType.TEXT, EventType.DONE]
    # ...and the ones that waited said so before blocking.
    assert _types(second) == [EventType.QUEUED, EventType.TEXT, EventType.DONE]
    assert _types(third) == [EventType.QUEUED, EventType.TEXT, EventType.DONE]
    assert second[1].text == "answering second"
    assert third[1].text == "answering third"


def test_a_queued_message_does_not_trip_the_stuck_prompt_timeout(registry, monkeypatch):
    """The 30s guard is for a *stuck* prompt, not for one deliberately waiting."""
    monkeypatch.setattr(session_module, "PROMPT_LOCK_TIMEOUT", 0.05)
    monkeypatch.setattr(runtime, "TIMEOUTS", replace(runtime.TIMEOUTS, prompt_queue=10))

    async def scenario():
        client = await _open_session()
        first = asyncio.create_task(_collect("first", on_busy="queue"))
        await _until_busy()
        second = asyncio.create_task(_collect("second", on_busy="queue"))

        # Far longer than the stuck-prompt deadline the queue must not use.
        await asyncio.sleep(0.3)

        client.finish_turn()
        await asyncio.wait_for(first, timeout=5)
        await _until_busy()
        client.finish_turn()
        return client, await asyncio.wait_for(second, timeout=5)

    client, events = asyncio.run(scenario())

    assert EventType.ERROR not in _types(events)
    assert client.finished == ["first", "second"]


# ── reject: the default, unchanged ──


def test_reject_is_the_default_and_keeps_the_stuck_prompt_deadline(
    registry, monkeypatch
):
    """No caller that has not opted in changes behaviour."""
    monkeypatch.setattr(session_module, "PROMPT_LOCK_TIMEOUT", 0.05)

    async def scenario():
        client = await _open_session()
        first = asyncio.create_task(_collect("first"))
        await _until_busy()

        events = await asyncio.wait_for(_collect("second"), timeout=5)

        client.finish_turn()
        await asyncio.wait_for(first, timeout=5)
        return client, events

    client, events = asyncio.run(scenario())

    assert _types(events) == [EventType.ERROR, EventType.DONE]
    assert "not responding" in events[0].field("message")
    # Nothing was steered aside on the caller's behalf.
    assert client.aborts == 0
    assert client.finished == ["first"]


# ── steer ──


def test_steer_stops_the_turn_ahead_and_takes_over(registry):
    async def scenario():
        client = await _open_session()
        first = asyncio.create_task(_collect("first", on_busy="steer"))
        await _until_busy()

        second = asyncio.create_task(_collect("second", on_busy="steer"))
        events_first = await asyncio.wait_for(first, timeout=5)

        await _until_busy()
        client.finish_turn()
        events_second = await asyncio.wait_for(second, timeout=5)
        return client, events_first, events_second

    client, first, second = asyncio.run(scenario())

    assert client.aborts == 1
    # The interrupted turn ends where the screen did...
    assert first[-1].type == EventType.DONE
    assert first[-1].stop_reason == "cancelled"
    assert client.finished == ["second"]
    # ...and the new turn went to the *same* session, so the agent keeps
    # everything the interrupted one had worked out.
    assert client.prompts == ["first", "second"]
    assert _types(second) == [EventType.QUEUED, EventType.TEXT, EventType.DONE]


def test_steer_denies_the_confirmation_the_abandoned_turn_was_holding(
    registry, monkeypatch
):
    """A redirected agent must not still hold a dialog for abandoned work."""
    confirmations = ConfirmationRegistry()
    monkeypatch.setattr("condor.runtime.confirmations._registry", confirmations)

    async def scenario():
        client = await _open_session()
        first = asyncio.create_task(_collect("first", on_busy="steer"))
        await _until_busy()

        pending = confirmations.register(
            session_key=str(KEY),
            user_id=7,
            summary="Place a 10 BTC order",
            tool_call={},
            options=[],
        )
        other = confirmations.register(
            session_key=str(SessionKey.telegram(99)),
            user_id=9,
            summary="Unrelated",
            tool_call={},
            options=[],
        )

        second = asyncio.create_task(_collect("second", on_busy="steer"))
        await asyncio.wait_for(first, timeout=5)
        await _until_busy()
        client.finish_turn()
        await asyncio.wait_for(second, timeout=5)
        return pending, other

    pending, other = asyncio.run(scenario())

    # Resolved now, rather than stalling the agent for the full 120s TTL.
    assert pending.status is ConfirmationStatus.DENIED
    # Another session's dialog is none of this turn's business.
    assert other.status is ConfirmationStatus.PENDING
