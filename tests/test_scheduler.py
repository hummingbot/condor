"""Scheduler execution (§5.4 / §11 Phase 4): fire-key dedup on
scheduled_for, overlap skip while the prior run is active or holds leases,
manual runs never suppress the next fire, durable routine schedules with
write-ahead last_fired, dangling-target disabling, and cron evaluation."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from condor.agents.cron import fires_between, next_fire
from condor.agents.runstore import RunStore, set_run_store
from condor.agents.scheduler import (
    RoutineScheduleStore,
    Scheduler,
    create_routine_schedule,
)


# ── cron evaluation ──


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_next_fire_basic():
    assert next_fire("*/15 * * * *", "UTC", _dt("2026-07-15T10:07:00")) == _dt(
        "2026-07-15T10:15:00"
    )
    assert next_fire("0 9 * * *", "UTC", _dt("2026-07-15T10:00:00")) == _dt(
        "2026-07-16T09:00:00"
    )
    # dow: 0 and 7 are Sunday; 2026-07-15 is a Wednesday (cron dow 3).
    assert next_fire("0 0 * * 0", "UTC", _dt("2026-07-15T00:00:00")) == _dt(
        "2026-07-19T00:00:00"
    )


def test_next_fire_dom_dow_or_rule():
    # Both restricted → OR: fires on the 20th AND on Fridays.
    got = next_fire("0 0 20 * 5", "UTC", _dt("2026-07-15T00:00:00"))
    assert got == _dt("2026-07-17T00:00:00")  # Friday before the 20th


def test_next_fire_timezone():
    got = next_fire("30 9 * * *", "America/New_York", _dt("2026-07-15T12:00:00"))
    assert got.astimezone(timezone.utc) == _dt("2026-07-15T13:30:00")


def test_fires_between():
    fires = fires_between(
        "*/20 * * * *", "UTC", _dt("2026-07-15T10:00:00"), _dt("2026-07-15T11:00:00")
    )
    assert [f.minute for f in fires] == [20, 40, 0]
    assert fires[-1].hour == 11


# ── fixtures ──


class _FakeAgent:
    def __init__(self, slug, schedule):
        self.slug = slug
        self.schedule = schedule


class _FakeService:
    """Duck-typed AgentService: list() + run() capture."""

    def __init__(self, agents, run_store):
        self._agents = agents
        self._store = run_store
        self.launches = []

    def list(self):
        return self._agents

    async def run(self, slug, config=None, kind="", scheduled_for=""):
        self.launches.append((slug, kind, scheduled_for))
        run_id = self._store.start_run(
            slug, kind or "session", payload={"scheduled_for": scheduled_for}
        )
        self._store.end_run(run_id, "completed")
        return {"started": True, "agent_id": run_id}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = RunStore(root=tmp_path / "agents")
    set_run_store(store)
    sched_store = RoutineScheduleStore(path=tmp_path / "store" / "schedules.json")
    return store, sched_store, tmp_path


def _make_scheduler(monkeypatch, store, sched_store, agents, runner=None):
    svc = _FakeService(agents, store)
    import condor.agents.scheduler as sched_mod
    import condor.agents.service as service_mod

    monkeypatch.setattr(service_mod, "AgentService", lambda: svc)
    ran = []

    async def _runner(entry):
        ran.append(entry["routine"])

    s = Scheduler(routine_store=sched_store, routine_runner=runner or _runner)
    return s, svc, ran


# ── agent fires ──


def test_due_fire_launches_with_fire_key(env, monkeypatch):
    store, sched_store, _ = env
    agent = _FakeAgent("alpha", {"cron": "*/5 * * * *", "tz": "UTC"})
    s, svc, _ = _make_scheduler(monkeypatch, store, sched_store, [agent])

    t0 = _dt("2026-07-15T10:04:30")
    t1 = _dt("2026-07-15T10:05:10")
    s._last_poll = t0
    asyncio.run(s.poll_once(now=t1))
    assert svc.launches == [
        ("alpha", "scheduled", "2026-07-15T10:05:00+00:00")
    ]

    # Second poll over the SAME window: fire key already in RunStore → dedup.
    s._last_poll = t0
    asyncio.run(s.poll_once(now=t1))
    assert len(svc.launches) == 1


def test_restart_across_due_time_launches_at_most_once(env, monkeypatch):
    """§11: restart across a due time — single launch keyed on
    scheduled_for, no backfill."""
    store, sched_store, _ = env
    agent = _FakeAgent("alpha", {"cron": "0 * * * *", "tz": "UTC"})
    s, svc, _ = _make_scheduler(monkeypatch, store, sched_store, [agent])

    t0 = _dt("2026-07-15T10:59:30")
    t1 = _dt("2026-07-15T11:00:20")
    s._last_poll = t0
    asyncio.run(s.poll_once(now=t1))
    assert len(svc.launches) == 1

    # "Restart": a NEW scheduler instance over the same RunStore. Its poll
    # window covers the same fire → RunStore dedup prevents a second launch.
    s2, svc2, _ = _make_scheduler(monkeypatch, store, sched_store, [agent])
    s2._last_poll = t0
    asyncio.run(s2.poll_once(now=t1))
    assert svc2.launches == []


def test_old_fire_skipped_never_backfilled(env, monkeypatch):
    store, sched_store, _ = env
    agent = _FakeAgent("alpha", {"cron": "0 * * * *", "tz": "UTC"})
    s, svc, _ = _make_scheduler(monkeypatch, store, sched_store, [agent])

    # The fire came due 30 minutes ago (e.g. process was down) — skip.
    s._last_poll = _dt("2026-07-15T10:59:00")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T11:30:00")))
    assert svc.launches == []


def test_overlap_skipped_while_prior_run_active(env, monkeypatch):
    store, sched_store, _ = env
    agent = _FakeAgent("alpha", {"cron": "*/5 * * * *", "tz": "UTC"})
    s, svc, _ = _make_scheduler(monkeypatch, store, sched_store, [agent])
    monkeypatch.setattr(s, "_overlaps", lambda slug: True)

    s._last_poll = _dt("2026-07-15T10:04:30")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T10:05:10")))
    assert svc.launches == []


def test_manual_run_between_fires_does_not_suppress(env, monkeypatch):
    """§11: dedup is on the fire key, never 'last run_started per agent'."""
    store, sched_store, _ = env
    agent = _FakeAgent("alpha", {"cron": "*/5 * * * *", "tz": "UTC"})
    s, svc, _ = _make_scheduler(monkeypatch, store, sched_store, [agent])

    # A manual (non-scheduled) run lands between fires.
    manual = store.start_run("alpha", "session")
    store.end_run(manual, "stopped")

    s._last_poll = _dt("2026-07-15T10:04:30")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T10:05:10")))
    assert len(svc.launches) == 1  # the fire still happens


# ── routine schedules ──


def test_routine_schedule_durable_and_write_ahead(env, monkeypatch):
    store, sched_store, tmp_path = env
    created = create_routine_schedule(
        routine="market_scanner", cron="*/10 * * * *", tz="UTC", store=sched_store
    )
    sid = created["schedule_id"]
    # Durable on disk with the sealed posture.
    path = tmp_path / "store" / "schedules.json"
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert json.loads(path.read_text())[sid]["routine"] == "market_scanner"

    fired_before_spawn = []

    async def runner(entry):
        # WRITE-AHEAD assertion: last_fired already advanced when the
        # worker spawns.
        fired_before_spawn.append(sched_store.load()[sid]["last_fired"])

    s, svc, _ = _make_scheduler(
        monkeypatch, store, sched_store, [], runner=runner
    )
    s._last_poll = _dt("2026-07-15T10:09:30")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T10:10:10")))
    assert fired_before_spawn == ["2026-07-15T10:10:00+00:00"]

    # "Restart": a new store instance reads the same file — schedule survives.
    reloaded = RoutineScheduleStore(path=path).load()
    assert reloaded[sid]["last_fired"] == "2026-07-15T10:10:00+00:00"

    # Same fire not repeated (last_fired == fire key).
    s._last_poll = _dt("2026-07-15T10:09:30")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T10:10:10")))
    assert len(fired_before_spawn) == 1


def test_dangling_routine_disables_schedule(env, monkeypatch):
    store, sched_store, _ = env
    created = create_routine_schedule(
        routine="ghost", cron="* * * * *", tz="UTC", store=sched_store
    )
    sid = created["schedule_id"]

    s, svc, ran = _make_scheduler(monkeypatch, store, sched_store, [])
    monkeypatch.setattr(
        s, "_routine_disabled_reason", lambda entry: "routine 'ghost' no longer exists"
    )
    s._last_poll = _dt("2026-07-15T10:09:30")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T10:10:30")))
    assert ran == []
    entry = sched_store.load()[sid]
    assert entry["disabled"] is True
    assert "no longer exists" in entry["disabled_reason"]

    # Disabled schedules never fire again.
    s._last_poll = _dt("2026-07-15T10:10:30")
    asyncio.run(s.poll_once(now=_dt("2026-07-15T10:11:30")))
    assert ran == []


def test_bad_cron_rejected_at_creation(env):
    _, sched_store, _ = env
    with pytest.raises(Exception):
        create_routine_schedule(
            routine="x", cron="not a cron", store=sched_store
        )
    with pytest.raises(Exception):
        create_routine_schedule(
            routine="x", cron="0 * * * *", tz="Mars/Olympus", store=sched_store
        )
