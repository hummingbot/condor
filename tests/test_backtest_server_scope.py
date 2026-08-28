"""SEC-197: backtest fetch/delete must be scoped to the {name} path server.

The single-item endpoints used to resolve saved results purely by task_id,
so a user with access to server A could read or delete a backtest that
belonged to server B by requesting /servers/A/backtesting/...{task_id_from_B}.
These pin the fix: cross-server ids 404 and leave the record untouched,
while same-server fetch and delete keep working.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import condor.web.routes.backtesting as bt_routes
from condor.backtest_store import BacktestStore


class _NoLiveConfigManager:
    """Config manager whose live API is unreachable, forcing the saved fallback."""

    async def get_client(self, name):
        raise RuntimeError("no live api in tests")


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = BacktestStore(data_dir=tmp_path / "backtests")
    store.save_result("server-b", "task-b", {"status": "completed", "config": {}})
    monkeypatch.setattr(bt_routes, "get_backtest_store", lambda: store)
    monkeypatch.setattr(bt_routes, "get_config_manager", lambda: _NoLiveConfigManager())
    return store


def test_get_cross_server_task_is_404(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_backtest_task("server-a", "task-b", user=None))
    assert exc.value.status_code == 404


def test_get_same_server_task_returns_saved_result(store):
    result = asyncio.run(bt_routes.get_backtest_task("server-b", "task-b", user=None))
    assert result["saved"] is True
    assert result["server"] == "server-b"


def test_delete_task_cross_server_is_404_and_keeps_record(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.delete_backtest_task("server-a", "task-b", user=None))
    assert exc.value.status_code == 404
    assert store.get_result("task-b") is not None


def test_delete_task_same_server_deletes(store):
    result = asyncio.run(
        bt_routes.delete_backtest_task("server-b", "task-b", user=None)
    )
    assert result == {"deleted": True}
    assert store.get_result("task-b") is None


def test_a_pruned_task_stays_scoped_to_its_server(store):
    """Retention must not open a hole: no payload is not the same as no owner.

    The ownership check used to read the payload, so once FEAT-075 let a
    payload expire, "no saved result" and "someone else's saved result" became
    the same answer -- and the second one is a cross-server delete.
    """
    store.prune_payloads(0)  # keep the summary, drop the payload by hand
    store._unlink_payload("task-b")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.delete_backtest_task("server-a", "task-b", user=None))
    assert exc.value.status_code == 404
    assert store.get_summary("task-b") is not None

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_backtest_task("server-a", "task-b", user=None))
    assert exc.value.status_code == 404


def test_an_expired_payload_on_the_right_server_is_409_not_404(store):
    """The distinct state: the run is real, its chart is not."""
    store._unlink_payload("task-b")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_backtest_task("server-b", "task-b", user=None))
    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "payload_expired"
    assert exc.value.detail["summary"]["task_id"] == "task-b"
