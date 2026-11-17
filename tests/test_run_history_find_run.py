"""``/terminated/history`` draws the run that was asked for, or says it has none (WR-06).

A bot name is reused across runs — the stop-archive-redeploy workflow makes
that the normal case — so the route resolves the run by name *and* deploy time.
When the time matched nothing it used to fall back to the newest run of that
name and draw that run's curve, with nothing in the response to say so: a
trader looking at an old run's row read a different run's PnL.

The timestamps are now compared as instants with a small tolerance (which is
all the fallback was ever for: a rounded or reformatted spelling of the same
moment), and anything else answers ``source: "none"``.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from condor.web.routes import controller_performance as cp

OLD = "2026-08-01T10:00:00+00:00"
NEW = "2026-09-01T10:00:00+00:00"


def _raw(run_id, deployed_at, controller):
    return {
        "id": run_id,
        "bot_name": "gan",
        "deployed_at": deployed_at,
        "stopped_at": "2026-09-02T00:00:00+00:00",
        "deployment_status": "ARCHIVED",
        "deployment_config": {"controllers_config": [controller]},
    }


class FakeClient:
    def __init__(self, runs=None, error=None):
        self._runs = runs or []
        self._error = error
        self.bot_orchestration = self

    async def get_bot_runs(self, **kwargs):
        if self._error:
            raise self._error
        return self._runs


@pytest.fixture
def history(monkeypatch):
    """Capture what the route asks the history fetch for, instead of fetching."""
    asked: list[dict] = []

    async def _fetch(client, name, **kwargs):
        asked.append(kwargs)
        return SimpleNamespace(
            controllers={},
            identities={},
            interval="5m",
            source="snapshots",
            points=0,
            cached=False,
        )

    monkeypatch.setattr(cp, "fetch_run_history", _fetch)
    return asked


def _route(client, monkeypatch, deployed_at):
    class FakeCM:
        async def get_client(self, name):
            return client

    monkeypatch.setattr(cp, "get_config_manager", lambda: FakeCM())
    return asyncio.run(
        cp.get_run_history(
            name="srv",
            bot_name="gan",
            deployed_at=deployed_at,
            db_path=None,
            user=object(),
        )
    )


TWO_RUNS = [_raw(2, NEW, "new-ctrl"), _raw(1, OLD, "old-ctrl")]


def test_a_timestamp_matching_no_run_draws_nothing(history, monkeypatch):
    out = _route(FakeClient(TWO_RUNS), monkeypatch, "2026-07-01T10:00:00+00:00")

    assert out.source == "none"
    assert "newest 50" in (out.detail or "")
    assert history == [], "no other run's history was fetched in its place"


def test_the_exact_run_is_the_one_drawn(history, monkeypatch):
    out = _route(FakeClient(TWO_RUNS), monkeypatch, OLD)

    assert out.source == "snapshots"
    assert history[0]["deployed_at"] == OLD
    assert history[0]["controller_ids"] == ["old-ctrl"]


@pytest.mark.parametrize(
    "spelling",
    [
        "2026-08-01T10:00:00Z",  # reformatted
        "2026-08-01T10:00:00",  # naive, read as UTC
        "2026-08-01T12:00:00+02:00",  # another offset, the same instant
        "2026-08-01T10:00:00.400000+00:00",  # sub-second drift
    ],
)
def test_another_spelling_of_the_same_instant_still_finds_the_run(
    history, monkeypatch, spelling
):
    _route(FakeClient(TWO_RUNS), monkeypatch, spelling)

    assert history[0]["controller_ids"] == ["old-ctrl"]
    # The history is fetched under the run's own deploy time, not the caller's.
    assert history[0]["deployed_at"] == OLD


def test_a_neighbouring_run_is_out_of_tolerance(history, monkeypatch):
    out = _route(FakeClient(TWO_RUNS), monkeypatch, "2026-08-01T10:00:05+00:00")

    assert out.source == "none"
    assert history == []


def test_an_unparseable_timestamp_draws_nothing(history, monkeypatch):
    out = _route(FakeClient(TWO_RUNS), monkeypatch, "last tuesday")

    assert out.source == "none"
    assert history == []


def test_an_outage_is_not_reported_as_a_run_that_never_existed(history, monkeypatch):
    with pytest.raises(HTTPException) as raised:
        _route(FakeClient(error=ConnectionError("boom")), monkeypatch, OLD)

    assert raised.value.status_code == 502
    assert "boom" not in raised.value.detail
    assert history == []
