"""A code run is work an agent did, and the Activity feed says so (FEAT-061).

Three things are load-bearing and all three are about who may see what.

The store is global and keyed by an in-record owner, while every other kind of
run is partitioned by a path segment — so the merge in
``list_delegation_history`` is where two ownership models meet, and it has to
hold both rules at once. Reading a run's *body* is as sensitive as producing it
(a snippet can print ``os.environ``), so its route sits behind the same
``_may_run_code`` gate that ran it, plus ownership. And a run recorded before
this feature has no owner, which means *unknown* and therefore admin-only —
fail closed, the rule SEC-196 set for ownerless reports.

The fourth is a regression this feature could easily have introduced: the chat
dock asks for background tasks only, and no code run may ever reach it.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio

import pytest
from fastapi import HTTPException

import condor.code_runs as code_runs_module
import config_manager
from condor import code_runner
from condor.agents import delegate as delegate_module
from condor.agents import delegation_history as history_module
from condor.code_runs import CodeRun, CodeRunStore
from condor.web.models import WebUser
from condor.web.routes import code as code_routes
from condor.web.routes.agents import list_delegation_history
from condor.web.routes.code import get_code_run

OWNER = WebUser(id=7, role="user")  # holds the code_run grant
STRANGER = WebUser(id=8, role="user")  # holds it too, owns nothing
NO_GRANT = WebUser(id=9, role="user")  # approved, may not run code
ADMIN = WebUser(id=99, role="admin")

GRANTED = {OWNER.id, STRANGER.id}


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN.id

    def get_user_preference(self, user_id: int, key: str, default=None):
        return user_id in GRANTED


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real store under tmp_path, wired into every reader of the singleton."""
    written = CodeRunStore(tmp_path / "code_runs")
    monkeypatch.setattr(code_runs_module, "get_code_run_store", lambda: written)
    monkeypatch.setattr(code_routes, "get_code_run_store", lambda: written)
    monkeypatch.setattr(code_runner, "get_code_run_store", lambda: written)
    return written


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    """One known admin, one known grant list, and an empty live registry."""
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(code_routes, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(
        history_module, "list_history", lambda *a, **kw: list(HISTORY_ROWS)
    )
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


# What the *other* two sources return; replaced per-test where it matters.
HISTORY_ROWS: list[dict] = []


@pytest.fixture(autouse=True)
def _no_history():
    global HISTORY_ROWS
    HISTORY_ROWS = []
    yield
    HISTORY_ROWS = []


def _save(store, run_id, **kw):
    kw.setdefault("agent", "quant")
    kw.setdefault("created", 1000.0)
    kw.setdefault("user_id", OWNER.id)
    store.save(CodeRun(id=run_id, **kw))


def _history(user, **kw):
    return asyncio.run(list_delegation_history(user=user, **kw))["delegations"]


def _ids(rows):
    return [r["task_id"] for r in rows]


# ── the third source shows up ──


def test_a_code_run_appears_in_its_agents_activity(store):
    _save(store, "code_1_a", label="returns of SOL 1h", status="ok", duration_ms=340)

    rows = _history(OWNER, agent="quant")

    assert _ids(rows) == ["code_1_a"]
    assert rows[0]["kind"] == "code"
    assert rows[0]["task"] == "returns of SOL 1h"
    assert rows[0]["status"] == "done"


def test_the_row_carries_a_duration_the_store_actually_measured(store):
    """Not a stand-in: the feed's median has to be a real number."""
    _save(store, "code_1_a", created=1000.0, duration_ms=340)

    row = _history(OWNER, agent="quant")[0]

    assert row["started_at"] == 1000.0
    assert row["ended_at"] == pytest.approx(1000.34)


def test_a_timed_out_snippet_reads_timeout_and_not_error(store):
    """The store bothered to record the distinction; it survives to the wire."""
    _save(store, "code_ok_a", status="ok")
    _save(store, "code_bad_a", status="error")
    _save(store, "code_cut_a", status="timeout")

    by_id = {r["task_id"]: r["status"] for r in _history(OWNER)}

    assert by_id == {
        "code_ok_a": "done",
        "code_bad_a": "error",
        "code_cut_a": "timeout",
    }


def test_a_code_row_invents_nothing_for_the_cells_it_has_no_answer_for(store):
    _save(store, "code_1_a")

    row = _history(OWNER)[0]

    assert row["caller"] == "" and row["conversation_id"] == ""
    assert row["tool_count"] == 0 and row["server_name"] is None


def test_the_agent_filter_reaches_the_code_source_too(store):
    _save(store, "code_1_a", agent="quant")
    _save(store, "code_2_a", agent="scout")

    assert _ids(_history(OWNER, agent="quant")) == ["code_1_a"]
    assert _ids(_history(OWNER, agent="scout")) == ["code_2_a"]


def test_code_runs_and_delegations_share_one_timeline_newest_first(store):
    global HISTORY_ROWS
    HISTORY_ROWS = [
        {
            "task_id": "d-old",
            "agent": "quant",
            "kind": "delegate",
            "status": "done",
            "user_id": OWNER.id,
            "started_at": 500.0,
            "task": "back-test the SOL grid",
        }
    ]
    _save(store, "code_1_a", created=1000.0)

    assert _ids(_history(OWNER)) == ["code_1_a", "d-old"]


# ── ownership: two models, one route ──


def test_a_code_run_is_invisible_to_anyone_but_its_owner(store):
    _save(store, "code_mine_a", user_id=OWNER.id)

    assert _ids(_history(OWNER)) == ["code_mine_a"]
    assert _history(STRANGER) == [], "a grant is not a licence to read output"


def test_an_admin_sees_every_code_run(store):
    _save(store, "code_1_a", user_id=OWNER.id)
    _save(store, "code_2_a", user_id=STRANGER.id)

    assert set(_ids(_history(ADMIN))) == {"code_1_a", "code_2_a"}


def test_a_run_recorded_before_this_feature_is_admin_only(store):
    """No owner means *unknown*, and unknown fails closed (SEC-196)."""
    _save(store, "code_legacy_a", user_id=0)

    assert _history(OWNER) == []
    assert _ids(_history(ADMIN)) == ["code_legacy_a"]


# ── the gate on the whole source ──


def test_a_caller_without_the_grant_gets_no_code_rows(store):
    _save(store, "code_1_a", user_id=NO_GRANT.id)

    assert _history(NO_GRANT) == []


def test_asking_for_code_without_the_grant_is_empty_and_not_a_refusal(store):
    """A filter over a kind you have none of is honestly empty, not a 403."""
    _save(store, "code_1_a", user_id=NO_GRANT.id)

    assert _history(NO_GRANT, kind="code") == []


# ── the other two kinds are untouched ──


def test_kind_delegate_returns_exactly_what_it_returned_before(store):
    """The chat dock pins this kind; a code run reaching it is the regression."""
    global HISTORY_ROWS
    HISTORY_ROWS = [
        {
            "task_id": "d-1",
            "agent": "quant",
            "kind": "delegate",
            "status": "done",
            "user_id": OWNER.id,
            "started_at": 500.0,
            "task": "back-test the SOL grid",
        }
    ]
    _save(store, "code_1_a", created=9000.0)  # newer, so it would sort on top

    assert _ids(_history(OWNER, kind="delegate")) == ["d-1"]


def test_kind_consult_never_admits_a_code_run(store):
    global HISTORY_ROWS
    HISTORY_ROWS = [
        {
            "task_id": "c-1",
            "agent": "quant",
            "kind": "consult",
            "status": "done",
            "user_id": OWNER.id,
            "started_at": 500.0,
            "task": "what is the funding on HYPE",
        }
    ]
    _save(store, "code_1_a", created=9000.0)

    assert _ids(_history(OWNER, kind="consult")) == ["c-1"]


def test_kind_code_returns_only_code_runs(store):
    global HISTORY_ROWS
    HISTORY_ROWS = []  # list_history does the kind filter for its own two
    _save(store, "code_1_a")

    assert _ids(_history(OWNER, kind="code")) == ["code_1_a"]


def test_the_limit_still_bounds_a_merged_page(store):
    global HISTORY_ROWS
    HISTORY_ROWS = [
        {
            "task_id": f"d-{i}",
            "agent": "quant",
            "kind": "delegate",
            "status": "done",
            "user_id": OWNER.id,
            "started_at": 100.0 + i,
            "task": "x",
        }
        for i in range(3)
    ]
    for i in range(3):
        _save(store, f"code_{i}_a", created=200.0 + i)

    rows = _history(OWNER, limit=2)

    assert len(rows) == 2, "the cut holds across the merge, not per source"
    assert _ids(rows) == ["code_2_a", "code_1_a"], "newest first, whatever wrote them"


# ── GET /code/runs/{run_id} ──


def _get(run_id, user):
    return asyncio.run(get_code_run(run_id, user=user))


def test_the_owner_reads_the_whole_run(store):
    _save(
        store,
        "code_1_a",
        code="print(1)",
        stdout="1\n",
        result="2",
        traceback='File "<code_run>", line 1',
    )

    run = _get("code_1_a", OWNER)

    assert run["code"] == "print(1)"
    assert run["stdout"] == "1\n"
    assert run["result"] == "2"
    assert run["traceback"] == 'File "<code_run>", line 1'


def test_an_admin_reads_anyones_run(store):
    _save(store, "code_1_a", user_id=OWNER.id, stdout="secret\n")

    assert _get("code_1_a", ADMIN)["stdout"] == "secret\n"


def test_a_granted_stranger_gets_a_404_not_someone_elses_stdout(store):
    _save(store, "code_1_a", user_id=OWNER.id)

    with pytest.raises(HTTPException) as exc:
        _get("code_1_a", STRANGER)
    assert exc.value.status_code == 404


def test_an_ownerless_run_is_readable_by_an_admin_only(store):
    _save(store, "code_legacy_a", user_id=0)

    assert _get("code_legacy_a", ADMIN)["id"] == "code_legacy_a"
    with pytest.raises(HTTPException) as exc:
        _get("code_legacy_a", OWNER)
    assert exc.value.status_code == 404


def test_a_caller_without_the_grant_is_refused_outright(store):
    """403 before the store is even asked: the gate is on reading, not on owning."""
    _save(store, "code_1_a", user_id=NO_GRANT.id)

    with pytest.raises(HTTPException) as exc:
        _get("code_1_a", NO_GRANT)
    assert exc.value.status_code == 403


def test_an_unknown_run_is_a_404(store):
    with pytest.raises(HTTPException) as exc:
        _get("code_nope_a", OWNER)
    assert exc.value.status_code == 404


def test_an_illegal_id_is_a_404_and_never_a_file_read(store, tmp_path):
    """``CodeRunStore.get`` refuses the shape; the route adds no second check."""
    (tmp_path / "secret.json").write_text('{"token": "hunter2"}')

    for bad in ("../secret", "/etc/passwd", ""):
        with pytest.raises(HTTPException) as exc:
            _get(bad, ADMIN)
        assert exc.value.status_code == 404


# ── the owner is recorded where it is known ──


def test_execute_code_stamps_the_authenticated_caller(store, monkeypatch):
    """The route already hands `chat_id`; before this feature it was discarded."""

    async def _client(*a, **kw):
        return object()

    monkeypatch.setattr(config_manager, "get_client", _client)

    out = asyncio.run(code_runner.execute_code("result = 1", chat_id=OWNER.id))

    assert out["user_id"] == OWNER.id
    assert store.list()[0]["user_id"] == OWNER.id, "and in the index, for listings"
