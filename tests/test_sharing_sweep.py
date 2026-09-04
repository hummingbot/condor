"""The automatic producer (FEAT-055).

This is the feature where nobody reads the payload before it leaves, so the
tests that matter are the ones that assert the sweep does **not** run: a default
install, a conversation from before the opt-in, a room with somebody else in it,
an excluded chat, one that has not grown. Each rule gets a conversation that
fails only that rule, so a regression in one of them cannot hide behind another.
"""

from __future__ import annotations

import time

import pytest

from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry
from condor.sharing import consent, outbox, share, sweep, wire

USER = 4242
OTHER = 5353

# Comfortably past IDLE_S, so a conversation stamped this far back is finished
# by any reading of the rule.
LONG_AGO = sweep.IDLE_S + 600


@pytest.fixture
def install(tmp_path, monkeypatch):
    """An isolated install: its own config.yml, its own runtime root, no env."""
    import config_manager as cm_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(consent.ENV_VAR, raising=False)
    cm_module.ConfigManager.reset_instance()
    from config_manager import get_config_manager

    get_config_manager()  # materialize config.yml in the tmp cwd
    yield tmp_path
    cm_module.ConfigManager.reset_instance()


def _conversation(
    user_id: int = USER,
    *,
    surface: str = "web",
    turns: int = 2,
    age_s: float = LONG_AGO,
    multi_author: bool = False,
):
    """One conversation, aged into the past.

    ``created_at``/``updated_at`` are rewritten through ``update_meta`` rather
    than faked in memory: the sweep reads what is on disk, so a test that only
    lied to the object would be testing nothing.
    """
    meta = conversations.new_conversation(
        user_id, surface=surface, multi_author=multi_author
    )
    for i in range(turns):
        conversations.append_turn(
            user_id,
            meta.id,
            TurnEntry(role="user" if i % 2 == 0 else "assistant", text=f"turn {i}"),
        )
    stamp = time.time() - age_s
    _touch(user_id, meta.id, created=stamp, updated=stamp)
    return conversations.get_conversation(user_id, meta.id)


def _touch(user_id: int, conv_id: str, *, created: float | None = None, updated: float):
    """Backdate a conversation on disk.

    ``write_status`` stamps ``updated_at`` itself on every merge, so it is
    written directly here — the whole point is to control the clock the sweep
    reads, and going through ``update_meta`` would overwrite it with now.
    """
    from condor.fsutil import atomic_write_json
    from condor.runtime.registry_file import read_status, status_path

    conv_dir = conversations._conv_dir(user_id, conv_id)
    data = read_status(conv_dir, conversations.META_FILENAME) or {}
    if created is not None:
        data["created_at"] = created
    data["updated_at"] = updated
    atomic_write_json(
        status_path(conv_dir, conversations.META_FILENAME), data, indent=2
    )


# Far enough back that every conversation these tests build counts as created
# after the opt-in. Forward-only gets its own test, which sets ``when`` by hand.
OPTED_IN_AGO = 7 * 24 * 3600


def _opt_in(user_id: int = USER, *, when: float | None = None):
    """Put a user at Always, backdating the moment they chose it.

    Backdated by default because every other rule is tested against a
    conversation that is already old, and an opt-in stamped *now* would fail all
    of them on forward-only before the rule under test was ever reached.
    """
    consent.set_user_state(user_id, consent.ALWAYS)
    states = dict(consent._section().get("users") or {})
    states[str(user_id)] = {
        "state": consent.ALWAYS,
        "opted_in_at": time.time() - OPTED_IN_AGO if when is None else when,
    }
    consent._update(users=states)


# ── Nothing happens on its own ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_default_install_sweeps_nothing(install):
    """Acceptance criterion: a fresh install with no interaction shares nothing.

    A finished conversation is sitting right there and the sweep still declines
    it, because nobody has said Always."""
    _conversation()
    assert consent.user_state(USER) == consent.OFF
    assert await sweep.sweep() == 0
    assert outbox.pending() == []


@pytest.mark.asyncio
async def test_ask_is_not_always(install):
    """``explicit`` records that the user chose the button. It is not consent to
    the sweep, and the two must not collapse into "anything but off"."""
    _conversation()
    consent.set_user_state(USER, consent.EXPLICIT)
    assert await sweep.sweep() == 0


# ── The gates still outrank a standing yes ───────────────────────────────


@pytest.mark.asyncio
async def test_the_admin_veto_outranks_always(install):
    meta = _conversation()
    _opt_in()
    assert sweep.eligible(USER) and sweep.covered(meta, USER)

    consent.set_install_allows(False)
    assert sweep.eligible(USER) == []
    assert not sweep.covered(meta, USER)
    assert await sweep.sweep() == 0


@pytest.mark.asyncio
async def test_the_environment_kill_switch_outranks_always(install, monkeypatch):
    _conversation()
    _opt_in()
    monkeypatch.setenv(consent.ENV_VAR, "off")

    assert sweep.eligible(USER) == []
    assert await sweep.sweep() == 0


# ── One rule at a time ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_idle_conversation_is_swept(install):
    """Acceptance criterion: with Always, an idle conversation is shared."""
    meta = _conversation()
    _opt_in()

    assert await sweep.sweep() == 1
    queued = outbox.pending()
    assert len(queued) == 1
    assert queued[0]["body"]["kind"] == wire.KIND_PASSIVE
    assert conversations.get_conversation(USER, meta.id).share_id


@pytest.mark.asyncio
async def test_a_still_active_conversation_is_not(install):
    """Acceptance criterion: a conversation still being used is left alone."""
    _conversation(age_s=60)
    _opt_in()

    assert sweep.eligible(USER) == []
    assert await sweep.sweep() == 0


@pytest.mark.asyncio
async def test_conversations_from_before_the_opt_in_are_never_swept(install):
    """Acceptance criterion: forward-only. Consent is to a policy from now on,
    not a licence over the archive."""
    old = _conversation(age_s=LONG_AGO)
    _opt_in(when=time.time() - 60)

    assert sweep.eligible(USER) == []
    assert not sweep.covered(old, USER)
    assert await sweep.sweep() == 0

    # And the button still works on it — the deliberate path is unaffected.
    share.submit(USER, old.id)
    assert len(outbox.pending()) == 1


@pytest.mark.asyncio
async def test_always_without_an_opt_in_time_sweeps_nothing(install):
    """A hand-edited config that says ``always`` with no timestamp cannot have
    forward-only enforced against it, so it gets nothing rather than everything."""
    _conversation()
    consent._update(users={str(USER): {"state": consent.ALWAYS}})

    assert consent.user_state(USER) == consent.ALWAYS
    assert sweep.eligible(USER) == []
    assert await sweep.sweep() == 0


@pytest.mark.asyncio
async def test_a_room_with_other_people_in_it_is_never_swept(install):
    """Acceptance criterion: a user consents for themselves, not for the others
    in a Telegram group — and that conversation is still shareable by hand."""
    group = _conversation(surface="tg", multi_author=True)
    _opt_in()

    assert not sweep.attributable(group)
    assert sweep.eligible(USER) == []
    assert await sweep.sweep() == 0

    share.submit(USER, group.id)
    assert len(outbox.pending()) == 1


@pytest.mark.asyncio
async def test_a_private_telegram_chat_is_swept(install):
    """The counterpart of the test above: Telegram is not excluded wholesale,
    only the rooms that admit somebody else."""
    _conversation(surface="tg")
    _opt_in()
    assert await sweep.sweep() == 1


@pytest.mark.asyncio
async def test_an_unknown_surface_is_refused_rather_than_assumed_solo(install):
    """A frontend added after this module does not silently inherit permission
    to upload other people's words."""
    stray = _conversation(surface="carrier-pigeon")
    _opt_in()

    assert not sweep.attributable(stray)
    assert await sweep.sweep() == 0


@pytest.mark.asyncio
async def test_an_excluded_conversation_is_never_shared(install):
    """Acceptance criterion: excluded before or after it grows, it never goes."""
    meta = _conversation()
    _opt_in()
    conversations.update_meta(USER, meta.id, share_excluded=True)
    _touch(USER, meta.id, updated=time.time() - LONG_AGO)

    assert sweep.eligible(USER) == []
    assert await sweep.sweep() == 0

    # It grows, goes idle again, and is still refused.
    conversations.append_turn(USER, meta.id, TurnEntry(role="user", text="more"))
    _touch(USER, meta.id, updated=time.time() - LONG_AGO)
    assert await sweep.sweep() == 0
    assert outbox.pending() == []


@pytest.mark.asyncio
async def test_unsharing_stops_the_sweep_from_sending_it_again(install):
    """Acceptance criterion (CORR-231): Unshare is a standing refusal.

    Clearing the receipt alone left the conversation *maximally* eligible —
    ``share_turn_count`` back at zero is what the growth gate reads as "never
    sent" — so the next tick re-uploaded it under a new ``share_id`` while the
    UI said it was not shared.
    """
    meta = _conversation()
    _opt_in()
    assert await sweep.sweep() == 1
    assert conversations.get_conversation(USER, meta.id).share_id

    assert share.unshare(USER, meta.id) is True
    after = conversations.get_conversation(USER, meta.id)
    assert after.share_excluded is True
    assert after.share_id == ""

    # Idle again, and grown since — it would clear every other gate.
    conversations.append_turn(USER, meta.id, TurnEntry(role="user", text="more"))
    _touch(USER, meta.id, updated=time.time() - LONG_AGO)

    assert sweep.eligible(USER) == []
    assert await sweep.sweep() == 0
    assert conversations.get_conversation(USER, meta.id).share_id == ""
    assert [r["op"] for r in outbox.pending()] == [outbox.OP_SHARE, outbox.OP_UNSHARE]


@pytest.mark.asyncio
async def test_unsharing_everything_takes_the_back_catalogue_out_of_the_sweep(install):
    """Acceptance criterion (CORR-231): the Settings button holds too.

    ``unshare_all`` inherits the exclusion per conversation, so an ``always``
    user's archive does not reappear over the following ticks.
    """
    first = _conversation(USER)
    second = _conversation(USER)
    _opt_in(USER)
    share.submit(USER, first.id)
    share.submit(USER, second.id)

    assert share.unshare_all(USER) == 2
    for conv in (first, second):
        assert conversations.get_conversation(USER, conv.id).share_excluded is True

    for conv in (first, second):
        conversations.append_turn(USER, conv.id, TurnEntry(role="user", text="more"))
        _touch(USER, conv.id, updated=time.time() - LONG_AGO)

    assert await sweep.sweep() == 0
    assert share.list_shares(USER) == []


def test_unsharing_records_the_exclusion_for_a_user_who_never_opted_in(install):
    """Acceptance criterion (CORR-231): ``off``/``explicit`` behave as before.

    The flag is written for them too — it costs nothing and keeps one code path
    — but it changes no behaviour, because the sweep was never coming for them.
    """
    meta = _conversation()
    share.submit(USER, meta.id)

    assert share.unshare(USER, meta.id) is True
    after = conversations.get_conversation(USER, meta.id)
    assert after.share_excluded is True
    assert after.share_id == ""
    assert share.list_shares(USER) == []
    assert [r["op"] for r in outbox.pending()] == [outbox.OP_SHARE, outbox.OP_UNSHARE]
    assert sweep.covered(after, USER) is False

    # And it can still be handed over again deliberately.
    assert share.submit(USER, meta.id).share_id


@pytest.mark.asyncio
async def test_including_it_again_puts_it_back(install):
    meta = _conversation()
    _opt_in()
    conversations.update_meta(USER, meta.id, share_excluded=True)
    conversations.update_meta(USER, meta.id, share_excluded=False)
    _touch(USER, meta.id, updated=time.time() - LONG_AGO)

    assert await sweep.sweep() == 1


# ── Growth, and the upsert ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unchanged_conversation_is_not_re_sent(install):
    """The rate limit is finite; re-posting an unchanged transcript spends it on
    nothing."""
    meta = _conversation()
    _opt_in()
    assert await sweep.sweep() == 1

    _touch(USER, meta.id, updated=time.time() - LONG_AGO)
    assert await sweep.sweep() == 0
    assert len(outbox.pending()) == 1


@pytest.mark.asyncio
async def test_a_conversation_that_grows_is_re_sent_as_the_next_revision(install):
    """Acceptance criterion: growth is an upsert on the same row, not a second
    one."""
    meta = _conversation()
    _opt_in()
    await sweep.sweep()
    first = outbox.pending()[0]["body"]

    conversations.append_turn(USER, meta.id, TurnEntry(role="user", text="and then?"))
    _touch(USER, meta.id, updated=time.time() - LONG_AGO)
    assert await sweep.sweep() == 1

    second = outbox.pending()[1]["body"]
    assert second["share_id"] == first["share_id"]
    assert second["revision"] == first["revision"] + 1


@pytest.mark.asyncio
async def test_a_passive_share_is_the_same_build_as_an_explicit_one(install):
    """The sweep must not have a scrubbing path of its own: what it sends is what
    the dialog would have shown, and ``kind`` is the only difference."""
    meta = _conversation()
    _opt_in()
    preview = share.preview(USER, meta.id)
    await sweep.sweep()

    body = outbox.pending()[0]["body"]
    assert body["kind"] == wire.KIND_PASSIVE
    assert body["turns"] == [t.model_dump(mode="json") for t in preview.turns]
    assert body["redaction"]["counts"] == preview.counts


# ── The rate limit ───────────────────────────────────────────────────────


def test_the_tick_budget_stays_inside_the_collectors_allowance():
    """Acceptance criterion. Asserted on the pair rather than on ``PER_TICK``
    alone: the constant that matters is shares *per hour*, and 5 per tick every
    15 minutes would be 20/h against a 12/h bucket."""
    ticks_per_hour = 3600 / sweep.SWEEP_INTERVAL_S
    assert sweep.PER_TICK * ticks_per_hour <= sweep.COLLECTOR_HOURLY_ALLOWANCE


@pytest.mark.asyncio
async def test_a_backlog_drains_a_tick_at_a_time_and_loses_nothing(install):
    """Acceptance criterion: 20 ready conversations drain ``PER_TICK`` per tick
    and none are lost. Oldest first, so nothing at the back starves."""
    import condor.sharing.outbox as outbox_module

    # The queue's own cap would otherwise drop the early shares as the later
    # ticks append; this test is about the sweep's pacing, not that cap.
    original = outbox_module.MAX_QUEUED_SHARES
    outbox_module.MAX_QUEUED_SHARES = 100
    try:
        metas = [_conversation(age_s=LONG_AGO + i * 60) for i in range(20)]
        _opt_in()

        seen: list[str] = []
        for _ in range(20):
            queued = await sweep.sweep()
            if not queued:
                break
            assert queued <= sweep.PER_TICK
            seen.extend(r["share_id"] for r in outbox.pending()[len(seen) :])

        shared = {conversations.get_conversation(USER, m.id).share_id for m in metas}
        assert len(seen) == 20
        assert set(seen) == shared
    finally:
        outbox_module.MAX_QUEUED_SHARES = original


@pytest.mark.asyncio
async def test_the_oldest_waiting_conversation_goes_first(install):
    metas = [
        _conversation(age_s=LONG_AGO + i * 3600) for i in range(sweep.PER_TICK + 2)
    ]
    _opt_in()
    await sweep.sweep()

    sent = [r["share_id"] for r in outbox.pending()]
    oldest = sorted(metas, key=lambda m: m.updated_at)[: sweep.PER_TICK]
    assert sent == [conversations.get_conversation(USER, m.id).share_id for m in oldest]


@pytest.mark.asyncio
async def test_one_users_backlog_does_not_starve_another(install):
    """The budget is per install, so it is spent by age across every user rather
    than user by user."""
    for i in range(10):
        _conversation(USER, age_s=LONG_AGO + i)
    _conversation(OTHER, age_s=LONG_AGO + 10_000)  # the oldest on the box
    _opt_in(USER)
    _opt_in(OTHER)

    await sweep.sweep()
    assert outbox.pending()[0]["user_id"] == str(OTHER)


# ── It never raises into the job queue ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_share_does_not_take_the_tick_down(install, monkeypatch):
    """Acceptance criterion: the sweep never raises into the job queue."""
    _conversation()
    _conversation()
    _opt_in()

    calls = {"n": 0}
    real = share.submit

    def _explode(user_id, conv_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk on fire")
        return real(user_id, conv_id, **kwargs)

    monkeypatch.setattr(share, "submit", _explode)
    assert await sweep.sweep() == 1


@pytest.mark.asyncio
async def test_an_unreadable_store_is_skipped_not_fatal(install, monkeypatch):
    _conversation(OTHER)
    _opt_in(USER)
    _opt_in(OTHER)

    def _explode(user_id, now=None):
        if str(user_id) == str(USER):
            raise OSError("no such directory")
        return []

    monkeypatch.setattr(sweep, "eligible", _explode)
    assert await sweep.sweep() == 0


# ── Withdrawal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turning_it_off_destroys_what_was_queued_but_never_sent(install):
    """Acceptance criterion: switching to Off leaves no passive share behind.

    ``_purge_collected``'s rule — a withdrawal destroys what was collected, so a
    later bug has nothing left to deliver."""
    _conversation()
    _opt_in()
    await sweep.sweep()
    assert len(outbox.pending()) == 1

    sweep.withdraw(USER)
    assert consent.user_state(USER) == consent.OFF
    assert outbox.pending() == []


@pytest.mark.asyncio
async def test_a_withdrawal_does_not_strand_somebody_elses_revocation(install):
    """The queue is the install's, not one user's. Emptying it wholesale would
    silently un-revoke an unshare that is already owed to the collector."""
    mine = _conversation(USER)
    theirs = _conversation(OTHER)
    _opt_in(USER)
    await sweep.sweep()

    share.submit(OTHER, theirs.id)
    share.unshare(OTHER, theirs.id)
    assert len(outbox.pending()) == 3

    sweep.withdraw(USER)

    remaining = outbox.pending()
    assert [r["op"] for r in remaining] == [outbox.OP_SHARE, outbox.OP_UNSHARE]
    assert all(r["user_id"] == str(OTHER) for r in remaining)
    assert conversations.get_conversation(USER, mine.id).share_id  # receipt stands


@pytest.mark.asyncio
async def test_a_withdrawal_keeps_the_users_own_deliberate_share(install):
    """They pressed the button on this one with the transcript in front of them.
    Turning off the automatic path is not a retraction of that."""
    deliberate = _conversation(USER)
    _opt_in(USER)
    share.submit(USER, deliberate.id)

    sweep.withdraw(USER)

    assert [r["kind"] for r in outbox.pending()] == [wire.KIND_EXPLICIT]


def test_the_back_catalogue_is_a_separate_button(install):
    """Acceptance criterion: "delete everything I've shared" removes every row
    this install created — and it is not what turning Always off does."""
    first = _conversation(USER)
    second = _conversation(USER)
    _opt_in(USER)
    share.submit(USER, first.id)
    share.submit(USER, second.id)
    assert len(share.list_shares(USER)) == 2

    sweep.withdraw(USER)
    assert len(share.list_shares(USER)) == 2  # withdrawal alone touches nothing

    assert share.unshare_all(USER) == 2
    assert share.list_shares(USER) == []
    assert [r["op"] for r in outbox.pending()[-2:]] == [
        outbox.OP_UNSHARE,
        outbox.OP_UNSHARE,
    ]


def test_unsharing_everything_only_reaches_the_callers_own(install):
    mine = _conversation(USER)
    theirs = _conversation(OTHER)
    share.submit(USER, mine.id)
    share.submit(OTHER, theirs.id)

    assert share.unshare_all(USER) == 1
    assert len(share.list_shares(OTHER)) == 1


# ── Consent bookkeeping ──────────────────────────────────────────────────


def test_choosing_always_records_when(install):
    before = time.time()
    consent.set_user_state(USER, consent.ALWAYS)
    assert before <= consent.opted_in_at(USER) <= time.time()


def test_re_choosing_always_does_not_move_the_timestamp(install):
    """Consent has been continuous; moving it forward would silently make the
    conversations in between ineligible."""
    consent.set_user_state(USER, consent.ALWAYS)
    first = consent.opted_in_at(USER)
    consent.set_user_state(USER, consent.ALWAYS)
    assert consent.opted_in_at(USER) == first


def test_leaving_and_returning_starts_a_fresh_window(install):
    """A gap in consent is a gap in the corpus, not something to paper over."""
    consent.set_user_state(USER, consent.ALWAYS)
    first = consent.opted_in_at(USER)
    consent.set_user_state(USER, consent.OFF)
    assert consent.opted_in_at(USER) == 0.0

    consent.set_user_state(USER, consent.ALWAYS)
    assert consent.opted_in_at(USER) >= first


def test_a_state_written_by_the_previous_build_still_reads(install):
    """FEAT-054 wrote a bare string per user. It must keep meaning what it meant."""
    consent._update(users={str(USER): consent.EXPLICIT})
    assert consent.user_state(USER) == consent.EXPLICIT
    assert not consent.can_sweep(USER)
    assert consent.opted_in_at(USER) == 0.0
