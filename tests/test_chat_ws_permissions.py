"""Tests for chat WS permission approval user scoping (SEC-053).

A resolve_permission message must only resolve confirmations belonging to the
same authenticated user; foreign or unknown ids are ignored.

FEAT-010 moved the pending approvals out of a per-connection dict of futures
and into condor.runtime.confirmations, so these assert the same guarantee
against the registry.
"""

import asyncio

import pytest

from condor.runtime import confirmations as confirmations_module
from condor.runtime.confirmations import ConfirmationRegistry, ConfirmationStatus
from condor.web.routes.chat_ws import _handle_resolve_permission

USER_A = 111
USER_B = 222

TOOL_CALL = {"tool": "place_order", "input": {}}
OPTIONS = [{"optionId": "allow", "kind": "allow_once"}]


@pytest.fixture
def registry(monkeypatch):
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


def _register(registry, user_id=USER_A):
    return registry.register("web:1:slot", user_id, "buy", TOOL_CALL, OPTIONS, 30)


def test_owner_can_approve(registry):
    pending = _register(registry)
    asyncio.run(
        _handle_resolve_permission(USER_A, {"request_id": pending.id, "approved": True})
    )
    assert pending.status is ConfirmationStatus.APPROVED


def test_owner_can_deny(registry):
    pending = _register(registry)
    asyncio.run(
        _handle_resolve_permission(
            USER_A, {"request_id": pending.id, "approved": False}
        )
    )
    assert pending.status is ConfirmationStatus.DENIED


def test_foreign_user_cannot_resolve(registry):
    pending = _register(registry)

    asyncio.run(
        _handle_resolve_permission(USER_B, {"request_id": pending.id, "approved": True})
    )
    assert pending.status is ConfirmationStatus.PENDING

    # Owner can still resolve afterwards
    asyncio.run(
        _handle_resolve_permission(USER_A, {"request_id": pending.id, "approved": True})
    )
    assert pending.status is ConfirmationStatus.APPROVED


def test_unknown_request_id_ignored(registry):
    # Must not raise or create entries
    asyncio.run(
        _handle_resolve_permission(USER_A, {"request_id": "nope", "approved": True})
    )
    assert registry.get("nope") is None


def test_already_resolved_not_overwritten(registry):
    pending = _register(registry)
    asyncio.run(
        _handle_resolve_permission(
            USER_A, {"request_id": pending.id, "approved": False}
        )
    )
    asyncio.run(
        _handle_resolve_permission(USER_A, {"request_id": pending.id, "approved": True})
    )
    assert pending.status is ConfirmationStatus.DENIED
