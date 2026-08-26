"""What a consult leaves behind (FEAT-058).

CONSULT is the channel every other agent, the bot and the dashboard actually
use, and until this feature it wrote nothing anywhere: an agent could answer a
hundred questions and its page would show an empty history. These tests pin the
ledger that fixes that, and — just as importantly — pin what recording must
never cost: a consult that fails still raises, a consult that is cancelled still
cancels, and a broken record write does not become a broken consult.

The other half is the store the record joins. Consults are the plentiful kind
and delegations the expensive one, in one directory, so retention is per kind:
an afternoon of consults must not evict a single background task's transcript.
"""

import asyncio

import pytest

from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegation_history import list_history, read_history
from condor.agents.run_records import KIND_CONSULT, KIND_DELEGATE


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


def _consult(monkeypatch, *, answer="the funding is 3bps", boom=None, **kw):
    """Run one consult through the real ``run_consult``, with a stubbed engine."""

    async def fake_engine(**_kw):
        if boom is not None:
            raise boom
        return answer

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_engine)
    monkeypatch.setattr(
        consult_module, "_build_consult_permission_cb", lambda *a, **k: None
    )

    async def scenario():
        return await consult_module.run_consult(
            slug=kw.pop("slug", "scout"),
            user_id=kw.pop("user_id", 7),
            chat_id=kw.pop("chat_id", 42),
            server_name=kw.pop("server_name", "local"),
            task=kw.pop("task", "what is the funding on HYPE right now"),
            **kw,
        )

    return asyncio.run(scenario())


def _only_record(user_id=7):
    records = list_history(user_id=user_id, limit=100)
    assert len(records) == 1, records
    return records[0]


# ── The record ─────────────────────────────────────────────────────────────


def test_a_consult_is_recorded_with_its_ask_caller_and_outcome(monkeypatch):
    answer = _consult(monkeypatch, caller="condor")

    assert answer == "the funding is 3bps"
    record = _only_record()
    assert record["kind"] == KIND_CONSULT
    assert record["agent"] == "scout"
    assert record["caller"] == "condor"
    assert record["task"] == "what is the funding on HYPE right now"
    assert record["status"] == "done"
    assert record["result"] == "the funding is 3bps"
    # A duration, not a guess: both ends are stamped by the two writes.
    assert record["started_at"] > 0
    assert record["ended_at"] >= record["started_at"]


def test_a_consult_the_dashboard_asked_for_names_no_caller(monkeypatch):
    """ "" means "a person asked", and is never dressed up as an agent."""
    _consult(monkeypatch)
    assert _only_record()["caller"] == ""


def test_a_failing_consult_records_the_error_and_still_raises(monkeypatch):
    with pytest.raises(RuntimeError, match="backend on fire"):
        _consult(monkeypatch, boom=RuntimeError("backend on fire"))

    record = _only_record()
    assert record["status"] == "error"
    assert record["error"] == "backend on fire"
    # Recording an error must not swallow it -- the raise above is the assertion.


def test_a_cancelled_consult_records_stopped_and_still_cancels(monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        _consult(monkeypatch, boom=asyncio.CancelledError())

    assert _only_record()["status"] == "stopped"


def test_a_consult_the_process_died_during_reads_interrupted(monkeypatch):
    """No reconciler: the honest label is derived from the boot id on the file."""
    from condor.runtime import registry_file

    # The start write lands under this boot...
    async def never_returns(**_kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", never_returns)
    monkeypatch.setattr(
        consult_module, "_build_consult_permission_cb", lambda *a, **k: None
    )

    async def scenario():
        task = asyncio.ensure_future(
            consult_module.run_consult(
                slug="scout",
                user_id=7,
                chat_id=42,
                server_name=None,
                task="summarise the LP ranges that went out of range",
            )
        )
        await asyncio.sleep(0)  # let the start record land
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    # ...and while this process lives, a running record is simply running.
    from condor import paths
    from condor.runtime.registry_file import write_status

    task_id = _only_record()["task_id"]
    write_status(paths.delegation_dir(7, task_id), state="running")
    assert read_history(7, task_id)["status"] == "running"

    # A different boot means the process that wrote it is gone.
    monkeypatch.setattr(registry_file, "BOOT_ID", "a-later-boot")
    assert read_history(7, task_id)["status"] == "interrupted"


def test_a_long_answer_is_clipped_in_the_record_but_not_in_the_reply(monkeypatch):
    huge = "x" * (consult_module.MAX_RECORDED_RESULT + 500)
    answer = _consult(monkeypatch, answer=huge)

    assert answer == huge  # the caller gets the whole thing
    recorded = _only_record()["result"]
    assert recorded == "x" * consult_module.MAX_RECORDED_RESULT + "\n… (truncated)"


def test_a_broken_record_write_does_not_break_the_consult(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("condor.runtime.registry_file.write_status", boom)

    assert _consult(monkeypatch) == "the funding is 3bps"
    assert list_history(user_id=7, limit=100) == []


# ── Retention, per kind ────────────────────────────────────────────────────


def _write_record(user_id, task_id, *, kind, state="done", started_at=0.0):
    from condor import paths
    from condor.runtime.registry_file import write_status

    record_dir = paths.delegation_dir(user_id, task_id)
    record_dir.mkdir(parents=True, exist_ok=True)
    write_status(
        record_dir,
        state=state,
        task_id=task_id,
        agent_slug="scout",
        user_id=user_id,
        kind=kind,
        started_at=started_at,
        task=f"task {task_id}",
    )


def _ids(user_id=7):
    from condor import paths

    return sorted(p.name for p in paths.delegations_dir(user_id).iterdir())


def test_a_flood_of_consults_evicts_no_delegation_record(monkeypatch):
    """The whole reason the cap is per kind, at the numbers that would bite."""
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 50)

    _write_record(7, "scout-delegate-precious", kind=KIND_DELEGATE, started_at=1.0)
    for i in range(400):
        _write_record(
            7, f"scout-consult-{i:03d}", kind=KIND_CONSULT, started_at=10.0 + i
        )

    evicted = delegate_module.prune_delegation_records(7, kind=KIND_CONSULT)

    assert evicted == 350
    # The delegation is older than every one of them and survives untouched.
    assert "scout-delegate-precious" in _ids()
    assert len([i for i in _ids() if "-consult-" in i]) == 50


def test_each_kind_is_capped_on_its_own_when_sweeping_everything(monkeypatch):
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 2)
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 3)

    # started_at ascending from 1.0: a literal 0.0 falls through the reader's own
    # fallback chain to the file's ``updated_at``, which would make the oldest
    # record sort newest -- a property of the store, not of this test.
    for i in range(5):
        _write_record(
            7, f"scout-delegate-{i}", kind=KIND_DELEGATE, started_at=float(i + 1)
        )
        _write_record(
            7, f"scout-consult-{i}", kind=KIND_CONSULT, started_at=float(i + 1)
        )

    assert delegate_module.prune_delegation_records(7) == 5  # 3 + 2
    assert _ids() == [
        "scout-consult-2",
        "scout-consult-3",
        "scout-consult-4",
        "scout-delegate-3",
        "scout-delegate-4",
    ]


def test_a_record_written_before_kinds_existed_counts_as_a_delegation(monkeypatch):
    """No ``kind`` on disk is not unknown: only delegations ever wrote one."""
    from condor import paths
    from condor.runtime.registry_file import write_status

    record_dir = paths.delegation_dir(7, "scout-delegate-old")
    record_dir.mkdir(parents=True, exist_ok=True)
    write_status(
        record_dir,
        state="done",
        task_id="scout-delegate-old",
        agent_slug="scout",
        user_id=7,
        started_at=1.0,
        task="from before FEAT-058",
        result="done",
        error="",
        tool_count=3,
    )

    assert read_history(7, "scout-delegate-old")["kind"] == KIND_DELEGATE
    assert [r["task_id"] for r in list_history(user_id=7, kind=KIND_DELEGATE)] == [
        "scout-delegate-old"
    ]
    assert list_history(user_id=7, kind=KIND_CONSULT) == []


def test_finishing_a_consult_sweeps_only_its_own_kind(monkeypatch):
    """End to end: the sweep a real consult triggers leaves delegations alone."""
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 1)

    _write_record(7, "scout-delegate-precious", kind=KIND_DELEGATE, started_at=1.0)
    _write_record(7, "scout-consult-old", kind=KIND_CONSULT, started_at=2.0)

    _consult(monkeypatch)

    ids = _ids()
    assert "scout-delegate-precious" in ids
    assert "scout-consult-old" not in ids


# ── The route: one door, one kind filter ───────────────────────────────────


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return user_id == 99


@pytest.fixture
def _routes(monkeypatch):
    """The history route over a real store, with a known admin id."""
    import config_manager
    from condor.web.models import WebUser
    from condor.web.routes.agents import list_delegation_history

    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)

    def ask(user_id, **kw):
        payload = asyncio.run(
            list_delegation_history(
                user=WebUser(id=user_id, role="admin" if user_id == 99 else "user"),
                **kw,
            )
        )
        return {r["task_id"] for r in payload["delegations"]}

    return ask


def test_the_route_answers_with_every_kind_or_with_one(_routes):
    _write_record(7, "scout-delegate-a", kind=KIND_DELEGATE, started_at=1.0)
    _write_record(7, "scout-consult-b", kind=KIND_CONSULT, started_at=2.0)

    assert _routes(7) == {"scout-delegate-a", "scout-consult-b"}
    # What the chat dock asks for: background tasks, and no consult among them.
    assert _routes(7, kind=KIND_DELEGATE) == {"scout-delegate-a"}
    assert _routes(7, kind=KIND_CONSULT) == {"scout-consult-b"}


def test_the_route_shows_a_consult_to_its_owner_and_to_nobody_else(_routes):
    _write_record(7, "scout-consult-mine", kind=KIND_CONSULT, started_at=1.0)
    _write_record(8, "scout-consult-theirs", kind=KIND_CONSULT, started_at=2.0)

    assert _routes(7, kind=KIND_CONSULT) == {"scout-consult-mine"}
    assert _routes(8, kind=KIND_CONSULT) == {"scout-consult-theirs"}
    # Admin scope is unchanged by this feature: it was already the one that sees
    # every owner, and a consult record carries the same user_id a delegation does.
    assert _routes(99, kind=KIND_CONSULT) == {
        "scout-consult-mine",
        "scout-consult-theirs",
    }


def test_a_live_delegation_is_excluded_when_the_caller_asks_for_consults(_routes):
    """Everything in the registry is a delegation, so the filter must drop it."""
    delegate_module._delegations["scout-delegate-live"] = delegate_module.DelegateTask(
        task_id="scout-delegate-live",
        agent_slug="scout",
        user_id=7,
        chat_id=42,
        server_name=None,
        task="still going",
    )
    _write_record(7, "scout-consult-b", kind=KIND_CONSULT, started_at=2.0)

    assert _routes(7) == {"scout-delegate-live", "scout-consult-b"}
    assert _routes(7, kind=KIND_CONSULT) == {"scout-consult-b"}
