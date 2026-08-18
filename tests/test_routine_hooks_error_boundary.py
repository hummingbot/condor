"""Tests for CORR-175: a mis-called hook dispatch must not look like a delivery.

``RoutineStore._fire_hooks`` deliberately tolerates a failing hook — a broken
Telegram delivery is not a reason to fail the routine run. But the broad
``except Exception`` also swallowed the ``TypeError`` that a signature mismatch
raises, so a call site left behind by a refactor (SEC-152 changed dispatch's
signature) would stop firing hooks entirely and say so in one log line.

The fix splits on *when* the error happens, not on its type: the coroutine is
built outside the try, so argument binding fails loudly, while anything raised
while awaiting stays swallowed.
"""

import asyncio
import logging

import pytest

import condor.routine_hooks as hooks
from condor.routine_store import RoutineStore

ROUTINE = "brigado/bot_report"


class FakeResult:
    text = "done"


@pytest.fixture
def store():
    s = RoutineStore()
    s._instances["i1"] = {"routine_name": ROUTINE, "user_id": 111}
    return s


def test_mis_called_dispatch_surfaces(store, monkeypatch):
    """A dispatch whose signature no longer matches the call blows up loudly."""

    async def stale_dispatch(routine_name, result, report_id, *, failed, bot):
        # Pre-SEC-152 signature: no owner_id.
        raise AssertionError("must not be reached")

    monkeypatch.setattr(hooks, "dispatch", stale_dispatch)

    with pytest.raises(TypeError):
        asyncio.run(store._fire_hooks("i1", FakeResult(), None, False))


def test_delivery_failure_is_tolerated(store, monkeypatch, caplog):
    """A hook that fails while delivering is logged, and the run survives."""

    async def failing_dispatch(*args, **kwargs):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(hooks, "dispatch", failing_dispatch)

    with caplog.at_level(logging.ERROR):
        asyncio.run(store._fire_hooks("i1", FakeResult(), None, False))

    assert "telegram is down" in caplog.text


def test_type_error_raised_while_delivering_is_still_tolerated(store, monkeypatch):
    """The boundary is when the error happens, not its type.

    A ``TypeError`` from inside the dispatch body is a runtime failure like any
    other and must not break the routine run — only a mismatched *call* does.
    """

    async def bad_delivery(*args, **kwargs):
        raise TypeError("bot.send_document got something odd")

    monkeypatch.setattr(hooks, "dispatch", bad_delivery)

    asyncio.run(store._fire_hooks("i1", FakeResult(), None, False))
