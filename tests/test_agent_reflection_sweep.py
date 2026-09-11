"""The idle sweep behind the reflection pass (FEAT-073).

The pass spends the agent's own model, unattended, on conversations nobody
asked it to read. So these tests are almost entirely about what the sweep
refuses: a chat still in use, a one-line greeting, one it has already read, and
an install whose operator turned the whole thing off. Each rule gets a
conversation that fails only that rule, so a regression in one cannot hide
behind another.

Nothing here reaches a model — :func:`condor.agents.reflection.reflect` is
stubbed, because what is under test is which conversations reach it and in
what order.
"""

from __future__ import annotations

import time

import pytest

from condor.agents import reflection
from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry

USER = 4242
OTHER = 5353

# Comfortably past IDLE_S, so a conversation stamped this far back is finished
# by any reading of the rule.
LONG_AGO = reflection.IDLE_S + 600


def _touch(user_id: int, conv_id: str, *, updated: float) -> None:
    """Backdate a conversation on disk.

    ``write_status`` stamps ``updated_at`` itself on every merge, so it is
    written directly here — the whole point is to control the clock the sweep
    reads, and going through ``update_meta`` would overwrite it with now.
    """
    from condor import paths
    from condor.fsutil import atomic_write_json
    from condor.runtime.conversations import META_FILENAME
    from condor.runtime.registry_file import read_status, status_path

    conv_dir = paths.conversation_dir(user_id, conv_id)
    data = read_status(conv_dir, META_FILENAME) or {}
    data["updated_at"] = updated
    atomic_write_json(status_path(conv_dir, META_FILENAME), data, indent=2)


def _conversation(user_id: int = USER, *, turns: int = 2, age_s: float = LONG_AGO):
    meta = conversations.new_conversation(user_id, surface="web")
    for i in range(turns):
        conversations.append_turn(
            user_id,
            meta.id,
            TurnEntry(role="user" if i % 2 == 0 else "assistant", text=f"turn {i}"),
        )
    _touch(user_id, meta.id, updated=time.time() - age_s)
    return conversations.get_conversation(user_id, meta.id)


@pytest.fixture
def ran(monkeypatch):
    """Collect what the sweep handed to the pass, without running it."""
    seen: list[tuple[int, str]] = []

    async def _reflect(user_id, meta):
        seen.append((user_id, meta.id))
        return True

    monkeypatch.setattr(reflection, "reflect", _reflect)
    monkeypatch.delenv(reflection.ENV_VAR, raising=False)
    return seen


# ── Eligibility, one rule at a time ──


def test_a_finished_conversation_is_eligible(ran):
    meta = _conversation()

    assert [m.id for m in reflection.eligible(USER)] == [meta.id]


def test_a_conversation_still_in_use_is_not(ran):
    _conversation(age_s=60)

    assert reflection.eligible(USER) == []


def test_a_single_turn_greeting_is_not(ran):
    _conversation(turns=1)

    assert reflection.eligible(USER) == []


def test_one_already_read_is_not(ran):
    meta = _conversation()
    conversations.update_meta(
        USER, meta.id, reflected_at="2026-08-27T12:00:00+00:00", reflected_ok=True
    )

    assert reflection.eligible(USER) == []


def test_a_failed_reflection_is_not_retried(ran):
    """``reflected_ok: false`` is still read — the marker is the attempt."""
    meta = _conversation()
    conversations.update_meta(
        USER, meta.id, reflected_at="2026-08-27T12:00:00+00:00", reflected_ok=False
    )

    assert reflection.eligible(USER) == []


def test_the_env_switch_empties_the_candidate_list(ran, monkeypatch):
    _conversation()
    monkeypatch.setenv(reflection.ENV_VAR, "off")

    assert reflection.enabled() is False
    assert reflection.eligible(USER) == []


def test_eligible_returns_oldest_first(ran):
    newer = _conversation(age_s=LONG_AGO)
    older = _conversation(age_s=LONG_AGO * 4)

    assert [m.id for m in reflection.eligible(USER)] == [older.id, newer.id]


# ── The tick ──


@pytest.mark.asyncio
async def test_the_sweep_reflects_what_is_finished(ran):
    meta = _conversation()

    assert await reflection.sweep() == 1
    assert ran == [(USER, meta.id)]


@pytest.mark.asyncio
async def test_the_sweep_is_a_no_op_when_switched_off(ran, monkeypatch):
    _conversation()
    monkeypatch.setenv(reflection.ENV_VAR, "off")

    assert await reflection.sweep() == 0
    assert ran == []


@pytest.mark.asyncio
async def test_an_install_with_nothing_finished_costs_nothing(ran):
    _conversation(age_s=60)

    assert await reflection.sweep() == 0


@pytest.mark.asyncio
async def test_the_tick_spends_its_budget_and_no_more(ran):
    for _ in range(reflection.PER_TICK + 2):
        _conversation()

    assert await reflection.sweep() == reflection.PER_TICK
    assert len(ran) == reflection.PER_TICK


@pytest.mark.asyncio
async def test_the_oldest_waiting_conversation_goes_first_whoever_owns_it(ran):
    mine = _conversation(USER, age_s=LONG_AGO * 5)
    theirs = _conversation(OTHER, age_s=LONG_AGO * 9)
    _conversation(USER, age_s=LONG_AGO * 2)

    await reflection.sweep()

    assert ran[:2] == [(OTHER, theirs.id), (USER, mine.id)]


@pytest.mark.asyncio
async def test_one_bad_conversation_is_not_the_tick(ran, monkeypatch):
    good = _conversation(age_s=LONG_AGO)
    bad = _conversation(age_s=LONG_AGO * 3)

    async def _reflect(user_id, meta):
        if meta.id == bad.id:
            raise RuntimeError("nope")
        ran.append((user_id, meta.id))
        return True

    monkeypatch.setattr(reflection, "reflect", _reflect)

    assert await reflection.sweep() == 1
    assert ran == [(USER, good.id)]


# ── Registration ──


def test_register_jobs_schedules_one_repeating_job():
    class Queue:
        def __init__(self):
            self.scheduled = []

        def get_jobs_by_name(self, name):
            return []

        def run_repeating(self, callback, interval, first, name):
            self.scheduled.append((interval, first, name))

    class App:
        job_queue = Queue()

    app = App()
    reflection.register_jobs(app)

    assert app.job_queue.scheduled == [
        (reflection.SWEEP_INTERVAL_S, 300, reflection.REFLECTION_JOB)
    ]


def test_register_jobs_survives_an_application_without_a_queue():
    class App:
        job_queue = None

    reflection.register_jobs(App())  # must not raise
