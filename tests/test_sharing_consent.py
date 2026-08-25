"""Sharing consent, identity and the queue (FEAT-054).

The negative assertions again carry the file: that a default install shares
nothing, that ``share_secret`` never reaches anything serialized, that each of
the two vetoes works on its own, and that what the dialog rendered is what the
queue holds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

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


# ── The cap is scoped to shares (CORR-234) ───────────────────────────────


def _unshare(n: int) -> dict:
    """One queued revocation, as ``share.unshare`` would have written it."""
    return outbox.enqueue(
        outbox.OP_UNSHARE,
        outbox.unshare_endpoint(str(n)),
        wire.unshare_body(f"token-{n}"),
        share_id=str(n),
        user_id=4242,
        kind=outbox.OP_UNSHARE,
    )


def test_the_cap_never_evicts_a_pending_unshare(chat):
    """Acceptance criterion: withdrawing sixty conversations withdraws sixty.

    The queued record holds the only surviving copy of the delete token —
    ``unshare`` clears it from the meta as it queues — so an eviction here is
    not a delay, it is a transcript nobody can ever take back.
    """
    for n in range(60):
        _unshare(n)

    queued = outbox.pending()
    assert len(queued) == 60
    assert [r["share_id"] for r in queued] == [str(n) for n in range(60)]


def test_an_unshare_older_than_the_age_cutoff_survives_a_trim(chat):
    """An install offline for a fortnight still owes those deletions."""
    stale = time.time() - outbox.MAX_QUEUE_AGE_S - 1
    outbox._write(
        [
            {"id": "old-share", "op": outbox.OP_SHARE, "url": "u", "queued_at": stale},
            {"id": "old-undo", "op": outbox.OP_UNSHARE, "url": "u", "queued_at": stale},
        ]
    )

    outbox.trim()

    assert [r["id"] for r in outbox.pending()] == ["old-undo"]


def test_an_unshare_does_not_use_up_a_shares_place_in_the_cap(chat, monkeypatch):
    """The cap counts transcripts, so revocations do not crowd shares out."""
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 3)
    _unshare(0)
    for _ in range(3):
        share.submit(4242, chat.id)

    assert [r["op"] for r in outbox.pending()] == [
        outbox.OP_UNSHARE,
        outbox.OP_SHARE,
        outbox.OP_SHARE,
        outbox.OP_SHARE,
    ]


def test_a_mixed_queue_past_the_cap_keeps_every_pair_in_order(chat, monkeypatch):
    """Acceptance criterion: a share and the unshare that revokes it never swap.

    Interleaved well past the cap, so shares are really being dropped while the
    unshares between them stay: what survives must still be a subsequence of
    what was queued.
    """
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 3)
    queued: list[str] = []
    for n in range(8):
        queued.append(
            outbox.enqueue(outbox.OP_SHARE, f"https://collector.invalid/{n}", {"n": n})[
                "id"
            ]
        )
        queued.append(_unshare(n)["id"])

    surviving = [r["id"] for r in outbox.pending()]
    assert [i for i in queued if i in set(surviving)] == surviving
    assert sum(r["op"] == outbox.OP_UNSHARE for r in outbox.pending()) == 8
    assert sum(r["op"] == outbox.OP_SHARE for r in outbox.pending()) == 3


@pytest.mark.asyncio
async def test_a_terminally_refused_unshare_is_logged_where_it_will_be_seen(
    chat, caplog
):
    """Acceptance criterion: the install giving up on a revocation is not a
    debug line. A refused *share* is a dropped upload; a refused unshare is a
    transcript left on the collector with its delete token already gone."""

    class _Refused:
        status = 403

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session(_Refused):
        def post(self, *a, **kw):
            return _Refused()

    import aiohttp

    caplog.set_level(logging.DEBUG)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(aiohttp, "ClientSession", lambda **kw: _Session())
        assert await outbox.post(_unshare(7)) is True

    refusal = [r for r in caplog.records if "refused an unshare" in r.getMessage()]
    assert refusal and refusal[0].levelno >= logging.ERROR


# ── The vetoes reach what is already queued (CORR-233) ───────────────────


def _posted(sink: list):
    async def _post(record):
        sink.append(record)
        return True

    return _post


@pytest.mark.asyncio
async def test_the_kill_switch_drops_a_share_that_was_already_queued(chat, monkeypatch):
    """Consent is checked when the share is sent, not only when it was made."""
    share.submit(4242, chat.id)
    sent: list = []
    monkeypatch.setattr(outbox, "post", _posted(sent))
    monkeypatch.setenv(consent.ENV_VAR, "off")

    delivered, remaining = await outbox.flush()

    assert sent == []
    assert (delivered, remaining) == (0, 0)
    assert outbox.pending() == []


@pytest.mark.asyncio
async def test_the_admin_veto_drops_a_share_that_was_already_queued(chat, monkeypatch):
    share.submit(4242, chat.id)
    sent: list = []
    monkeypatch.setattr(outbox, "post", _posted(sent))
    consent.set_install_allows(False)

    assert await outbox.flush() == (0, 0)
    assert sent == []
    assert outbox.pending() == []


@pytest.mark.asyncio
async def test_a_queued_unshare_survives_the_kill_switch(chat, monkeypatch):
    """A revocation is owed to the user; the veto is about what may go out."""
    share.submit(4242, chat.id)
    share.unshare(4242, chat.id)
    sent: list = []
    monkeypatch.setattr(outbox, "post", _posted(sent))
    monkeypatch.setenv(consent.ENV_VAR, "off")

    delivered, remaining = await outbox.flush()

    assert [r["op"] for r in sent] == [outbox.OP_UNSHARE]
    assert (delivered, remaining) == (1, 0)


@pytest.mark.asyncio
async def test_a_queued_unshare_survives_the_admin_veto(chat, monkeypatch):
    share.submit(4242, chat.id)
    share.unshare(4242, chat.id)
    sent: list = []
    monkeypatch.setattr(outbox, "post", _posted(sent))
    consent.set_install_allows(False)

    assert await outbox.flush() == (1, 0)
    assert [r["op"] for r in sent] == [outbox.OP_UNSHARE]


@pytest.mark.asyncio
async def test_a_vetoed_share_is_dropped_even_behind_a_stalled_record(
    chat, monkeypatch
):
    """The kill switch does not wait for the collector to come back."""
    share.submit(4242, chat.id)
    share.unshare(4242, chat.id)
    share.submit(4242, chat.id)
    monkeypatch.setattr(outbox, "post", _always(False))
    monkeypatch.setenv(consent.ENV_VAR, "off")

    await outbox.flush()

    assert [r["op"] for r in outbox.pending()] == [outbox.OP_UNSHARE]


def test_the_admin_veto_destroys_what_is_undelivered(chat):
    """Set it and the transcripts are gone, not merely unsent."""
    share.submit(4242, chat.id)
    conversations.append_turn(4242, chat.id, TurnEntry(role="user", text="again"))
    other = conversations.new_conversation(99, surface="web", agent_slug="condor")
    conversations.append_turn(99, other.id, TurnEntry(role="user", text="mine"))
    share.submit(99, other.id)
    share.unshare(4242, chat.id)
    assert len(outbox.pending()) == 3

    consent.set_install_allows(False)

    assert [r["op"] for r in outbox.pending()] == [outbox.OP_UNSHARE]


def test_lifting_the_veto_does_not_resurrect_anything(chat):
    share.submit(4242, chat.id)
    consent.set_install_allows(False)
    consent.set_install_allows(True)

    assert outbox.pending() == []


def test_purge_shares_never_takes_a_pending_revocation(chat):
    """The delete token lives nowhere else once unshare has queued it."""
    share.submit(4242, chat.id)
    token = conversations.get_conversation(4242, chat.id).share_delete_token
    share.unshare(4242, chat.id)

    assert outbox.purge_shares() == 1

    revocation = outbox.pending()
    assert len(revocation) == 1
    assert revocation[0]["body"]["delete_token"] == token


# ── The queue has two writers (CORR-232) ─────────────────────────────────


def _enqueue_during(fn, monkeypatch, *, reader: str = "_read") -> dict:
    """Run ``fn`` while another thread appends to the queue. Returns its record.

    The append is aimed at the window between a read-modify-write's read and its
    write — the window a flush of a full queue holds open for minutes. If the
    write path is locked the append lands just after that window instead of
    inside it; either way the record must be in the file afterwards.

    ``reader`` names the read that opens that window, because the queue has two:
    the record-shaped :func:`outbox._read` and the line-shaped
    ``outbox._read_probes`` the count-capped paths use (PERF-237).
    """
    reached = threading.Event()
    real_read = getattr(outbox, reader)
    slowed: list[bool] = []

    def slow_read():
        records = real_read()
        if not slowed:  # only the read that opens the window
            slowed.append(True)
            reached.set()
            time.sleep(0.2)
        return records

    queued: list[dict] = []

    def worker():
        reached.wait(5)
        queued.append(
            outbox.enqueue(
                outbox.OP_SHARE, "https://collector.invalid/late", {}, user_id=99
            )
        )

    thread = threading.Thread(target=worker)
    monkeypatch.setattr(outbox, reader, slow_read)
    thread.start()
    try:
        fn()
    finally:
        thread.join(5)
        monkeypatch.setattr(outbox, reader, real_read)
    assert queued, "the worker thread never got to enqueue"
    return queued[0]


def test_trim_keeps_what_a_worker_thread_appends_while_it_runs(chat):
    stale = time.time() - outbox.MAX_QUEUE_AGE_S - 1
    outbox._write(
        [
            {"id": "old", "op": outbox.OP_SHARE, "url": "u", "queued_at": stale},
            {"id": "kept", "op": outbox.OP_SHARE, "url": "u", "queued_at": time.time()},
        ]
    )

    with pytest.MonkeyPatch.context() as mp:
        late = _enqueue_during(outbox.trim, mp, reader="_read_probes")

    # The cap was still enforced, and the sweep's append was not collateral.
    assert [r["id"] for r in outbox.pending()] == ["kept", late["id"]]


def test_purge_user_shares_keeps_what_a_worker_thread_appends_while_it_runs(chat):
    outbox.enqueue(outbox.OP_SHARE, "u", {}, user_id=4242, kind="passive")

    with pytest.MonkeyPatch.context() as mp:
        late = _enqueue_during(
            lambda: outbox.purge_user_shares(4242, kind="passive"), mp
        )

    assert [r["id"] for r in outbox.pending()] == [late["id"]]


@pytest.mark.asyncio
async def test_a_flush_keeps_what_was_queued_while_it_was_posting(chat, monkeypatch):
    """Acceptance criterion: a POST that enqueues does not lose its own record."""
    first = outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/1", {"n": 1})
    second = outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/2", {"n": 2})

    arrivals: list[dict] = []

    async def post_and_enqueue(record):
        arrivals.append(
            outbox.enqueue(
                outbox.OP_SHARE,
                "https://collector.invalid/late",
                {"after": record["id"]},
            )
        )
        return True

    monkeypatch.setattr(outbox, "post", post_and_enqueue)
    delivered, remaining = await outbox.flush()

    assert (delivered, remaining) == (2, 2)
    queued = [r["id"] for r in outbox.pending()]
    assert first["id"] not in queued and second["id"] not in queued
    assert queued == [a["id"] for a in arrivals]  # and in the order they arrived


@pytest.mark.asyncio
async def test_an_unshare_queued_during_a_flush_survives_it(chat, monkeypatch):
    """The worst case: the queue holds the only copy of the delete token.

    ``share.unshare`` clears ``share_delete_token`` from the meta as soon as it
    queues, so a flush that dropped the record would leave nothing on the box
    able to revoke — permanently, silently.
    """
    share.submit(4242, chat.id)

    async def post_then_unshare(record):
        if record["op"] == outbox.OP_SHARE:
            share.unshare(4242, chat.id)  # a user pressing Unshare mid-flight
        return True

    monkeypatch.setattr(outbox, "post", post_then_unshare)
    delivered, remaining = await outbox.flush()
    assert (delivered, remaining) == (1, 1)
    assert [r["op"] for r in outbox.pending()] == [outbox.OP_UNSHARE]

    posted: list[dict] = []

    async def record_post(record):
        posted.append(record)
        return True

    monkeypatch.setattr(outbox, "post", record_post)
    assert await outbox.flush() == (1, 0)
    assert [r["op"] for r in posted] == [outbox.OP_UNSHARE]


@pytest.mark.asyncio
async def test_a_stalled_flush_still_keeps_a_concurrent_arrival(chat, monkeypatch):
    """Order survives too: nothing overtakes the record that stalled."""
    stalled = outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/1", {"n": 1})
    behind = outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/2", {"n": 2})

    async def post_and_enqueue(record):
        outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/late", {})
        return False

    monkeypatch.setattr(outbox, "post", post_and_enqueue)
    delivered, remaining = await outbox.flush()

    assert (delivered, remaining) == (0, 3)
    queued = outbox.pending()
    assert [r["id"] for r in queued[:2]] == [stalled["id"], behind["id"]]


@pytest.mark.asyncio
async def test_two_overlapping_flushes_never_post_the_same_record_twice(
    chat, monkeypatch
):
    """The job fires every 300s; a full queue of 10s timeouts can outlast that."""
    outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/1", {"n": 1})
    posted: list[str] = []
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def slow_post(record):
        posted.append(record["id"])
        in_flight.set()
        await release.wait()
        return True

    monkeypatch.setattr(outbox, "post", slow_post)
    first = asyncio.create_task(outbox.flush())
    await in_flight.wait()

    assert await outbox.flush() == (0, 1)  # the second stands down
    release.set()
    assert await first == (1, 0)
    assert len(posted) == 1


@pytest.mark.asyncio
async def test_no_lock_is_held_while_a_record_is_in_flight(chat, monkeypatch):
    """A POST can take ``POST_TIMEOUT_S``; producers must not block on it."""
    outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/1", {"n": 1})
    free: list[bool] = []

    def _lock_is_free() -> bool:
        # From another thread: an RLock would let this one straight back in.
        acquired = outbox._QUEUE_LOCK.acquire(timeout=1)
        if acquired:
            outbox._QUEUE_LOCK.release()
        return acquired

    async def post(record):
        free.append(await asyncio.to_thread(_lock_is_free))
        return True

    monkeypatch.setattr(outbox, "post", post)
    await outbox.flush()
    assert free == [True]


@pytest.mark.asyncio
async def test_a_record_queued_before_ids_existed_is_still_retired(chat, monkeypatch):
    """An upgrade finds a queue written by the version that had no ``id``."""
    outbox._write(
        [
            {"op": outbox.OP_SHARE, "url": "u", "body": {}, "queued_at": time.time()},
            {"op": outbox.OP_SHARE, "url": "v", "body": {}, "queued_at": time.time()},
        ]
    )
    delivered_urls: list[str] = []

    async def post(record):
        delivered_urls.append(record["url"])
        return record["url"] == "u"  # the second stalls

    monkeypatch.setattr(outbox, "post", post)
    assert await outbox.flush() == (1, 1)
    assert [r["url"] for r in outbox.pending()] == ["v"]


# ── The count-shaped paths do not read transcripts (PERF-237) ────────────


def _count_loads(monkeypatch) -> list[str]:
    """Record every line ``json.loads`` is handed while the fixture is up."""
    seen: list[str] = []
    real = json.loads

    def counting(text, *args, **kwargs):
        seen.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(outbox.json, "loads", counting)
    return seen


def test_trim_does_not_parse_the_records_it_keeps(chat, monkeypatch):
    """Acceptance criterion: the cap is a count, so enforcing it must not cost
    a transcript. Every enqueue trims, three times a sweep tick, and a queued
    record is a whole conversation."""
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 10)
    for n in range(4):
        outbox.enqueue(outbox.OP_SHARE, f"https://collector.invalid/{n}", {"n": n})
        _unshare(n)

    parsed = _count_loads(monkeypatch)
    outbox.trim()

    assert parsed == []
    assert len(outbox.pending()) == 8


def test_trim_does_not_parse_the_records_it_drops_either(chat, monkeypatch):
    """Dropping the oldest is a truncation of a list of lines, not a rewrite of
    a list of transcripts: nothing is parsed and nothing is re-serialised."""
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 4)
    for n in range(4):
        outbox.enqueue(outbox.OP_SHARE, f"https://collector.invalid/{n}", {"n": n})
    assert len(outbox.pending()) == 4

    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 2)
    parsed = _count_loads(monkeypatch)
    outbox.trim()

    assert parsed == []
    assert [r["body"]["n"] for r in outbox.pending()] == [2, 3]


def test_count_agrees_with_pending_without_materialising_it(chat, monkeypatch):
    """Acceptance criterion: ``GET /sharing/settings`` reports the same number
    ``len(outbox.pending())`` would, having parsed nothing to get it."""
    for n in range(3):
        outbox.enqueue(outbox.OP_SHARE, f"https://collector.invalid/{n}", {"n": n})
    _unshare(9)

    expected = len(outbox.pending())
    parsed = _count_loads(monkeypatch)

    assert outbox.count() == expected == 4
    assert parsed == []


def test_count_skips_a_torn_line_exactly_as_pending_does(chat):
    """A killed process leaves half a record behind; the two must still agree."""
    outbox.enqueue(outbox.OP_SHARE, "https://collector.invalid/1", {"n": 1})
    with outbox.queue_path().open("a", encoding="utf-8") as fh:
        fh.write('{"id":"torn","op":"share","url":"u","bo')

    assert outbox.count() == len(outbox.pending()) == 1


def test_a_record_the_probe_cannot_read_is_still_trimmed_correctly(chat, monkeypatch):
    """An upgrade finds records whose keys are not where the probe looks — a
    queue written before ``id`` existed, say. They are parsed the slow, certain
    way rather than misread, so the policy sees the same fields either way."""
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 1)
    now = time.time()
    outbox._write(
        [
            {"op": outbox.OP_SHARE, "url": "old", "queued_at": now, "id": "a"},
            {"op": outbox.OP_UNSHARE, "url": "undo", "queued_at": now, "id": "b"},
            {"op": outbox.OP_SHARE, "url": "new", "queued_at": now, "id": "c"},
        ]
    )

    outbox.trim()

    assert [r["id"] for r in outbox.pending()] == ["b", "c"]


def test_a_trim_that_drops_nothing_leaves_the_file_untouched(chat):
    """Acceptance criterion, half one: a no-op trim costs no rename."""
    for n in range(3):
        outbox.enqueue(outbox.OP_SHARE, f"https://collector.invalid/{n}", {"n": n})
    before = outbox.queue_path().read_bytes()
    stat = outbox.queue_path().stat()

    outbox.trim()

    assert outbox.queue_path().read_bytes() == before
    assert outbox.queue_path().stat().st_mtime_ns == stat.st_mtime_ns


def test_the_survivors_of_a_trim_are_byte_identical_to_what_was_appended(
    chat, monkeypatch
):
    """Acceptance criterion, half two: the kept lines are written back verbatim
    rather than round-tripped through ``json``."""
    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 4)
    for n in range(4):
        outbox.enqueue(outbox.OP_SHARE, f"https://collector.invalid/{n}", {"n": n})
    appended = outbox.queue_path().read_text().splitlines()

    monkeypatch.setattr(outbox, "MAX_QUEUED_SHARES", 2)
    outbox.trim()

    assert outbox.queue_path().read_text().splitlines() == appended[2:]


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
