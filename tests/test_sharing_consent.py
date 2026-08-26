"""Sharing consent, identity and the queue (FEAT-054).

The negative assertions again carry the file: that a default install shares
nothing, that ``share_secret`` never reaches anything serialized, that each of
the two vetoes works on its own, and that what the dialog rendered is what the
queue holds.
"""

from __future__ import annotations

import json

import pytest

from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry
from condor.sharing import consent, outbox, share, wire


@pytest.fixture
def install(tmp_path, monkeypatch):
    """An isolated install: its own config.yml, its own runtime root, no env.

    ``_isolated_runtime_root`` in conftest already repoints the runtime root, so
    the queue and the conversations land under ``tmp_path`` without any private
    name being monkeypatched here.
    """
    import config_manager as cm_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(consent.ENV_VAR, raising=False)
    cm_module.ConfigManager.reset_instance()
    yield tmp_path
    cm_module.ConfigManager.reset_instance()


@pytest.fixture
def chat(install):
    """One user with one two-turn conversation, and nothing shared."""
    from config_manager import get_config_manager

    get_config_manager()  # materialize config.yml in the tmp cwd
    meta = conversations.new_conversation(4242, surface="web", agent_slug="condor")
    conversations.append_turn(
        4242, meta.id, TurnEntry(role="user", text="check SOL-USDC at 142.35")
    )
    conversations.append_turn(
        4242, meta.id, TurnEntry(role="assistant", text="the book is thin")
    )
    return meta


# ── Nothing leaves on its own ────────────────────────────────────────────


def test_a_conversation_held_end_to_end_queues_nothing(chat):
    """Acceptance criterion: with no action taken, no conversation content
    leaves the install. A whole chat is held, and the queue is inspected."""
    assert outbox.pending() == []
    assert not outbox.queue_path().exists()


def test_previewing_sends_nothing(chat):
    share.preview(4242, chat.id)
    assert outbox.pending() == []


# ── The gates ────────────────────────────────────────────────────────────


def test_sharing_is_allowed_by_default(chat):
    """The admin veto is a switch an admin reaches for, not a second opt-in a
    user has to chase — nothing is sent without a button press anyway."""
    assert consent.install_allows()
    assert consent.can_share(4242)


def test_the_admin_veto_suppresses_it_for_everyone(chat):
    consent.set_install_allows(False)
    assert not consent.can_share(4242)
    assert not consent.can_share(1)


def test_the_environment_kill_switch_outranks_the_stored_answer(chat, monkeypatch):
    consent.set_install_allows(True)
    monkeypatch.setenv(consent.ENV_VAR, "off")
    assert not consent.can_share(4242)
    assert consent.env_overridden()


def test_a_user_state_is_off_until_something_sets_it(chat):
    """FEAT-054 never moves anyone off ``off``: the button is the consent, and
    it is recorded on the conversation. ``always`` is FEAT-055's to set."""
    assert consent.user_state(4242) == consent.OFF
    assert consent.user_state(999) == consent.OFF


# ── Identity ─────────────────────────────────────────────────────────────


def test_the_sharing_identity_is_not_the_telemetry_one(chat):
    from condor.telemetry import consent as telemetry_consent

    telemetry_consent.grant("usage")
    consent.ensure_identity()
    assert consent.share_install_id()
    assert consent.share_install_id() != telemetry_consent.install_id()


def test_an_install_that_never_shares_never_grows_the_section(install):
    from config_manager import get_config_manager

    assert get_config_manager().get_sharing() == {}


def test_the_share_secret_never_reaches_the_wire(chat):
    consent.ensure_identity()
    secret = consent.share_secret()
    assert secret

    receipt = share.submit(4242, chat.id)
    queued = json.dumps(outbox.pending())
    assert secret not in queued
    assert secret not in receipt.model_dump_json()


def test_a_receipt_never_carries_the_delete_token(chat):
    receipt = share.submit(4242, chat.id)
    assert "delete_token" not in receipt.model_dump()


def test_the_delete_token_is_not_serialized_off_the_conversation(chat):
    """An admin may read someone else's conversation. Handing them the
    capability that revokes its share would be a different thing entirely."""
    share.submit(4242, chat.id)
    meta = conversations.get_conversation(4242, chat.id)
    assert meta.share_delete_token
    assert "share_delete_token" not in meta.model_dump(mode="json")


# ── Preview is what is sent ──────────────────────────────────────────────


def test_preview_and_submit_produce_byte_identical_turns(chat):
    """Acceptance criterion: pressing Share sends exactly the bytes the dialog
    displayed. The two verbs call one builder, and this is what says so."""
    previewed = share.preview(4242, chat.id)
    share.submit(4242, chat.id)

    sent = outbox.pending()[0]["body"]["turns"]
    assert sent == [t.model_dump(mode="json") for t in previewed.turns]


def test_the_envelope_carries_the_redaction_counts(chat):
    share.submit(4242, chat.id)
    redaction = outbox.pending()[0]["body"]["redaction"]
    from condor.sharing import scrub

    assert set(redaction["counts"]) == set(scrub.CATEGORIES)
    assert redaction["truncated"] is False


def test_a_share_never_carries_the_events_taxonomy_shape(chat):
    """The two pipelines share no wire schema. An envelope from here has no
    ``events``, no ``level`` and no ``install_id`` — the three fields the
    collector's events path requires."""
    share.submit(4242, chat.id)
    body = outbox.pending()[0]["body"]
    assert "events" not in body and "level" not in body
    assert "install_id" not in body and "share_install_id" in body


# ── Re-sharing and revoking ──────────────────────────────────────────────


def test_re_sharing_keeps_the_id_and_bumps_the_revision(chat):
    first = share.submit(4242, chat.id)
    conversations.append_turn(4242, chat.id, TurnEntry(role="user", text="and now?"))
    second = share.submit(4242, chat.id)

    assert second.share_id == first.share_id
    assert second.revision == first.revision + 1
    assert [r["body"]["revision"] for r in outbox.pending()] == [1, 2]


def test_unshare_queues_the_token_and_clears_the_receipt(chat):
    share.submit(4242, chat.id)
    token = conversations.get_conversation(4242, chat.id).share_delete_token

    assert share.unshare(4242, chat.id) is True

    revocation = outbox.pending()[-1]
    assert revocation["op"] == outbox.OP_UNSHARE
    assert revocation["body"]["delete_token"] == token
    assert conversations.get_conversation(4242, chat.id).share_id == ""


def test_unsharing_something_that_was_never_shared_is_a_no_op(chat):
    assert share.unshare(4242, chat.id) is False
    assert outbox.pending() == []


def test_only_the_hash_of_the_delete_token_is_sent(chat):
    share.submit(4242, chat.id)
    token = conversations.get_conversation(4242, chat.id).share_delete_token
    body = outbox.pending()[0]["body"]
    assert body["delete_token_hash"] == wire.token_hash(token)
    assert token not in json.dumps(body)


def test_list_shares_reports_what_is_currently_out(chat):
    assert share.list_shares(4242) == []
    share.submit(4242, chat.id)
    listed = share.list_shares(4242)
    assert [s["conversation_id"] for s in listed] == [chat.id]
    share.unshare(4242, chat.id)
    assert share.list_shares(4242) == []


# ── The queue ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_post_leaves_the_share_queued(chat, monkeypatch):
    share.submit(4242, chat.id)
    monkeypatch.setattr(outbox, "post", _always(False))

    delivered, remaining = await outbox.flush()
    assert (delivered, remaining) == (0, 1)
    assert len(outbox.pending()) == 1


@pytest.mark.asyncio
async def test_a_delivered_share_leaves_the_queue(chat, monkeypatch):
    share.submit(4242, chat.id)
    monkeypatch.setattr(outbox, "post", _always(True))

    delivered, remaining = await outbox.flush()
    assert (delivered, remaining) == (1, 0)
    assert outbox.pending() == []


@pytest.mark.asyncio
async def test_a_stalled_record_does_not_let_the_next_one_overtake_it(
    chat, monkeypatch
):
    """A share and the unshare that revokes it must not arrive out of order."""
    share.submit(4242, chat.id)
    share.unshare(4242, chat.id)
    monkeypatch.setattr(outbox, "post", _always(False))

    await outbox.flush()
    assert [r["op"] for r in outbox.pending()] == [outbox.OP_SHARE, outbox.OP_UNSHARE]


def test_the_queue_is_capped(chat, monkeypatch):
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 3)
    for _ in range(6):
        share.submit(4242, chat.id)
    assert len(outbox.pending()) == 3
    # Oldest first: what survives is the newest, which is what a retry wants.
    assert [r["body"]["revision"] for r in outbox.pending()] == [4, 5, 6]


# ── Bounding ─────────────────────────────────────────────────────────────


def test_an_oversized_transcript_is_cut_from_the_middle_and_marked():
    """Acceptance criterion: a transcript over the cap is truncated from the
    middle, marked, and still small enough for the collector to accept."""
    turns = [TurnEntry(role="user", text=f"{i} " + "x" * 40_000) for i in range(120)]
    bounded, truncated = wire.bound(turns)

    assert truncated
    assert bounded[0] == turns[0] and bounded[-1] == turns[-1]
    markers = [t for t in bounded if t.kind == wire.OMITTED_KIND]
    assert len(markers) == 1 and "omitted" in markers[0].text
    assert wire._size(bounded) <= wire.MAX_SHARE_BYTES


def _always(result: bool):
    async def _post(record):
        return result

    return _post
