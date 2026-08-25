"""The HTTP surface of conversation sharing (FEAT-054).

The rule this file exists to pin: **sharing is the owner's act alone.** The
conversations router lets an admin read someone else's transcript by naming
them in ``?user_id=`` — right for support, wrong for consent — so the sharing
router has no such parameter and acts on the caller's own id and nothing else.

The other assertions are the two vetoes, each independently, and the delete
path: unsharing has to keep working after an admin turns sharing off, or a user
is stranded with something they can no longer take back.

Handlers are driven directly with ``asyncio.run`` (pytest-asyncio is available
but the rest of the route suite does it this way), with a fake ConfigManager
for the admin check.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry
from condor.sharing import consent, outbox
from condor.web.models import WebUser
from condor.web.routes import sharing as routes

OWNER = WebUser(id=4242, role="user")
ADMIN = WebUser(id=99, role="admin")
STRANGER = WebUser(id=7, role="user")


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


def run(coro):
    return asyncio.run(coro)


# ── Ownership ────────────────────────────────────────────────────────────


def test_the_owner_can_preview_and_share(chat):
    preview = run(routes.preview_share(chat.id, user=OWNER))
    assert preview["conversation_id"] == chat.id
    assert preview["turns"]

    receipt = run(routes.submit_share(chat.id, user=OWNER))
    assert receipt["revision"] == 1
    assert len(outbox.pending()) == 1


def test_an_admin_cannot_share_a_conversation_they_do_not_own(chat):
    """Acceptance criterion. An admin may *read* this transcript through
    /conversations; consent to publish it is not theirs to give, and there is
    no parameter here through which they could try."""
    with pytest.raises(HTTPException) as raised:
        run(routes.preview_share(chat.id, user=ADMIN))
    assert raised.value.status_code == 404

    with pytest.raises(HTTPException) as raised:
        run(routes.submit_share(chat.id, user=ADMIN))
    assert raised.value.status_code == 404
    assert outbox.pending() == []


def test_a_stranger_reaches_nothing(chat):
    with pytest.raises(HTTPException) as raised:
        run(routes.preview_share(chat.id, user=STRANGER))
    assert raised.value.status_code == 404
    assert outbox.pending() == []


def test_an_unknown_conversation_is_a_404(chat):
    with pytest.raises(HTTPException) as raised:
        run(routes.preview_share("nosuchid", user=OWNER))
    assert raised.value.status_code == 404


def test_a_malformed_id_is_a_400(chat):
    with pytest.raises(HTTPException) as raised:
        run(routes.preview_share("../../etc/passwd", user=OWNER))
    assert raised.value.status_code == 400


# ── The vetoes ───────────────────────────────────────────────────────────


def test_the_admin_veto_suppresses_the_endpoint_for_the_owner(chat):
    consent.set_install_allows(False)
    for call in (routes.preview_share, routes.submit_share):
        with pytest.raises(HTTPException) as raised:
            run(call(chat.id, user=OWNER))
        assert raised.value.status_code == 403
    assert outbox.pending() == []


def test_the_env_kill_switch_suppresses_the_endpoint(chat, monkeypatch):
    monkeypatch.setenv(consent.ENV_VAR, "off")
    with pytest.raises(HTTPException) as raised:
        run(routes.submit_share(chat.id, user=OWNER))
    assert raised.value.status_code == 403
    assert outbox.pending() == []


def test_only_the_admin_may_flip_the_install_switch(chat):
    with pytest.raises(HTTPException) as raised:
        run(
            routes.set_sharing_settings(routes.SharingUpdate(enabled=False), user=OWNER)
        )
    assert raised.value.status_code == 403
    assert consent.install_allows()

    result = run(
        routes.set_sharing_settings(routes.SharingUpdate(enabled=False), user=ADMIN)
    )
    assert result.enabled is False


def test_the_environment_refuses_a_ui_change_rather_than_lying_about_it(
    chat, monkeypatch
):
    monkeypatch.setenv(consent.ENV_VAR, "off")
    with pytest.raises(HTTPException) as raised:
        run(routes.set_sharing_settings(routes.SharingUpdate(enabled=True), user=ADMIN))
    assert raised.value.status_code == 409


def test_settings_are_readable_by_every_seat(chat):
    theirs = run(routes.get_sharing_settings(user=OWNER))
    admins = run(routes.get_sharing_settings(user=ADMIN))
    assert theirs.enabled and admins.enabled
    assert theirs.can_change is False and admins.can_change is True


# ── Unsharing ────────────────────────────────────────────────────────────


def test_unsharing_survives_the_admin_turning_sharing_off(chat):
    """An admin closing the door must not strand a user with something they can
    no longer take back."""
    run(routes.submit_share(chat.id, user=OWNER))
    consent.set_install_allows(False)

    result = run(routes.unshare_conversation(chat.id, user=OWNER))

    assert result["unshared"] is True
    assert outbox.pending()[-1]["op"] == outbox.OP_UNSHARE


def test_unsharing_something_that_was_never_shared_is_a_404(chat):
    with pytest.raises(HTTPException) as raised:
        run(routes.unshare_conversation(chat.id, user=OWNER))
    assert raised.value.status_code == 404


def test_an_admin_cannot_unshare_someone_elses_conversation(chat):
    run(routes.submit_share(chat.id, user=OWNER))
    with pytest.raises(HTTPException) as raised:
        run(routes.unshare_conversation(chat.id, user=ADMIN))
    assert raised.value.status_code == 404


def test_listing_shows_only_the_callers_own_shares(chat):
    run(routes.submit_share(chat.id, user=OWNER))
    assert [s["conversation_id"] for s in run(routes.list_shared(user=OWNER))] == [
        chat.id
    ]
    assert run(routes.list_shared(user=ADMIN)) == []


# ── Deleting a conversation revokes its share ────────────────────────────


def test_deleting_a_shared_conversation_queues_the_unshare_first(chat, monkeypatch):
    """Otherwise "delete" would mean "delete here, keep there" — and the local
    delete destroys the only token that could ever remove the remote copy."""
    from condor.web.routes import conversations as conversation_routes

    run(routes.submit_share(chat.id, user=OWNER))

    async def _no_sessions(*args, **kwargs):
        return []

    monkeypatch.setattr(conversation_routes.runtime, "list_sessions", _no_sessions)
    result = run(conversation_routes.delete_conversation(chat.id, user=OWNER))

    assert result["deleted"] is True
    assert result["unshared"] is True
    assert outbox.pending()[-1]["op"] == outbox.OP_UNSHARE
    assert conversations.get_conversation(OWNER.id, chat.id) is None
