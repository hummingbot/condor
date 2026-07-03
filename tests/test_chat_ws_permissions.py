"""Tests for chat WS permission approval user scoping (SEC-053).

A resolve_permission message must only resolve futures created for the
same authenticated user; foreign or unknown request_ids are ignored.
"""

import asyncio

from condor.web.routes.chat_ws import _handle_resolve_permission, _pending_permissions

USER_A = 111
USER_B = 222


def _run(coro):
    return asyncio.run(coro)


def test_owner_can_approve():
    async def scenario():
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        _pending_permissions["req-own"] = (USER_A, future)
        try:
            _handle_resolve_permission(
                USER_A, {"request_id": "req-own", "approved": True}
            )
            assert future.done()
            assert future.result() is True
        finally:
            _pending_permissions.pop("req-own", None)

    _run(scenario())


def test_owner_can_deny():
    async def scenario():
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        _pending_permissions["req-deny"] = (USER_A, future)
        try:
            _handle_resolve_permission(
                USER_A, {"request_id": "req-deny", "approved": False}
            )
            assert future.done()
            assert future.result() is False
        finally:
            _pending_permissions.pop("req-deny", None)

    _run(scenario())


def test_foreign_user_cannot_resolve():
    async def scenario():
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        _pending_permissions["req-foreign"] = (USER_A, future)
        try:
            _handle_resolve_permission(
                USER_B, {"request_id": "req-foreign", "approved": True}
            )
            assert not future.done()
            # Owner can still resolve afterwards
            _handle_resolve_permission(
                USER_A, {"request_id": "req-foreign", "approved": True}
            )
            assert future.done()
            assert future.result() is True
        finally:
            _pending_permissions.pop("req-foreign", None)

    _run(scenario())


def test_unknown_request_id_ignored():
    async def scenario():
        # Must not raise or create entries
        _handle_resolve_permission(USER_A, {"request_id": "nope", "approved": True})
        assert "nope" not in _pending_permissions

    _run(scenario())


def test_already_resolved_future_not_overwritten():
    async def scenario():
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        future.set_result(False)
        _pending_permissions["req-done"] = (USER_A, future)
        try:
            _handle_resolve_permission(
                USER_A, {"request_id": "req-done", "approved": True}
            )
            assert future.result() is False
        finally:
            _pending_permissions.pop("req-done", None)

    _run(scenario())
