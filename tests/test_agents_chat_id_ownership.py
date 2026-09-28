"""Ownership gate on body-supplied ``chat_id`` push targets (SEC-198).

``/agents/notify``, ``/agents/{slug}/delegate`` and the strategy start routes all
forward a ``chat_id`` from the request body to outbound Telegram sends (directly,
or via the delegation's completion notice or the engine's tick notifications). The
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
    DelegateRequest,
    NotifyRequest,
    StartStrategyRequest,
    _check_chat_access,
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


def _resolves_to(monkeypatch, conversation_id: str = "", owner: int = CALLER.id):
    """Stub the session registry: any key names a live session recorded under
    ``owner`` with ``conversation_id`` on it (no session when that is empty)."""
    from condor.runtime import client
    from condor.runtime.models import SessionInfo

    looked_up: list[str] = []

    async def fake_get_info(key):
        looked_up.append(str(key))
        if not conversation_id:
            return None
        return SessionInfo(
            key=str(key),
            agent_key="condor",
            user_id=owner,
            conversation_id=conversation_id,
        )

    monkeypatch.setattr(client, "get_info", fake_get_info)
    return looked_up


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


def _delegate(
    monkeypatch,
    req: DelegateRequest,
    user: WebUser,
    started: list | None = None,
    resolve: bool = True,
):
    monkeypatch.setattr(agents_routes, "_get_agent", lambda slug: SimpleNamespace())
    if resolve:
        _resolves_to(monkeypatch, "")
    started = [] if started is None else started

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


# ── Body-supplied ``session_key`` (SEC-636) ──
#
# The key decides whose live session a delegation's completion is resumed into
# and whose dashboard tab a note lands in, so it must name the caller's own
# session -- ``sessions._require_ownership``'s rule, admins exempt.

OTHER_USER = 777
FOREIGN_KEY = f"tg:{OTHER_USER}"


def test_delegate_refuses_another_users_session_key(monkeypatch, bot):
    _resolves_to(monkeypatch, "victim-conv", owner=OTHER_USER)
    started: list = []
    with pytest.raises(HTTPException) as exc:
        _delegate(
            monkeypatch,
            DelegateRequest(task="t", session_key=FOREIGN_KEY, on_complete="resume"),
            CALLER,
            started=started,
            resolve=False,
        )
    assert exc.value.status_code == 403
    assert started == []  # no delegation ever holds the foreign key


def test_delegate_refuses_an_ownerless_session_key(monkeypatch, bot):
    """A session with no recorded owner is nobody's to target, as in sessions.py."""
    _resolves_to(monkeypatch, "conv-x", owner=None)
    started: list = []
    with pytest.raises(HTTPException) as exc:
        _delegate(
            monkeypatch,
            DelegateRequest(task="t", session_key="web:1:s"),
            CALLER,
            started=started,
            resolve=False,
        )
    assert exc.value.status_code == 403
    assert started == []


def test_delegate_forwards_the_callers_own_session(monkeypatch, bot):
    _resolves_to(monkeypatch, "conv-1", owner=CALLER.id)
    started, result = _delegate(
        monkeypatch,
        DelegateRequest(task="t", session_key=f"tg:{CALLER.id}"),
        CALLER,
        resolve=False,
    )
    assert result["task_id"] == "t-1"
    assert started[0]["conversation_id"] == "conv-1"
    assert started[0]["session_key"] == f"tg:{CALLER.id}"


def test_admin_may_delegate_into_another_users_session(monkeypatch, bot):
    _resolves_to(monkeypatch, "victim-conv", owner=OTHER_USER)
    started, _ = _delegate(
        monkeypatch,
        DelegateRequest(task="t", session_key=FOREIGN_KEY),
        ADMIN,
        resolve=False,
    )
    assert started[0]["conversation_id"] == "victim-conv"


def test_a_dead_or_malformed_session_key_resolves_to_nothing(monkeypatch, bot):
    looked_up = _resolves_to(monkeypatch, "")  # registry: no such session
    for key in (FOREIGN_KEY, "not-a-key", ""):
        started, _ = _delegate(
            monkeypatch,
            DelegateRequest(task="t", session_key=key),
            CALLER,
            resolve=False,
        )
        assert started[0]["conversation_id"] == ""
    assert looked_up == [FOREIGN_KEY]  # malformed/empty never reach the registry


def test_notify_refuses_another_users_session_key_before_any_side_effect(
    monkeypatch, bot
):
    _resolves_to(monkeypatch, "victim-conv", owner=OTHER_USER)
    noted: list = []
    announced: list = []
    from condor import notifications
    from condor.runtime import conversations

    monkeypatch.setattr(
        conversations, "record_system", lambda *a, **kw: noted.append(a)
    )

    async def fake_announce(*a, **kw):
        announced.append(a)

    monkeypatch.setattr(notifications, "announce", fake_announce)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            notify_user(
                NotifyRequest(text="phish", chat_id=CALLER.id, session_key=FOREIGN_KEY),
                user=CALLER,
            )
        )

    assert exc.value.status_code == 403
    assert noted == []
    assert announced == []  # no bell entry
    assert bot.calls == []  # no Telegram send


def test_notify_notes_the_callers_own_session(monkeypatch, bot):
    _resolves_to(monkeypatch, "conv-1", owner=CALLER.id)
    noted: list = []
    from condor.runtime import conversations

    monkeypatch.setattr(
        conversations, "record_system", lambda *a, **kw: noted.append(a)
    )
    asyncio.run(
        notify_user(
            NotifyRequest(text="done", chat_id=CALLER.id, session_key="web:1:s"),
            user=CALLER,
        )
    )
    assert noted[0][:2] == (CALLER.id, "conv-1")


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
