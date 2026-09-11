"""CORR-259: the history route reads the cursor upstream actually sends.

The route used to look for ``next_cursor``/``cursor`` at the top level of the
response, but the Hummingbot API nests both under ``pagination``. Both keys were
therefore always absent and the dashboard told every client "this is the whole
history" even when upstream had truncated the page — no client could page
backwards, which is why a chart could only ever show its newest window.

The route now runs the same extractor the fetcher path uses
(``condor.fetchers._pagination.next_cursor``), so every spelling the backend has
shipped resolves, and a terminal page still reports ``None``.
"""

import asyncio

import pytest

import condor.web.routes.controller_performance as cp
from condor.web.models import WebUser

ROW = {
    "timestamp": "2026-08-27T10:05:00+00:00",
    "controller_id": "ctrl-0",
    "performance": {"realized_pnl_quote": 1.0, "volume_traded": 10.0},
}


class _Cm:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name):
        return True

    async def get_client(self, name):
        return self._client


class _Client:
    """Hands back one canned envelope and records the query it was asked."""

    def __init__(self, envelope):
        self._envelope = envelope
        self.calls: list[dict] = []

    @property
    def bot_orchestration(self):
        return self

    async def get_controller_performance_history(self, **kw):
        self.calls.append(kw)
        return self._envelope


def _call(monkeypatch, envelope, **overrides):
    client = _Client(envelope)
    monkeypatch.setattr(cp, "get_config_manager", lambda: _Cm(client), raising=True)
    params = dict(
        bot_name="bot-1",
        controller_id=None,
        start_time=None,
        end_time=None,
        interval="5m",
        limit=1,
        cursor=None,
        user=WebUser(id=1, role="user"),
    )
    params.update(overrides)
    return asyncio.run(cp.get_controller_performance_history("srv", **params))


def test_nested_pagination_cursor_reaches_the_client(monkeypatch):
    """The upstream envelope's ``pagination.next_cursor`` is what the route returns."""
    response = _call(
        monkeypatch,
        {
            "status": "success",
            "data": [ROW],
            "pagination": {
                "next_cursor": "2026-08-27T10:00:00+00:00",
                "has_more": True,
            },
        },
    )

    assert response.next_cursor == "2026-08-27T10:00:00+00:00"
    assert [s.timestamp for s in response.snapshots] == [ROW["timestamp"]]


def test_the_returned_cursor_is_the_one_that_fetches_older_rows(monkeypatch):
    """Handing the cursor back as ``?cursor=`` reaches upstream verbatim."""
    older = {
        **ROW,
        "timestamp": "2026-08-27T10:00:00+00:00",
    }
    client = _Client({"data": [older], "pagination": {"next_cursor": None}})
    monkeypatch.setattr(cp, "get_config_manager", lambda: _Cm(client), raising=True)

    response = asyncio.run(
        cp.get_controller_performance_history(
            "srv",
            bot_name="bot-1",
            controller_id=None,
            start_time=None,
            end_time=None,
            interval="5m",
            limit=1,
            cursor="2026-08-27T10:00:00+00:00",
            user=WebUser(id=1, role="user"),
        )
    )

    assert client.calls[0]["cursor"] == "2026-08-27T10:00:00+00:00"
    assert [s.timestamp for s in response.snapshots] == [older["timestamp"]]
    assert response.next_cursor is None


@pytest.mark.parametrize(
    "envelope",
    [
        {"data": [ROW], "pagination": {"next_cursor": None, "has_more": False}},
        {"data": [ROW], "pagination": {}},
        {"data": [ROW]},
        [ROW],
    ],
)
def test_terminal_page_reports_no_cursor(monkeypatch, envelope):
    """No further page upstream means a null cursor, for every envelope shape."""
    assert _call(monkeypatch, envelope).next_cursor is None


def test_legacy_top_level_spelling_still_resolves(monkeypatch):
    """Older responses put the cursor at the top level; both still work."""
    assert (
        _call(monkeypatch, {"data": [ROW], "next_cursor": "abc"}).next_cursor == "abc"
    )
