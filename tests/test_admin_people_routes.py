"""The admin people panel's server-side contract (FEAT-088).

This module is not hot-reloaded — ``main.py``'s reload list does not cover
``condor/web/routes/*.py`` — so clicking through the dashboard is a poor way to
learn whether a rule holds. These tests are where the rules are checked.

Three things are worth pinning:

* **The refusal table.** ``POST /people/{id}/role`` takes a destination state and
  picks the transition, so every illegal move is refused in one place. A verb
  that quietly returns False would render in the UI as a successful change that
  did not happen, so each arm has to surface as a 409.
* **The orphan grant.** ``local`` is shared with an id that has no ``users``
  record at all. That access is live, and before this feature nothing in the
  product could display or revoke it.
* **The gate.** Hiding the tab is cosmetic; every route re-reads the role.
"""

import asyncio

import pytest
from fastapi import HTTPException

import config_manager as cm_module
from condor.web import auth as web_auth
from condor.web.models import WebUser
from condor.web.routes import admin as admin_routes
from condor.web.routes.admin import (
    CodeRunGrantRequest,
    RoleRequest,
    ServerGrantRequest,
    list_audit,
    list_people,
    list_users,
    set_person_code_run,
    set_person_role,
    set_person_server_access,
)

ADMIN = WebUser(id=1, username="root", role="admin")
SEAT = WebUser(id=2, username="quant", role="user")
PENDING = WebUser(id=3, username="newcomer", role="user")
BLOCKED = WebUser(id=4, username="banned", role="user")
# Holds a grant on `local` and has no users record — the live config's 6483117755.
ORPHAN_ID = 5


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """A real ConfigManager on a throwaway config.yml — persistence included."""
    monkeypatch.chdir(tmp_path)  # audit_log.yml is written relative to cwd
    manager = cm_module.ConfigManager(config_path=str(tmp_path / "config.yml"))
    manager._data["users"] = {
        ADMIN.id: {
            "user_id": ADMIN.id,
            "role": "admin",
            "first_name": "Root",
        },
        SEAT.id: {
            "user_id": SEAT.id,
            "username": "quant",
            "role": "user",
            "first_name": "Federico",
            "last_name": "Cardoso",
        },
        PENDING.id: {
            "user_id": PENDING.id,
            "username": "newcomer",
            "role": "pending",
        },
        BLOCKED.id: {"user_id": BLOCKED.id, "username": "banned", "role": "blocked"},
    }
    # `list_accessible_servers` intersects the access record with the server
    # config, so both halves have to be here for "does it show up in /servers"
    # to mean anything.
    manager._data["servers"] = {
        "brigado_2": {"host": "10.0.0.2", "port": 8000},
        "local": {"host": "localhost", "port": 8000},
        "owned_by_seat": {"host": "10.0.0.3", "port": 8000},
    }
    manager._data["server_access"] = {
        "brigado_2": {"owner_id": ADMIN.id, "shared_with": {}},
        "local": {"owner_id": ADMIN.id, "shared_with": {ORPHAN_ID: "trader"}},
        "owned_by_seat": {"owner_id": SEAT.id, "shared_with": {}},
    }
    manager._data["user_preferences"] = {}
    manager._audit_log = []
    manager._save_config()
    for module in (cm_module, web_auth, admin_routes):
        monkeypatch.setattr(module, "get_config_manager", lambda: manager)
    return manager


def people(actor=ADMIN):
    return {p.user_id: p for p in asyncio.run(list_people(user=actor))}


def set_role(target_id, role, actor=ADMIN):
    return asyncio.run(set_person_role(target_id, RoleRequest(role=role), user=actor))


def set_access(target_id, server, permission, actor=ADMIN):
    return asyncio.run(
        set_person_server_access(
            target_id, server, ServerGrantRequest(permission=permission), user=actor
        )
    )


def expect_refusal(fn, status):
    with pytest.raises(HTTPException) as excinfo:
        fn()
    assert excinfo.value.status_code == status
    assert excinfo.value.detail, "a refusal has to say why"
    return excinfo.value


# ── the gate: every route, not just the mutating ones ──


@pytest.mark.parametrize(
    "call",
    [
        lambda: asyncio.run(list_people(user=SEAT)),
        lambda: asyncio.run(list_users(user=SEAT)),
        lambda: asyncio.run(list_audit(user=SEAT)),
        lambda: set_role(PENDING.id, "user", actor=SEAT),
        lambda: set_access(SEAT.id, "brigado_2", "trader", actor=SEAT),
        lambda: asyncio.run(
            set_person_code_run(SEAT.id, CodeRunGrantRequest(granted=True), user=SEAT)
        ),
    ],
    ids=["people", "users", "audit", "role", "access", "code-run"],
)
def test_every_route_refuses_a_non_admin_seat(cm, call):
    expect_refusal(call, 403)


def test_the_role_is_reread_not_trusted_from_the_token(cm):
    """A JWT minted while the caller was an admin must not outlive the role."""
    stale = WebUser(id=SEAT.id, username="quant", role="admin")
    expect_refusal(lambda: set_role(PENDING.id, "user", actor=stale), 403)


# ── who the rows are ──


def test_a_person_is_named_by_their_full_name(cm):
    assert people()[SEAT.id].display_name == "Federico Cardoso"


def test_a_person_with_only_a_handle_is_named_by_it(cm):
    assert people()[PENDING.id].display_name == "newcomer"


def test_the_id_is_always_reported_alongside_the_name(cm):
    """The panel prints it as secondary text; it is the only stable identifier."""
    assert all(p.user_id for p in people().values())


def test_the_pending_request_is_visible_as_a_role(cm):
    assert people()[PENDING.id].role == "pending"


def test_admins_sort_first_and_pending_next(cm):
    rows = asyncio.run(list_people(user=ADMIN))
    assert [r.role for r in rows][:2] == ["admin", "pending"]


# ── the orphan grant ──


def test_an_id_that_only_holds_a_grant_is_still_a_row(cm):
    """Otherwise the access is a hole in config.yml that nothing can show."""
    row = people()[ORPHAN_ID]

    assert row.known is False
    assert row.display_name == f"User {ORPHAN_ID}"
    assert [g.permission for g in row.servers if g.server == "local"] == ["trader"]


def test_the_orphan_grant_can_be_revoked(cm):
    set_access(ORPHAN_ID, "local", "")

    assert cm.get_server_shared_users("local") == []
    assert ORPHAN_ID not in people(), "with the grant gone there is nothing to show"


def test_an_orphan_cannot_be_granted_more_access(cm):
    """`share_server` refuses a target who is not approved, and a record that
    does not exist cannot be approved. Revoke is the only move."""
    expect_refusal(lambda: set_access(ORPHAN_ID, "brigado_2", "trader"), 409)


def test_the_orphan_is_absent_from_the_legacy_users_alias(cm):
    """The old shape had no way to say "this is not a user record"."""
    assert ORPHAN_ID not in {r.user_id for r in asyncio.run(list_users(user=ADMIN))}


# ── the refusal table ──


def test_approving_a_pending_user_makes_them_a_user(cm):
    assert set_role(PENDING.id, "user").role == "user"
    assert cm.is_approved(PENDING.id) is True


def test_approving_someone_already_approved_is_a_no_op(cm):
    assert set_role(SEAT.id, "user").role == "user"


def test_approving_a_blocked_user_is_refused_with_the_way_out(cm):
    refusal = expect_refusal(lambda: set_role(BLOCKED.id, "user"), 409)

    assert "unblock" in refusal.detail.lower()
    assert cm.get_user_role(BLOCKED.id).value == "blocked"


def test_unblocking_returns_them_to_pending(cm):
    assert set_role(BLOCKED.id, "pending").role == "pending"


def test_only_a_blocked_user_can_be_moved_back_to_pending(cm):
    expect_refusal(lambda: set_role(SEAT.id, "pending"), 409)


def test_rejecting_a_pending_request_deletes_the_record(cm):
    set_role(PENDING.id, "rejected")

    assert cm.get_user(PENDING.id) is None
    assert PENDING.id not in people()


def test_an_approved_user_cannot_be_rejected_only_blocked(cm):
    refusal = expect_refusal(lambda: set_role(SEAT.id, "rejected"), 409)

    assert "block" in refusal.detail.lower()
    assert cm.get_user(SEAT.id) is not None


def test_blocking_an_approved_user_works(cm):
    assert set_role(SEAT.id, "blocked").role == "blocked"


def test_blocking_a_pending_user_works(cm):
    assert set_role(PENDING.id, "blocked").role == "blocked"


def test_an_admin_cannot_be_blocked(cm):
    expect_refusal(lambda: set_role(ADMIN.id, "blocked"), 409)
    assert cm.is_admin(ADMIN.id) is True


def test_an_admin_cannot_be_demoted_from_here(cm):
    """The role comes from ADMIN_USER_ID; a web toggle would be undone on the
    next boot by `_ensure_admin_user`."""
    expect_refusal(lambda: set_role(ADMIN.id, "pending"), 409)


def test_an_admin_cannot_block_themselves(cm):
    """`block_user` refuses `user_id == admin_id` even before the admin check."""
    expect_refusal(lambda: set_role(ADMIN.id, "blocked", actor=ADMIN), 409)


def test_an_unknown_role_is_a_400_not_a_silent_no_op(cm):
    expect_refusal(lambda: set_role(SEAT.id, "superuser"), 400)


def test_a_role_change_for_an_unknown_user_is_a_404(cm):
    expect_refusal(lambda: set_role(9999, "user"), 404)


# ── server access ──


def test_granting_trader_puts_the_server_in_that_users_reach(cm):
    person = set_access(SEAT.id, "brigado_2", "trader")

    assert cm.has_server_access(SEAT.id, "brigado_2") is True
    assert "brigado_2" in cm.list_accessible_servers(SEAT.id)
    assert [g.permission for g in person.servers if g.server == "brigado_2"] == [
        "trader"
    ]


def test_revoking_takes_it_away_again(cm):
    set_access(SEAT.id, "brigado_2", "trader")
    person = set_access(SEAT.id, "brigado_2", "")

    assert cm.has_server_access(SEAT.id, "brigado_2") is False
    assert "brigado_2" not in cm.list_accessible_servers(SEAT.id)
    assert [g.permission for g in person.servers if g.server == "brigado_2"] == [""]


def test_revoking_access_nobody_had_is_not_an_error(cm):
    """The caller asked for a state, and the state is already that."""
    assert set_access(SEAT.id, "brigado_2", "").user_id == SEAT.id


def test_the_grant_lands_in_config_yml_so_no_restart_is_needed(cm):
    set_access(SEAT.id, "brigado_2", "trader")

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.has_server_access(SEAT.id, "brigado_2") is True


def test_a_pending_user_cannot_be_granted_a_server(cm):
    """`share_server` refuses a non-approved target, so the panel disables the
    grid rather than offering a control that silently no-ops."""
    refusal = expect_refusal(lambda: set_access(PENDING.id, "brigado_2", "trader"), 409)

    assert "approve" in refusal.detail.lower()


def test_approving_then_granting_works_in_one_sitting(cm):
    """The acceptance criterion behind putting the two controls in one panel."""
    set_role(PENDING.id, "user")

    assert set_access(PENDING.id, "brigado_2", "trader").user_id == PENDING.id
    assert cm.has_server_access(PENDING.id, "brigado_2") is True


def test_an_admins_access_is_reported_as_inherited_not_as_a_grant(cm):
    grants = {g.server: g for g in people()[ADMIN.id].servers}

    assert all(g.implicit for g in grants.values())
    assert grants["brigado_2"].permission == "owner"


def test_an_admins_access_cannot_be_edited(cm):
    """Revoking would appear to do nothing: `get_server_permission` short-circuits."""
    expect_refusal(lambda: set_access(ADMIN.id, "brigado_2", ""), 409)


def test_a_servers_owner_is_reported_as_owner(cm):
    grants = {g.server: g for g in people()[SEAT.id].servers}

    assert grants["owned_by_seat"].permission == "owner"
    assert grants["owned_by_seat"].implicit is False


def test_the_owners_own_access_is_not_editable(cm):
    expect_refusal(lambda: set_access(SEAT.id, "owned_by_seat", ""), 409)


def test_ownership_transfer_is_refused_rather_than_faked(cm):
    """`share_server` writes `shared_with`, never `owner_id`; accepting this
    would put a second writer on the owner field."""
    expect_refusal(lambda: set_access(SEAT.id, "brigado_2", "owner"), 409)


def test_an_unknown_server_is_a_404(cm):
    expect_refusal(lambda: set_access(SEAT.id, "nope", "trader"), 404)


def test_an_unknown_permission_is_a_400(cm):
    expect_refusal(lambda: set_access(SEAT.id, "brigado_2", "superuser"), 400)


# ── capabilities ──


def test_the_code_run_grant_is_reported_on_the_person(cm):
    asyncio.run(
        set_person_code_run(SEAT.id, CodeRunGrantRequest(granted=True), user=ADMIN)
    )

    assert people()[SEAT.id].code_run is True
    assert people()[ADMIN.id].code_run is False, "admins pass on their role"


def test_granting_code_run_to_an_admin_is_still_refused(cm):
    expect_refusal(
        lambda: asyncio.run(
            set_person_code_run(ADMIN.id, CodeRunGrantRequest(granted=True), user=ADMIN)
        ),
        409,
    )


# ── audit ──


def test_both_parties_are_named_in_the_audit_log(cm):
    set_role(PENDING.id, "user")

    entry = next(
        e for e in asyncio.run(list_audit(user=ADMIN)) if e.action == "user_approved"
    )
    assert entry.actor_name == "Root"
    assert entry.target_name == "newcomer"


def test_granting_and_revoking_a_server_both_show_up(cm):
    set_access(SEAT.id, "brigado_2", "trader")
    set_access(SEAT.id, "brigado_2", "")

    actions = [e.action for e in asyncio.run(list_audit(user=ADMIN))]
    assert "server_shared" in actions
    assert "server_access_revoked" in actions


def test_a_server_target_is_not_mistaken_for_a_person(cm):
    set_access(SEAT.id, "brigado_2", "trader")

    entry = next(
        e for e in asyncio.run(list_audit(user=ADMIN)) if e.action == "server_shared"
    )
    assert entry.target_id == "brigado_2"
    assert entry.target_name == ""
    assert entry.details["target_user"] == SEAT.id


def test_the_audit_limit_is_bounded(cm):
    for _ in range(5):
        set_access(SEAT.id, "brigado_2", "trader")
        set_access(SEAT.id, "brigado_2", "")

    assert len(asyncio.run(list_audit(limit=3, user=ADMIN))) == 3
    assert len(asyncio.run(list_audit(limit=0, user=ADMIN))) == 1


# ── the legacy alias ──


def test_the_old_users_route_still_answers_for_a_stale_bundle(cm):
    """`Settings.tsx` probes it to decide whether the Admin *and* Updates tabs
    exist at all; a browser holding the previous bundle must not lose them."""
    rows = {r.user_id: r for r in asyncio.run(list_users(user=ADMIN))}

    assert rows[SEAT.id].username == "quant"
    assert rows[ADMIN.id].is_admin is True


# ── the identity backfill ──


class FakeBot:
    """A `getChat` that answers like a live python-telegram-bot Chat object."""

    def __init__(self, chats: dict, delay: float = 0.0):
        self._chats = chats
        self._delay = delay
        self.calls: list[int] = []
        self.concurrent = 0
        self.max_concurrent = 0

    async def get_chat(self, chat_id: int):
        self.calls.append(chat_id)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if chat_id not in self._chats:
                raise RuntimeError("chat not found")  # they blocked the bot
            return type("Chat", (), self._chats[chat_id])()
        finally:
            self.concurrent -= 1


def refresh(bot, monkeypatch):
    import condor.agents.delegate as delegate

    monkeypatch.setattr(delegate, "resolve_bot", lambda *a, **kw: bot)
    return asyncio.run(admin_routes.refresh_names(user=ADMIN))


def test_the_backfill_names_a_record_that_has_none(cm, monkeypatch):
    """The admin's own record is minted from ADMIN_USER_ID with nothing but an
    id, and nothing ever re-captures it — this is the only path to a name."""
    cm._data["users"][ADMIN.id].pop("first_name")
    bot = FakeBot(
        {
            ADMIN.id: {
                "first_name": "Federico",
                "last_name": "Cardoso",
                "username": "cardosofede",
            }
        }
    )

    out = refresh(bot, monkeypatch)

    assert out == {"checked": 1, "resolved": 1, "failed": 0}
    assert people()[ADMIN.id].display_name == "Federico Cardoso"


def test_the_backfill_skips_records_that_already_have_a_name(cm, monkeypatch):
    """A Telegram round-trip per person per click, for data that changes yearly."""
    bot = FakeBot({})

    refresh(bot, monkeypatch)

    assert bot.calls == [], "everyone in the fixture already has a name or a handle"


def test_one_unreachable_chat_does_not_cost_the_others_their_names(cm, monkeypatch):
    """Someone who blocked the bot is a per-record failure, never a 500."""
    cm._data["users"][ADMIN.id].pop("first_name")
    cm._data["users"][BLOCKED.id].pop("username")
    bot = FakeBot({ADMIN.id: {"first_name": "Federico"}})  # BLOCKED raises

    out = refresh(bot, monkeypatch)

    assert out["resolved"] == 1
    assert out["failed"] == 1
    assert people()[ADMIN.id].display_name == "Federico"


def test_an_answer_with_no_names_in_it_is_not_counted_as_resolved(cm, monkeypatch):
    cm._data["users"][ADMIN.id].pop("first_name")
    bot = FakeBot({ADMIN.id: {}})

    assert refresh(bot, monkeypatch)["resolved"] == 0


def test_the_http_fallback_envelope_is_understood_too(cm, monkeypatch):
    """`_HttpBot` returns the raw `{"ok":..., "result":{...}}` envelope; a live
    bot returns a Chat object. Both are legitimate rungs of `resolve_bot`."""

    class HttpishBot:
        async def get_chat(self, chat_id: int):
            return {"ok": True, "result": {"first_name": "Federico"}}

    cm._data["users"][ADMIN.id].pop("first_name")

    assert refresh(HttpishBot(), monkeypatch)["resolved"] == 1
    assert people()[ADMIN.id].display_name == "Federico"


def test_the_backfill_is_capped_so_it_cannot_stall_the_bots_event_loop(cm, monkeypatch):
    """It runs on the bot's own loop; twenty sequential round-trips would freeze
    polling for the duration, and twenty concurrent ones would flood Telegram."""
    for uid in range(100, 120):
        cm._data["users"][uid] = {"user_id": uid, "role": "user"}
    bot = FakeBot(
        {uid: {"first_name": f"P{uid}"} for uid in range(100, 120)}, delay=0.01
    )

    out = refresh(bot, monkeypatch)

    assert out["resolved"] == 20
    assert bot.max_concurrent <= admin_routes._REFRESH_CONCURRENCY
    assert bot.max_concurrent > 1, "sequential would stall the loop"


def test_a_non_admin_cannot_trigger_the_backfill(cm, monkeypatch):
    import condor.agents.delegate as delegate

    monkeypatch.setattr(delegate, "resolve_bot", lambda *a, **kw: FakeBot({}))
    expect_refusal(lambda: asyncio.run(admin_routes.refresh_names(user=SEAT)), 403)
