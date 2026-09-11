"""FEAT-079: the dashboard finds a run's stored report instead of remaking it.

An archived run is immutable, so charting one is "generate once, look it up
forever". These pin the lookup route's three obligations: the subject key is
built server-side from the parts (so the caller cannot point it at another
server), a controller is a different subject from the run that contains it, and
a miss — nothing charted yet, or a pruned index — is an ordinary 200 with a null
id, not an error.

The controllers route is pinned alongside it: it answers out of the same warm
performance object the run's header came from, so expanding a row costs no
second walk of the archive.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

import pytest

import condor.reports as reports
import condor.web.routes.archived as archived_routes
from condor.fetchers import archived_run
from condor.reports import ReportBuilder, store, subjects
from condor.web.models import NormalizedExecutor, WebUser

ALICE = WebUser(id=1, username="alice", first_name="A", role="user")
BOB = WebUser(id=2, username="bob", first_name="B", role="user")

DB = "/data/bots/archive/run.sqlite"


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(reports, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


def _save(title: str, subject: str, owner: int) -> str:
    builder = ReportBuilder(title).kpi("PnL", "1").subject(subject)
    with store.attribute_owner(owner):
        return asyncio.run(builder.save())


def _lookup(user=ALICE, controller_id="", server="brigado"):
    return asyncio.run(
        archived_routes.get_archived_report(
            name=server, db_path=DB, controller_id=controller_id, user=user
        )
    )


# ── The lookup ──


def test_a_stored_report_is_found_by_its_subject(reports_dir):
    report_id = _save("Run", subjects.bot_run("brigado", DB), owner=ALICE.id)

    assert _lookup().report_id == report_id


def test_nothing_charted_yet_is_a_null_id_not_an_error(reports_dir):
    answer = _lookup()

    assert answer.report_id is None
    assert answer.created_at is None


def test_a_pruned_report_misses_cleanly(reports_dir):
    _save("Run", subjects.bot_run("brigado", DB), owner=ALICE.id)
    (reports_dir / "reports_index.json").write_text("[]", encoding="utf-8")

    assert _lookup().report_id is None


def test_a_controller_is_a_different_subject_from_its_run(reports_dir):
    run_report = _save("Run", subjects.bot_run("brigado", DB), owner=ALICE.id)
    ctrl_report = _save(
        "Controller", subjects.bot_run("brigado", DB, "pmm_1"), owner=ALICE.id
    )

    assert _lookup().report_id == run_report
    assert _lookup(controller_id="pmm_1").report_id == ctrl_report
    assert _lookup(controller_id="pmm_2").report_id is None


def test_the_key_carries_the_server_so_a_run_cannot_alias_another(reports_dir):
    _save("Run", subjects.bot_run("brigado", DB), owner=ALICE.id)

    assert _lookup(server="moneymaker").report_id is None


def test_a_report_is_only_found_by_the_user_who_made_it(reports_dir):
    """SEC-196: two users charting the same controller each get their own."""
    _save("Run", subjects.bot_run("brigado", DB), owner=ALICE.id)

    assert _lookup(user=BOB).report_id is None


def test_the_newest_report_for_a_subject_wins(reports_dir):
    _save("First", subjects.bot_run("brigado", DB), owner=ALICE.id)
    newest = _save("Second", subjects.bot_run("brigado", DB), owner=ALICE.id)

    answer = _lookup()
    assert answer.report_id == newest
    assert answer.title == "Second"


# ── The controllers route ──


def _executor(controller_id: str, pnl: float) -> NormalizedExecutor:
    return NormalizedExecutor(
        id=f"e-{controller_id}-{pnl}",
        connector="binance",
        trading_pair="SOL-USDC",
        side="BUY",
        pnl=pnl,
        volume=100.0,
        cum_fees_quote=0.1,
        timestamp=1_700_000_000.0,
        close_timestamp=1_700_000_060.0,
        controller_id=controller_id,
    )


class _NoClient:
    """Any use of the API client at all is a failure: the run is warm."""

    async def get_client(self, name):  # pragma: no cover - must not be reached
        raise AssertionError("the controllers route re-fetched a cached run")


def test_controllers_are_served_out_of_the_warm_run(monkeypatch):
    from condor.web.models import ArchivedBotPerformance

    perf = ArchivedBotPerformance(
        bot_name="bot",
        db_path=DB,
        executors=[
            _executor("alpha", 1.0),
            _executor("beta", -9.0),
            _executor("", 0.5),
        ],
    )
    monkeypatch.setattr(
        archived_run,
        "_performance_cache",
        OrderedDict({("brigado", DB): perf}),
        raising=True,
    )
    monkeypatch.setattr(archived_routes, "get_config_manager", lambda: _NoClient())

    answer = asyncio.run(
        archived_routes.get_archived_controllers(name="brigado", db_path=DB, user=ALICE)
    )

    assert [c.controller_id for c in answer.controllers] == ["beta", "alpha", ""]
    assert answer.controllers[1].executor_count == 1
    assert answer.controllers[0].pnl_usd == -9.0
