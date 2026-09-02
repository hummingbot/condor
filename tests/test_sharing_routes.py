"""The HTTP surface of conversation sharing (FEAT-054, FEAT-055).

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
import threading
import time

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
    """On every verb, not just preview. The three of them do their work in a
    worker thread now (PERF-235), and an exception that does not come back
    across that boundary would surface as a 500 instead."""
    for verb in (
        routes.preview_share,
        routes.submit_share,
        routes.unshare_conversation,
    ):
        with pytest.raises(HTTPException) as raised:
            run(verb("nosuchid", user=OWNER))
        assert raised.value.status_code == 404, verb.__name__


def test_a_malformed_id_is_a_400(chat):
    for verb in (
        routes.preview_share,
        routes.submit_share,
        routes.unshare_conversation,
    ):
        with pytest.raises(HTTPException) as raised:
            run(verb("../../etc/passwd", user=OWNER))
        assert raised.value.status_code == 400, verb.__name__


# ── The shared event loop ────────────────────────────────────────────────


def test_a_slow_scrub_does_not_stop_the_rest_of_the_install(chat, monkeypatch):
    """Acceptance criterion (PERF-235): a request that scrubs a transcript must
    not hold the event loop while it does it.

    ``main.py`` runs uvicorn as a task beside PTB's polling and job queue, so a
    scrub on the loop is not slow for its caller — it is a stopped bot for
    everyone. The gate here proves the overlap rather than timing it: the slow
    scrub is released *by the second request*, so the first can only finish if
    the second ran while it was still in flight. Inline on the loop the second
    request could not start, the wait would expire instead, and both assertions
    below would fail.
    """
    from condor.sharing import scrub as scrub_module

    started = threading.Event()
    release = threading.Event()
    order: list[str] = []
    freed: list[bool] = []
    real_scrubber = scrub_module.scrubber

    def slow_scrubber(*args, **kwargs):
        started.set()
        freed.append(release.wait(5))
        order.append("first")
        return real_scrubber(*args, **kwargs)

    # The build's redaction seam: since PERF-284 it is the per-share
    # ``Scrubber`` that ``wire.bound`` calls turn by turn, not a whole-transcript
    # ``scrub``. What is being gated is unchanged — the slow part of a build must
    # not run on the loop.
    monkeypatch.setattr(scrub_module, "scrubber", slow_scrubber)

    async def drive():
        first = asyncio.create_task(routes.preview_share(chat.id, user=OWNER))
        await asyncio.to_thread(started.wait, 5)  # the scrub is now in flight
        settings = await routes.get_sharing_settings(user=OWNER)
        order.append("second")
        release.set()
        return settings, await first

    settings, preview = run(drive())

    assert freed == [True], "the scrub timed out instead of being released"
    assert order == ["second", "first"]
    assert settings.pending == 0
    assert preview["turns"]


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


def test_settings_count_the_queue_without_reading_it(chat, monkeypatch):
    """Acceptance criterion (PERF-237): ``pending`` is the number
    ``len(outbox.pending())`` would give, arrived at without parsing a single
    queued transcript — this runs on the event loop, and a queued record is a
    whole conversation."""
    run(routes.submit_share(chat.id, user=OWNER))
    expected = len(outbox.pending())

    parsed: list[str] = []
    real = outbox.json.loads
    monkeypatch.setattr(
        outbox.json, "loads", lambda text, *a, **k: (parsed.append(text), real(text))[1]
    )
    settings = run(routes.get_sharing_settings(user=OWNER))

    assert settings.pending == expected == 1
    assert parsed == []


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


# ── The standing answer, and its per-conversation escape hatch (FEAT-055) ─


def test_the_preference_starts_off_and_is_the_callers_own(chat):
    """A route that reads a consent must never be able to read somebody else's:
    there is no id to name one, and a stranger sees their own default."""
    assert run(routes.get_preference(user=OWNER)).state == consent.OFF
    assert run(routes.get_preference(user=STRANGER)).state == consent.OFF


def test_the_preference_never_reads_the_conversation_store(chat, monkeypatch):
    """The answer is three consent lookups and a timestamp. It used to also
    walk the whole store to count shares, for a number no caller read
    (PERF-239); a store read that explodes must not be able to reach this
    route — nor the PUT, which answers by calling it."""

    def boom(*args, **kwargs):
        raise AssertionError("the preference route read the conversation store")

    monkeypatch.setattr(conversations, "list_conversations", boom)

    answer = run(routes.get_preference(user=OWNER))
    assert answer.state == consent.OFF
    assert answer.allowed is True
    assert answer.sweeping is False

    stored = run(
        routes.set_preference(
            routes.SharingPreferenceUpdate(state="always"), user=OWNER
        )
    )
    assert stored.state == consent.ALWAYS
    assert stored.sweeping


def test_choosing_always_records_the_moment_and_starts_sweeping(chat):
    before = time.time()
    answer = run(
        routes.set_preference(
            routes.SharingPreferenceUpdate(state="always"), user=OWNER
        )
    )
    assert answer.state == consent.ALWAYS
    assert answer.sweeping
    assert before <= answer.opted_in_at <= time.time()
    # And only for them.
    assert not run(routes.get_preference(user=STRANGER)).sweeping


def test_always_is_refused_while_the_install_veto_is_on(chat):
    """The only answer that sends anything on its own is the only one that has
    to clear the install's gates before it can be stored at all."""
    consent.set_install_allows(False)
    with pytest.raises(HTTPException) as raised:
        run(
            routes.set_preference(
                routes.SharingPreferenceUpdate(state="always"), user=OWNER
            )
        )
    assert raised.value.status_code == 403
    assert consent.user_state(OWNER.id) == consent.OFF


def test_turning_it_off_is_never_refused(chat):
    """A user must not be lockable into a standing yes by an admin veto that
    landed after they said it."""
    run(
        routes.set_preference(
            routes.SharingPreferenceUpdate(state="always"), user=OWNER
        )
    )
    consent.set_install_allows(False)

    answer = run(
        routes.set_preference(routes.SharingPreferenceUpdate(state="off"), user=OWNER)
    )
    assert answer.state == consent.OFF


def test_an_unknown_preference_is_a_400_not_a_silent_default(chat):
    with pytest.raises(HTTPException) as raised:
        run(
            routes.set_preference(
                routes.SharingPreferenceUpdate(state="sure why not"), user=OWNER
            )
        )
    assert raised.value.status_code == 400


def _after_opt_in() -> str:
    """A conversation started once Always is already on.

    Forward-only is a property of ``created_at``, so a chat that existed before
    the choice is deliberately not covered by it — which is why the chip tests
    below cannot reuse the fixture's conversation.
    """
    meta = conversations.new_conversation(OWNER.id, surface="web")
    conversations.append_turn(
        OWNER.id, meta.id, TurnEntry(role="user", text="and now?")
    )
    return meta.id


def test_the_chip_reads_the_conversations_own_status(chat):
    off = run(routes.conversation_status(chat.id, user=OWNER))
    assert not off.covered and not off.excluded and not off.shared

    run(
        routes.set_preference(
            routes.SharingPreferenceUpdate(state="always"), user=OWNER
        )
    )
    assert run(routes.conversation_status(_after_opt_in(), user=OWNER)).covered


def test_the_chip_stays_off_for_a_chat_that_predates_the_opt_in(chat):
    """Forward-only, as the user sees it: the chat they were already in when
    they chose Always is not covered, and the chip does not claim otherwise."""
    run(
        routes.set_preference(
            routes.SharingPreferenceUpdate(state="always"), user=OWNER
        )
    )
    assert not run(routes.conversation_status(chat.id, user=OWNER)).covered


def test_excluding_one_conversation_is_honoured_and_reversible(chat):
    run(
        routes.set_preference(
            routes.SharingPreferenceUpdate(state="always"), user=OWNER
        )
    )
    conv_id = _after_opt_in()

    excluded = run(
        routes.set_exclusion(conv_id, routes.ExclusionUpdate(excluded=True), user=OWNER)
    )
    assert excluded.excluded and not excluded.covered
    assert conversations.get_conversation(OWNER.id, conv_id).share_excluded

    back = run(
        routes.set_exclusion(
            conv_id, routes.ExclusionUpdate(excluded=False), user=OWNER
        )
    )
    assert not back.excluded and back.covered


def test_excluding_does_not_unshare_what_was_already_sent(chat):
    """Two verbs, two meanings. Leaving the sweep is not taking a copy back."""
    run(routes.submit_share(chat.id, user=OWNER))
    run(
        routes.set_exclusion(chat.id, routes.ExclusionUpdate(excluded=True), user=OWNER)
    )

    status = run(routes.conversation_status(chat.id, user=OWNER))
    assert status.excluded and status.shared
    assert len(run(routes.list_shared(user=OWNER))) == 1


def test_a_stranger_can_neither_read_nor_exclude_someone_elses_chat(chat):
    for call in (
        lambda: routes.conversation_status(chat.id, user=STRANGER),
        lambda: routes.set_exclusion(
            chat.id, routes.ExclusionUpdate(excluded=True), user=STRANGER
        ),
    ):
        with pytest.raises(HTTPException) as raised:
            run(call())
        assert raised.value.status_code == 404
    assert not conversations.get_conversation(OWNER.id, chat.id).share_excluded


def test_deleting_everything_shared_reaches_only_the_callers_own(chat):
    """Acceptance criterion: the button removes every row this user created."""
    mine = conversations.new_conversation(STRANGER.id, surface="web")
    conversations.append_turn(STRANGER.id, mine.id, TurnEntry(role="user", text="hi"))
    run(routes.submit_share(chat.id, user=OWNER))
    run(routes.submit_share(mine.id, user=STRANGER))

    assert run(routes.unshare_everything(user=OWNER)) == {"unshared": 1}
    assert run(routes.list_shared(user=OWNER)) == []
    assert len(run(routes.list_shared(user=STRANGER))) == 1


def test_unsharing_everything_still_works_while_the_veto_is_on(chat):
    """Withdrawal is never behind the switch it is withdrawing from."""
    run(routes.submit_share(chat.id, user=OWNER))
    consent.set_install_allows(False)
    assert run(routes.unshare_everything(user=OWNER)) == {"unshared": 1}
