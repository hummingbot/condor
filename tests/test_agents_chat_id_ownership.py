"""Ownership gate on body-supplied ``chat_id`` push targets (SEC-198).

``/agents/notify``, ``/agents/{slug}/delegate``, ``/agents/{slug}/consult`` and
the strategy start routes all forward a ``chat_id`` from the request body to
outbound Telegram sends (directly, or via the delegation's completion notice,
the consult's confirmation prompts, or the engine's tick notifications). The
routes already refuse ``req.user_id`` impersonation; this pins the same rule for
the outbound address: a caller reaches their own private chat for free, a group
only when Telegram confirms they are a member, and anything unverifiable is
refused with 403. Admins are exempt, mirroring SEC-081/SEC-152.

Sync tests driving coroutines with ``asyncio.run`` (pytest-asyncio is not
installed in this venv), fakes in the style of test_notify_route.py.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import config_manager
from condor.agents import delegate as delegate_module
from condor.web.models import WebUser
from condor.web.routes import agents as agents_routes
from condor.web.routes.agents import (
    ConsultRequest,
    DelegateRequest,
    NotifyRequest,
    StartStrategyRequest,
    _check_chat_access,
    consult_agent,
    delegate_agent,
    notify_user,
)

CALLER = WebUser(id=1, role="user")
ADMIN = WebUser(id=99, role="admin")
FOREIGN_CHAT = 555  # another user's private chat
GROUP_CHAT = -100123  # a Telegram group id


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN.id


class _FakeBot:
    """Records sends; answers ``get_chat_member`` with a configurable shape."""

    def __init__(self, member=None):
        self.member = member
        self.calls: list[dict] = []
        self.membership_checks: list[tuple] = []

    async def send_message(self, **kw):
        self.calls.append(kw)
        return {"ok": True}

    async def get_chat_member(self, chat_id, user_id):
        self.membership_checks.append((chat_id, user_id))
        if isinstance(self.member, Exception):
            raise self.member
        return self.member


@pytest.fixture(autouse=True)
def cm(monkeypatch):
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)


@pytest.fixture
def bot(monkeypatch):
    fake = _FakeBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)
    return fake


def _resolves_to(monkeypatch, conversation_id: str = ""):
    async def fake(session_key: str) -> str:
        return conversation_id if session_key else ""

    monkeypatch.setattr(agents_routes, "_conversation_for_session", fake)


# ── The check itself ──


def test_own_private_chat_needs_no_verification(bot):
    asyncio.run(_check_chat_access(CALLER.id, CALLER.id))
    assert bot.membership_checks == []


def test_zero_chat_id_passes(bot):
    asyncio.run(_check_chat_access(CALLER.id, 0))
    assert bot.membership_checks == []


def test_a_foreign_chat_is_refused_when_unverifiable(bot):
    """The fake answers ``None`` — exactly what ``_HttpBot`` without a token
    says. An unverifiable target must fail closed."""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_check_chat_access(CALLER.id, FOREIGN_CHAT))
    assert exc.value.status_code == 403


def test_a_telegram_error_fails_closed(bot):
    bot.member = RuntimeError("Bad Request: user not found")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_check_chat_access(CALLER.id, GROUP_CHAT))
    assert exc.value.status_code == 403


def test_a_verified_group_member_passes_object_shape(bot):
    """A live python-telegram-bot answers a ChatMember with ``.status``."""
    bot.member = SimpleNamespace(status="member")
    asyncio.run(_check_chat_access(CALLER.id, GROUP_CHAT))
    assert bot.membership_checks == [(GROUP_CHAT, CALLER.id)]


def test_a_verified_group_member_passes_envelope_shape(bot):
    """``_HttpBot`` answers Telegram's raw envelope."""
    bot.member = {"ok": True, "result": {"status": "administrator"}}
    asyncio.run(_check_chat_access(CALLER.id, GROUP_CHAT))


def test_a_left_or_kicked_member_is_refused(bot):
    for status in ("left", "kicked"):
        bot.member = {"ok": True, "result": {"status": status}}
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_check_chat_access(CALLER.id, GROUP_CHAT))
        assert exc.value.status_code == 403


def test_admin_may_target_any_chat(bot):
    """Admins route announcements into shared groups/channels (SEC-152)."""
    asyncio.run(_check_chat_access(ADMIN.id, FOREIGN_CHAT))
    assert bot.membership_checks == []


# ── POST /agents/notify ──


def test_notify_refuses_a_foreign_chat_before_any_side_effect(monkeypatch, bot):
    _resolves_to(monkeypatch, "conv-1")
    noted = []
    from condor.runtime import conversations

    monkeypatch.setattr(
        conversations, "record_system", lambda *a, **kw: noted.append(a)
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            notify_user(
                NotifyRequest(text="phish", chat_id=FOREIGN_CHAT, session_key="w:1:s"),
                user=CALLER,
            )
        )

    assert exc.value.status_code == 403
    assert bot.calls == []  # nothing left the process
    assert noted == []  # the 403 lands before the transcript note


def test_notify_still_delivers_to_the_callers_own_chat(monkeypatch, bot):
    _resolves_to(monkeypatch, "")
    result = asyncio.run(
        notify_user(NotifyRequest(text="done", chat_id=CALLER.id), user=CALLER)
    )
    assert result["sent"] is True
    assert bot.calls[0]["chat_id"] == CALLER.id


def test_notify_delivers_to_a_group_the_caller_belongs_to(monkeypatch, bot):
    """The MCP crossback for a group session sends the group's id under the
    member's own JWT — that legitimate path must keep working."""
    _resolves_to(monkeypatch, "")
    bot.member = {"ok": True, "result": {"status": "member"}}

    result = asyncio.run(
        notify_user(NotifyRequest(text="done", chat_id=GROUP_CHAT), user=CALLER)
    )

    assert result["sent"] is True
    assert bot.calls[0]["chat_id"] == GROUP_CHAT


# ── POST /agents/{slug}/delegate ──


def _delegate(monkeypatch, req: DelegateRequest, user: WebUser):
    monkeypatch.setattr(agents_routes, "_get_agent", lambda slug: SimpleNamespace())
    _resolves_to(monkeypatch, "")
    started = []

    async def fake_start(**kw):
        started.append(kw)
        return SimpleNamespace(task_id="t-1", status="running")

    monkeypatch.setattr(delegate_module, "start_delegation", fake_start)
    return started, asyncio.run(delegate_agent("scout", req, user=user))


def test_delegate_refuses_a_foreign_completion_chat(monkeypatch, bot):
    with pytest.raises(HTTPException) as exc:
        _delegate(monkeypatch, DelegateRequest(task="t", chat_id=FOREIGN_CHAT), CALLER)
    assert exc.value.status_code == 403


def test_delegate_accepts_the_callers_own_chat(monkeypatch, bot):
    started, result = _delegate(
        monkeypatch, DelegateRequest(task="t", chat_id=CALLER.id), CALLER
    )
    assert result["task_id"] == "t-1"
    assert started[0]["chat_id"] == CALLER.id


# ── POST /agents/{slug}/consult ──


def test_consult_refuses_a_foreign_chat(monkeypatch, bot):
    from condor.agents import consult as consult_module

    called = []

    async def fake_run_consult(**kw):  # pragma: no cover - must not be reached
        called.append(kw)
        return "no"

    monkeypatch.setattr(consult_module, "run_consult", fake_run_consult)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            consult_agent(
                "scout", ConsultRequest(task="t", chat_id=FOREIGN_CHAT), user=CALLER
            )
        )

    assert exc.value.status_code == 403
    assert called == []


# ── POST /agents/{slug}(/strategies/{sslug})/start ──


def test_start_refuses_a_foreign_notification_chat(monkeypatch, bot):
    from condor.agents import config as config_module

    monkeypatch.setattr(config_module, "load_full_config", lambda *a, **kw: {})
    agent = SimpleNamespace(slug="scout")
    strategy = SimpleNamespace(
        slug="scalp", home=".", default_config={}, default_trading_context=""
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            agents_routes._start(
                agent,
                strategy,
                StartStrategyRequest(chat_id=FOREIGN_CHAT),
                CALLER.id,
            )
        )
    assert exc.value.status_code == 403
