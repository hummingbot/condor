"""PERF-280: nothing reads or rewrites the share queue on the event loop.

A queued record is a whole scrubbed transcript, and the queue holds up to
``MAX_QUEUED_SHARES`` of them. So every read-modify-write of that file is tens
to hundreds of milliseconds of ``json.loads`` — and the three places that do it
from a coroutine all sat on the one loop that polls Telegram, serves every
dashboard request and runs every routine: ``outbox.flush`` (the 300s delivery
job), ``PUT /sharing/preference`` leaving Always, and ``PUT /sharing/settings``
turning the install veto on.

PERF-235 fixed only the two ``len(_read())`` counts in ``flush``; its docstring
then claimed the whole function was clear, while ``_ensure_ids`` and
``_rewrite`` — the expensive half — stayed inline.

What is pinned here is not "it is fast", it is **which thread** touches the
file. Every queue primitive is spied, and every recorded call must have run
somewhere other than the thread driving the loop. A test that only checked the
result would pass with the work back on the loop.

Sync tests driving coroutines with ``asyncio.run``, like the rest of the
sharing route suite: ``asyncio.run`` drives the loop on this thread, so "not
the current thread" is exactly "not the event loop".
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry
from condor.sharing import consent, outbox, wire
from condor.web.models import WebUser
from condor.web.routes import sharing as routes

OWNER = WebUser(id=4242, role="user")
ADMIN = WebUser(id=99, role="admin")


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN.id


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """One conversation owned by OWNER, on an isolated install."""
    import config_manager as cm_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(consent.ENV_VAR, raising=False)
    cm_module.ConfigManager.reset_instance()
    monkeypatch.setattr(routes, "get_config_manager", _FakeConfigManager)

    from config_manager import get_config_manager

    get_config_manager()
    meta = conversations.new_conversation(OWNER.id, surface="web")
    conversations.append_turn(
        OWNER.id, meta.id, TurnEntry(role="user", text="what is the book on SOL-USDC?")
    )
    yield meta
    cm_module.ConfigManager.reset_instance()


def threads_of(monkeypatch) -> list[threading.Thread]:
    """Record the thread every touch of the queue file runs on, from now on.

    Called inside a test rather than taken as a fixture, so a test's own setup
    is not recorded: ``enqueue`` and ``trim`` build the queue on the main
    thread, where they legitimately belong.

    All four primitives, not just the parsed ones: ``count`` walks the file
    line by line and parses nothing (PERF-237), which is cheaper but is still
    a whole file read of up to ``MAX_QUEUED_SHARES`` transcripts.
    """
    seen: list[threading.Thread] = []

    for name in ("_read", "_write", "_read_probes", "_write_lines"):
        real = getattr(outbox, name)

        def spy(*args, _real=real, **kwargs):
            seen.append(threading.current_thread())
            return _real(*args, **kwargs)

        monkeypatch.setattr(outbox, name, spy)
    return seen


def _off_loop(recorded: list[threading.Thread]) -> bool:
    assert recorded, "nothing was recorded — the spies never fired"
    return all(t is not threading.current_thread() for t in recorded)


def _queue(n: int, *, user_id: int = OWNER.id, kind: str = wire.KIND_PASSIVE) -> None:
    for i in range(n):
        outbox.enqueue(
            outbox.OP_SHARE,
            f"https://collector.invalid/{i}",
            {"n": i},
            user_id=user_id,
            kind=kind,
        )


# ── The delivery job ─────────────────────────────────────────────────────


def test_a_flush_never_reads_or_rewrites_the_queue_on_the_loop(chat, monkeypatch):
    """``_ensure_ids``, the retiring ``_rewrite`` and the closing ``count``."""
    _queue(3)

    async def post(record):
        return True

    monkeypatch.setattr(outbox, "post", post)
    seen = threads_of(monkeypatch)

    assert asyncio.run(outbox.flush()) == (3, 0)
    assert _off_loop(seen), "the flush parsed the queue on the event loop"


def test_a_flush_that_drops_a_vetoed_share_rewrites_off_the_loop(chat, monkeypatch):
    """The veto path writes the file back too, and posts nothing to hide it."""
    _queue(2)
    consent.set_install_allows(False)
    seen = threads_of(monkeypatch)

    assert asyncio.run(outbox.flush()) == (0, 0)
    assert _off_loop(seen)  # before pending(): that read is on this thread
    assert outbox.pending() == []


def test_a_flush_that_stands_down_counts_off_the_loop(chat, monkeypatch):
    """Two flushes can overlap; the one standing down still answers with a
    number, and reading the file for it must not be its parting gift to the
    loop. The count is taken after ``_QUEUE_LOCK`` is released — the lock is
    never held across an await (CORR-232)."""
    _queue(2)
    monkeypatch.setattr(outbox, "_flushing", True)
    seen = threads_of(monkeypatch)

    assert asyncio.run(outbox.flush()) == (0, 2)
    assert _off_loop(seen)


def test_an_empty_queue_still_costs_one_hop_and_no_more(chat, monkeypatch):
    """The common case on an install nobody shares from: read once, off the
    loop, and return without a second look at the file."""
    seen = threads_of(monkeypatch)

    assert asyncio.run(outbox.flush()) == (0, 0)
    assert _off_loop(seen)
    assert len(seen) == 1


# ── The two veto routes ──────────────────────────────────────────────────


def test_leaving_always_purges_this_users_shares_off_the_loop(chat, monkeypatch):
    """``PUT /sharing/preference`` always → off runs ``sweep.withdraw``, which
    rewrites the queue to destroy what the sweep queued but never sent."""
    consent.set_user_state(OWNER.id, consent.ALWAYS)
    _queue(4)
    seen = threads_of(monkeypatch)

    preference = asyncio.run(
        routes.set_preference(routes.SharingPreferenceUpdate(state="off"), user=OWNER)
    )

    assert preference.state == consent.OFF
    assert _off_loop(seen), "the withdrawal parsed the queue on the event loop"
    assert outbox.pending() == []


def test_the_install_veto_purges_a_full_queue_off_the_loop(chat, monkeypatch):
    """``PUT /sharing/settings`` enabled=false runs ``consent.set_install_allows``,
    which purges every undelivered share. A full queue is the worst case the
    cap allows."""
    _queue(outbox.MAX_QUEUED_SHARES)
    assert outbox.count() == outbox.MAX_QUEUED_SHARES
    seen = threads_of(monkeypatch)

    settings = asyncio.run(
        routes.set_sharing_settings(routes.SharingUpdate(enabled=False), user=ADMIN)
    )

    assert settings.enabled is False
    assert settings.pending == 0
    assert _off_loop(seen), "the veto parsed the queue on the event loop"


# ── The loop keeps turning while they do it ──────────────────────────────


@pytest.mark.parametrize("route", ("preference", "settings"))
def test_a_slow_purge_does_not_stop_the_rest_of_the_install(chat, monkeypatch, route):
    """The overlap, proved rather than timed — the shape
    ``tests/test_sharing_routes.py`` uses for the scrub.

    The purge's read is released *by the second request*, so the first can only
    finish if the second ran while it was still in flight. Inline on the loop
    the second request could not start, the wait would expire, and both
    assertions below would fail.
    """
    consent.set_user_state(OWNER.id, consent.ALWAYS)
    _queue(4)

    started = threading.Event()
    release = threading.Event()
    order: list[str] = []
    freed: list[bool] = []
    real_read = outbox._read

    def slow_read():
        if not started.is_set():
            started.set()
            freed.append(release.wait(5))
            order.append("purge")
        return real_read()

    monkeypatch.setattr(outbox, "_read", slow_read)

    if route == "preference":
        call = routes.set_preference(
            routes.SharingPreferenceUpdate(state="off"), user=OWNER
        )
    else:
        call = routes.set_sharing_settings(
            routes.SharingUpdate(enabled=False), user=ADMIN
        )

    async def drive():
        first = asyncio.create_task(call)
        await asyncio.to_thread(started.wait, 5)  # the purge is now in flight
        # A second request, on the loop, while the first holds the queue open.
        await routes.get_preference(user=OWNER)
        order.append("second")
        release.set()
        return await first

    result = asyncio.run(drive())

    assert freed == [True], "the purge timed out instead of being released"
    assert order == ["second", "purge"]
    assert result is not None
    assert outbox.pending() == []
