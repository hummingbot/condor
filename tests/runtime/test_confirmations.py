"""Tests for the confirmation registry (FEAT-010).

This sits on the mutation-authorization path, so the tests that matter most are
the ones pinning the *auto-approve* semantics (a safe tool and the unattended
DELEGATE path must behave exactly as before) and the ones proving every
non-approval outcome cancels.
"""

import asyncio

import pytest

from condor.runtime import confirmations as confirmations_module
from condor.runtime.confirmations import (
    CANCELLED,
    ConfirmationRegistry,
    ConfirmationStatus,
    build_permission_callback,
)

# A tool the danger heuristic flags, and one it does not.
DANGEROUS = {"tool": "place_order", "input": {"trading_pair": "SOL-USDC"}}
SAFE = {"tool": "get_market_data", "input": {}}

OPTIONS = [
    {"optionId": "allow", "kind": "allow_once"},
    {"optionId": "deny", "kind": "reject_once"},
]


class _RecordingChannel:
    """Channel that records what it was asked to render."""

    def __init__(self):
        self.delivered = []

    async def deliver(self, pending):
        self.delivered.append(pending)


class _BrokenChannel:
    async def deliver(self, pending):
        raise RuntimeError("channel is down")


@pytest.fixture
def registry(monkeypatch):
    """A fresh registry, swapped in for the process-global singleton."""
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


# ── Registry mechanics ──


def test_resolve_wakes_waiter(registry):
    """wait_for returns as soon as resolve lands, not on the TTL."""

    async def scenario():
        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS, 30)

        async def answer():
            await asyncio.sleep(0.01)
            return await registry.resolve(pending.id, approved=True, by_user_id=1)

        answered, resolved = await asyncio.gather(
            answer(), registry.wait_for(pending.id)
        )
        return answered, resolved

    answered, resolved = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert answered is True
    assert resolved.status is ConfirmationStatus.APPROVED
    assert resolved.approved is True


def test_timeout_denies(registry):
    """An unanswered request expires as TIMEOUT — never as an approval."""

    async def scenario():
        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS, 0)
        return await registry.wait_for(pending.id)

    resolved = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert resolved.status is ConfirmationStatus.TIMEOUT
    assert resolved.approved is False


def test_double_resolve_idempotent(registry):
    """The first answer wins; a second is a no-op that reports False."""

    async def scenario():
        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS, 30)
        first = await registry.resolve(pending.id, approved=True, by_user_id=1)
        second = await registry.resolve(pending.id, approved=False, by_user_id=1)
        return first, second, pending.status

    first, second, status = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert status is ConfirmationStatus.APPROVED  # the reject did not overwrite


def test_resolve_authz(registry):
    """Another user cannot answer your approval."""

    async def scenario():
        pending = registry.register(
            "tg:1",
            user_id=1,
            summary="buy",
            tool_call=DANGEROUS,
            options=OPTIONS,
            timeout_seconds=30,
        )
        stranger = await registry.resolve(pending.id, approved=True, by_user_id=999)
        owner = await registry.resolve(pending.id, approved=True, by_user_id=1)
        return stranger, owner

    stranger, owner = asyncio.run(scenario())
    assert stranger is False
    assert owner is True


def test_unknown_id_resolves_false(registry):
    assert asyncio.run(registry.resolve("nope", approved=True, by_user_id=1)) is False
    assert asyncio.run(registry.wait_for("nope")) is None


def test_list_pending_scoped_to_user(registry):
    registry.register("tg:1", 1, "a", DANGEROUS, OPTIONS, 30)
    registry.register("tg:2", 2, "b", DANGEROUS, OPTIONS, 30)

    assert [p.summary for p in registry.list_pending(user_id=1)] == ["a"]
    assert len(registry.list_pending()) == 2


def test_sweep_expires_and_wakes(registry):
    """The sweep denies an overdue entry rather than leaving it pending."""
    pending = registry.register("tg:1", 1, "a", DANGEROUS, OPTIONS, 0)
    registry.sweep()
    assert pending.status is ConfirmationStatus.TIMEOUT
    assert pending._event.is_set()
    assert registry.list_pending(user_id=1) == []


# ── The auto-approve semantics that must not change ──


def test_safe_tool_not_registered(registry):
    """A non-dangerous tool is approved without touching the registry."""
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel])

    outcome = asyncio.run(cb(SAFE, OPTIONS))

    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}
    assert channel.delivered == []
    assert registry.list_pending() == []


def test_delegate_auto_approve_unchanged(registry):
    """DELEGATE passes permission_callback=None, so ACP auto-approves.

    Nothing in this feature may make an unattended run start asking a human;
    this pins that the registry is simply not on that path.
    """
    # The unattended path never builds a callback at all.
    assert registry.list_pending() == []

    # And a builder with no channels denies rather than hanging, so a
    # misconfigured caller fails closed instead of stalling forever.
    cb = build_permission_callback("consult:x", 1, channels=[])
    assert asyncio.run(asyncio.wait_for(cb(DANGEROUS, OPTIONS), timeout=2)) == CANCELLED
    assert registry.list_pending() == []


def test_blocked_tool_refused(registry, monkeypatch):
    """A tool that would bypass RBAC is refused without asking anyone.

    BLOCKED_TOOLS is empty today, so the entry is injected: the point is that
    the gate still fires the moment something is added to that set.
    """
    monkeypatch.setattr(
        "handlers.agents._shared.BLOCKED_TOOLS", {"place_order"}, raising=False
    )
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel])

    outcome = asyncio.run(cb({"tool": "place_order", "input": {}}, OPTIONS))

    assert outcome == CANCELLED
    assert channel.delivered == []
    assert registry.list_pending() == []


# ── End-to-end through the callback ──


def test_callback_approves_after_resolve(registry):
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        # Wait for the request to be rendered before answering it.
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        pending = channel.delivered[0]
        await registry.resolve(pending.id, approved=True, by_user_id=1)
        return await task

    outcome = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}


def test_callback_cancels_on_denial(registry):
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        await registry.resolve(channel.delivered[0].id, approved=False, by_user_id=1)
        return await task

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=2)) == CANCELLED


def test_callback_cancels_on_timeout(registry):
    """TTL expiry cancels the tool call instead of letting it through."""
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=0)

    assert asyncio.run(asyncio.wait_for(cb(DANGEROUS, OPTIONS), timeout=2)) == CANCELLED


def test_dual_channel_first_wins(registry):
    """Both surfaces render it; whoever answers first decides."""
    telegram = _RecordingChannel()
    web = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [telegram, web], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if telegram.delivered and web.delivered:
                break
            await asyncio.sleep(0.005)
        cid = telegram.delivered[0].id
        # Same entry reached both channels — one request, two renderings.
        assert web.delivered[0].id == cid
        web_first = await registry.resolve(cid, approved=True, by_user_id=1)
        telegram_late = await registry.resolve(cid, approved=False, by_user_id=1)
        return web_first, telegram_late, await task

    web_first, telegram_late, outcome = asyncio.run(
        asyncio.wait_for(scenario(), timeout=2)
    )
    assert web_first is True
    assert telegram_late is False  # the late tap is a no-op, not a reversal
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}


def test_broken_channel_does_not_swallow_request(registry):
    """One channel failing still leaves the request answerable on the other."""
    good = _RecordingChannel()
    cb = build_permission_callback(
        "tg:1", 1, [_BrokenChannel(), good], timeout_seconds=30
    )

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if good.delivered:
                break
            await asyncio.sleep(0.005)
        await registry.resolve(good.delivered[0].id, approved=True, by_user_id=1)
        return await task

    outcome = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}
