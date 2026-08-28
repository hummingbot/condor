"""SEC-197: a backtest read or delete is scoped to the run's own server.

The single-item endpoints used to resolve saved results purely by task_id, so a
user with access to server A could read or delete a backtest that belonged to
server B. The rule survived the routes: FEAT-076 deleted the per-server pair,
and the archive is now where a run's server comes from — the record, not the
path — so the check has to read the summary, which is also the tier that
survives retention.

These pin the corner the payload tier opened: "no payload" must not read as "no
owner", or a pruned run becomes a cross-server delete.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import condor.web.routes.backtesting as bt_routes
from condor.backtest_store import BacktestStore
from condor.web.models import WebUser

ALICE = WebUser(id=1, username="alice", first_name="A", role="user")

# Alice trades on server-a only; the saved run belongs to server-b.
_ACCESS = {"server-a": {1}, "server-b": {2}}


class _ConfigManager:
    def list_servers(self):
        return {name: {} for name in _ACCESS}

    def has_server_access(self, user_id, name, min_permission=None):
        return user_id in _ACCESS.get(name, set())


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = BacktestStore(data_dir=tmp_path / "backtests")
    store.save_result("server-b", "task-b", {"status": "completed", "config": {}})
    monkeypatch.setattr(bt_routes, "get_backtest_store", lambda: store)
    monkeypatch.setattr(bt_routes, "get_config_manager", lambda: _ConfigManager())
    return store


def test_get_cross_server_task_is_404(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_archived_backtest("task-b", user=ALICE))
    assert exc.value.status_code == 404


def test_delete_cross_server_is_404_and_keeps_record(store):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.delete_archived_backtest("task-b", user=ALICE))
    assert exc.value.status_code == 404
    assert store.get_result("task-b") is not None


def test_a_pruned_task_stays_scoped_to_its_server(store):
    """Retention must not open a hole: no payload is not the same as no owner.

    The ownership check used to read the payload, so once FEAT-075 let a
    payload expire, "no saved result" and "someone else's saved result" became
    the same answer -- and the second one is a cross-server delete.
    """
    store.prune_payloads(0)  # keep the summary, drop the payload by hand
    store._unlink_payload("task-b")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.delete_archived_backtest("task-b", user=ALICE))
    assert exc.value.status_code == 404
    assert store.get_summary("task-b") is not None

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt_routes.get_archived_backtest("task-b", user=ALICE))
    assert exc.value.status_code == 404
