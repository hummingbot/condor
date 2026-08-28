"""FEAT-075: the archive routes list across servers and filter by access.

The archive is not under ``/servers/{name}`` — a backtest is a computation over
candles and the server is only provenance — so authorization has to *filter*
rather than gate, and an id-addressed read has to resolve its server from the
record. These pin both, plus the two states the payload tier introduced: a
listing that never carries a payload, and a run whose chart has expired.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import HTTPException

import condor.web.routes.backtesting as bt_routes
from condor.backtest_store import BacktestStore
from condor.web.models import WebUser

ALICE = WebUser(id=1, username="alice", first_name="A", role="user")
BOB = WebUser(id=2, username="bob", first_name="B", role="user")

# alice trades on both servers; bob only on brigado_2.
_ACCESS = {"local": {1}, "brigado_2": {1, 2}}


class _ConfigManager:
    def list_servers(self):
        return {name: {} for name in _ACCESS}

    def has_server_access(self, user_id, name, min_permission=None):
        return user_id in _ACCESS.get(name, set())

    async def get_client(self, name):
        raise RuntimeError("no live api in tests")


def _envelope(net_pnl=12.5, pair="BTC-USDT"):
    return {
        "status": "completed",
        "completed_at": time.time(),
        "config": {
            "config": {
                "id": "ema",
                "controller_name": "pmm_simple",
                "trading_pair": pair,
                "connector_name": "binance",
            },
            "start_time": 1_751_328_000,
            "end_time": 1_759_104_000,
            "backtesting_resolution": "1m",
            "trade_cost": 0.0002,
        },
        "result": {
            "results": {"net_pnl_quote": net_pnl},
            "processed_data": {"close": {str(i): i for i in range(2000)}},
            "executors": [],
        },
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = BacktestStore(data_dir=tmp_path / "backtests")
    store.save_result("local", "task-local", _envelope(net_pnl=1.0))
    store.save_result(
        "brigado_2", "task-brigado", _envelope(net_pnl=2.0, pair="SOL-USDC")
    )
    monkeypatch.setattr(bt_routes, "get_backtest_store", lambda: store)
    monkeypatch.setattr(bt_routes, "get_config_manager", lambda: _ConfigManager())
    return store


def _archive(user, server=None):
    return asyncio.run(bt_routes.list_backtest_archive(server=server, user=user))


# ── listing ───────────────────────────────────────────────────────────────────


def test_the_archive_spans_every_server_the_caller_can_reach(store):
    body = _archive(ALICE)

    assert body["migrated"] is True
    assert {s["task_id"] for s in body["summaries"]} == {"task-local", "task-brigado"}
    assert {s["server"] for s in body["summaries"]} == {"local", "brigado_2"}


def test_the_archive_omits_a_server_the_caller_cannot_access(store):
    body = _archive(BOB)

    assert [s["task_id"] for s in body["summaries"]] == ["task-brigado"]


def test_the_archive_can_still_be_scoped_to_one_server(store):
    body = _archive(ALICE, server="local")

    assert [s["task_id"] for s in body["summaries"]] == ["task-local"]


def test_no_listing_response_carries_a_payload(store):
    """The defect this feature closes: a 1 GB response to fill six columns."""
    body = _archive(ALICE)
    assert "processed_data" not in json.dumps(body)

    tasks = asyncio.run(bt_routes.list_backtest_tasks("local", user=ALICE))
    assert [t["task_id"] for t in tasks] == ["task-local"]
    assert "result" not in tasks[0]
    assert "processed_data" not in json.dumps(tasks)
    assert tasks[0]["metrics"]["net_pnl_quote"] == 1.0
    # The six fields the list actually renders all survive the trim.
    assert tasks[0]["config"]["config"]["trading_pair"] == "BTC-USDT"
    assert tasks[0]["config"]["backtesting_resolution"] == "1m"


def test_the_task_list_stays_far_under_the_wire_budget(store):
    """22 runs used to ship 1 GB; a summary row is ~1.6 KB."""
    for i in range(22):
        store.save_result("local", f"bulk-{i}", _envelope())

    tasks = asyncio.run(bt_routes.list_backtest_tasks("local", user=ALICE))
    assert len(tasks) == 23
    assert len(json.dumps(tasks).encode()) < 100_000


# ── id-addressed reads ────────────────────────────────────────────────────────


def test_a_run_from_another_server_opens_without_switching_the_sidebar(store):
    task = asyncio.run(bt_routes.get_archived_backtest("task-brigado", user=ALICE))

    assert task["task_id"] == "task-brigado"
    assert task["result"]["results"]["net_pnl_quote"] == 2.0


def test_an_inaccessible_run_is_404_by_id(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_archived_backtest("task-local", user=BOB))
    assert exc.value.status_code == 404


def test_an_unknown_id_is_404(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_archived_backtest("nope", user=ALICE))
    assert exc.value.status_code == 404


def test_an_expired_payload_is_409_with_its_summary_attached(store):
    store._unlink_payload("task-local")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_archived_backtest("task-local", user=ALICE))
    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "payload_expired"
    # "Chart expired", not "not found": the metrics are right there.
    assert exc.value.detail["summary"]["metrics"]["net_pnl_quote"] == 1.0


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_by_id_needs_access_to_the_runs_own_server(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.delete_archived_backtest("task-local", user=BOB))
    assert exc.value.status_code == 404
    assert store.get_summary("task-local") is not None

    assert asyncio.run(
        bt_routes.delete_archived_backtest("task-local", user=ALICE)
    ) == {"deleted": True}
    assert store.get_summary("task-local") is None


def test_a_run_with_no_server_provenance_is_reachable_by_nobody(store):
    store.save_result("", "orphan", _envelope())

    assert "orphan" not in {s["task_id"] for s in _archive(ALICE)["summaries"]}
    with pytest.raises(HTTPException):
        asyncio.run(bt_routes.get_archived_backtest("orphan", user=ALICE))
