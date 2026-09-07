"""The run-record store: per-kind retention and the history route (FEAT-058).

Every agent run leaves a record in one directory per owner, discriminated by a
``kind``. Today only ``delegate`` writes one. ``consult`` -- the synchronous
channel that has since been removed -- wrote there too, and an install that used
it still has that history on disk, so these tests pin both halves: a consult
record stays readable, listable and prunable, and the per-kind caps still hold.

Retention is per kind and always was, for a reason that outlived the channel:
consults were the plentiful kind and delegations the expensive one, in one
directory, so an afternoon of consults must not evict a single background task's
transcript. That is now a statement about a fixed backlog rather than a growing
one, and it is still the only thing keeping the two budgets apart.
"""

import asyncio

import pytest

from condor.agents import delegate as delegate_module
from condor.agents.delegation_history import list_history, read_history
from condor.agents.run_records import KIND_CONSULT, KIND_DELEGATE, record_run


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


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


# ── Reading a record back ──────────────────────────────────────────────────


def test_an_archived_consult_reads_back_whole(monkeypatch):
    """The channel is gone; the records it wrote are still first-class rows."""
    _write_record(7, "scout-consult-abc", kind=KIND_CONSULT, started_at=1.0)

    record = read_history(7, "scout-consult-abc")
    assert record["kind"] == KIND_CONSULT
    assert record["agent"] == "scout"
    assert record["status"] == "done"
    assert [r["task_id"] for r in list_history(user_id=7, kind=KIND_CONSULT)] == [
        "scout-consult-abc"
    ]


def test_a_run_the_process_died_during_reads_interrupted(monkeypatch):
    """No reconciler: the honest label is derived from the boot id on the file."""
    from condor.runtime import registry_file

    # While the process that wrote it lives, a running record is simply running.
    _write_record(7, "scout-delegate-live", kind=KIND_DELEGATE, state="running")
    assert read_history(7, "scout-delegate-live")["status"] == "running"

    # A different boot means the process that wrote it is gone.
    monkeypatch.setattr(registry_file, "BOOT_ID", "a-later-boot")
    assert read_history(7, "scout-delegate-live")["status"] == "interrupted"


# ── Retention, per kind ────────────────────────────────────────────────────


def test_a_backlog_of_consults_evicts_no_delegation_record(monkeypatch):
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


def test_finishing_a_delegation_sweeps_only_its_own_kind(monkeypatch):
    """End to end: the sweep a finishing run triggers leaves the other kind alone."""
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 1)

    _write_record(7, "scout-consult-precious", kind=KIND_CONSULT, started_at=1.0)
    _write_record(7, "scout-delegate-old", kind=KIND_DELEGATE, started_at=2.0)

    record_run(
        state="done",
        user_id=7,
        run_id="scout-delegate-new",
        agent_slug="scout",
        kind=KIND_DELEGATE,
        task="the newest one",
        started_at=3.0,
        result="ok",
    )

    ids = _ids()
    assert "scout-consult-precious" in ids
    assert "scout-delegate-old" not in ids
    assert "scout-delegate-new" in ids


# ── The route: one door, one kind filter ───────────────────────────────────


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return user_id == 99

    def get_user_preference(self, user_id: int, key: str, default=None):
        # ``_may_run_code`` asks for the code_run grant when the caller wants
        # every kind. Nobody here has it, which is the interesting case: the
        # merge must still answer with the record kinds it does own.
        return default


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


def test_the_route_shows_a_record_to_its_owner_and_to_nobody_else(_routes):
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


def test_an_archived_consult_row_opens_into_the_same_sheet(monkeypatch):
    """The sheet fetches the record it was handed a summary of, kind regardless."""
    import config_manager
    from condor.web.models import WebUser
    from condor.web.routes.agents import get_delegation_status

    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    _write_record(7, "scout-consult-abc", kind=KIND_CONSULT, started_at=1.0)

    record = asyncio.run(
        get_delegation_status("scout-consult-abc", user=WebUser(id=7, role="user"))
    )
    assert record["kind"] == KIND_CONSULT

    # And it is not a stranger's to open: the owner is a path segment, so a
    # scoped read cannot name it at all.
    with pytest.raises(Exception) as exc:
        asyncio.run(
            get_delegation_status("scout-consult-abc", user=WebUser(id=8, role="user"))
        )
    assert getattr(exc.value, "status_code", None) == 404
