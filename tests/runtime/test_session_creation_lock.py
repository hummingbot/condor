"""Concurrent session creation is serialized per key (CORR-187).

``get_or_create_session`` is a check-then-create that spans many awaits
(subprocess spawn, eager context prompt). Before the per-key creation lock,
two concurrent calls for the same key both spawned a full client and the
second registration overwrote the first, whose process tree leaked until bot
shutdown; the session budget had the same TOCTOU. These tests *race* real
concurrent calls — they do not merely assert that a lock object exists.
"""

import asyncio

import pytest

from condor.runtime import SessionKey, SessionSpec
from condor.runtime import sessions as session_module


class _SlowClient:
    """Client stub whose start() yields to the loop, opening the race window."""

    instances: list = []
    started = 0
    stopped = 0
    # When set, start() blocks until the event fires (for the parallelism test).
    start_gate: asyncio.Event | None = None

    def __init__(self, **kwargs):
        self.alive = True
        type(self).instances.append(self)

    async def start(self):
        type(self).started += 1
        if type(self).start_gate is not None:
            await type(self).start_gate.wait()
        else:
            # Yield a few times so a concurrent caller can reach the registry
            # check while this creation is mid-flight.
            for _ in range(3):
                await asyncio.sleep(0)

    async def stop(self):
        self.alive = False
        type(self).stopped += 1

    async def prompt(self, text):
        return ""

    async def prompt_stream(self, text):
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def registry(monkeypatch):
    """Fresh module state with the subprocess client stubbed out."""
    _SlowClient.instances = []
    _SlowClient.started = 0
    _SlowClient.stopped = 0
    _SlowClient.start_gate = None
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "_pending_creates", {})
    monkeypatch.setattr(session_module, "_creation_locks", {})
    monkeypatch.setattr(session_module, "_creation_lock_refs", {})
    monkeypatch.setattr(session_module, "ACPClient", _SlowClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


def _spec(key: SessionKey, user_id: int = 42) -> SessionSpec:
    return SessionSpec(key=str(key), agent_key="claude-code", user_id=user_id)


def _assert_no_orphans(registry):
    """Every started client is either registered in _sessions or stopped."""
    registered = {s.client for s in registry._sessions.values()}
    for client in _SlowClient.instances:
        assert (
            client in registered or client.stopped or not client.alive
        ), "orphaned client: started but neither registered nor stopped"


def test_concurrent_same_key_spawns_once(registry):
    """Two racing creates for one key: one start(), one shared session."""
    key = SessionKey.web(42, "slot1")

    async def scenario():
        return await asyncio.gather(
            session_module.get_or_create_session(_spec(key)),
            session_module.get_or_create_session(_spec(key)),
        )

    first, second = asyncio.run(scenario())
    assert first is second
    assert _SlowClient.started == 1
    assert len(registry._sessions) == 1
    assert registry._sessions[str(key)] is first


def test_racing_creates_leave_no_orphan(registry):
    """N racing creates for one key never strand a started client."""
    key = SessionKey.web(42, "slot1")

    async def scenario():
        return await asyncio.gather(
            *(session_module.get_or_create_session(_spec(key)) for _ in range(5))
        )

    results = asyncio.run(scenario())
    assert len({id(s) for s in results}) == 1
    _assert_no_orphans(registry)
    assert _SlowClient.started == 1


def test_budget_holds_under_concurrent_distinct_creates(registry):
    """Racing creates of distinct keys never exceed MAX_SESSIONS_PER_USER."""
    cap = session_module.MAX_SESSIONS_PER_USER
    total = cap + 3

    async def scenario():
        return await asyncio.gather(
            *(
                session_module.get_or_create_session(
                    _spec(SessionKey.web(42, f"slot{i}"))
                )
                for i in range(total)
            ),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    sessions = [r for r in results if isinstance(r, session_module.AgentSession)]
    refused = [r for r in results if isinstance(r, session_module.SessionLimitReached)]
    unexpected = [
        r
        for r in results
        if not isinstance(
            r, (session_module.AgentSession, session_module.SessionLimitReached)
        )
    ]
    assert not unexpected
    assert len(sessions) == cap
    assert len(refused) == total - cap
    assert len([s for s in registry._sessions.values() if s.user_id == 42]) == cap
    # The refused creates never spawned anything, so nothing to orphan.
    assert _SlowClient.started == cap
    _assert_no_orphans(registry)


def test_distinct_keys_create_concurrently(registry):
    """Per-key locks: creations under different keys are not serialized."""
    _SlowClient.start_gate = asyncio.Event()  # replaced inside the loop

    async def scenario():
        gate = asyncio.Event()
        _SlowClient.start_gate = gate
        tasks = [
            asyncio.create_task(
                session_module.get_or_create_session(
                    _spec(SessionKey.web(42, f"slot{i}"))
                )
            )
            for i in range(2)
        ]
        # Both creations must reach start() while neither has finished —
        # a global creation lock would leave the second waiting forever.
        async with asyncio.timeout(2):
            while _SlowClient.started < 2:
                await asyncio.sleep(0)
        gate.set()
        return await asyncio.gather(*tasks)

    first, second = asyncio.run(scenario())
    assert first is not second
    assert len(registry._sessions) == 2


def test_fast_path_returns_without_spawning(registry):
    """An existing matching session is returned with no new spawn."""
    key = SessionKey.web(42, "slot1")

    async def scenario():
        created = await session_module.get_or_create_session(_spec(key))
        again = await session_module.get_or_create_session(_spec(key))
        return created, again

    created, again = asyncio.run(scenario())
    assert created is again
    assert _SlowClient.started == 1
    # The reservation bookkeeping is fully cleaned up after every create.
    assert registry._pending_creates == {}
    assert registry._creation_locks == {}
    assert registry._creation_lock_refs == {}
