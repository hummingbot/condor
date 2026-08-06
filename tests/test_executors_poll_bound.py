"""Tests for PERF-117: the executors poll is bounded, the history walk is not.

``fetch_executors`` used to be a bare alias of ``fetch_all_executors``, so the
2s SDS poll for ``ServerDataType.EXECUTORS`` walked the server's entire executor
history — up to ten sequential ``search_executors`` round trips per tick, while
SDS's rate limiter only ever spends one token per key per tick. These tests pin
the poll at a single request, keep the on-demand walk unbounded, and cover the
one surface that read full history out of the cache: the offset-cursor scroll of
``/servers/{name}/executors/page``, which must continue past the cached prefix
instead of ending there.
"""

import asyncio

import pytest

from condor.fetchers.executors import (
    EXECUTORS_POLL_MAX,
    MAX_EXECUTORS_FETCH,
    fetch_all_executors,
    fetch_executors,
)
from condor.server_data_service import ServerDataService, ServerDataType
from condor.web.models import WebUser
from condor.web.routes.executors import list_executors_page


def _rows(start, count):
    """``count`` distinct executor rows numbered from ``start``."""
    return [
        {"id": f"e{i}", "config": {"type": "position"}, "status": "TERMINATED"}
        for i in range(start, start + count)
    ]


class FakeClient:
    """Client serving an endless executor stream, recording every request."""

    def __init__(self, total):
        self.calls = []
        self.executors = self._Executors(self)
        self._total = total

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            outer = self._outer
            outer.calls.append(kwargs)
            # The cursor encodes how far the walk has come.
            cursor = kwargs.get("cursor")
            start = int(cursor) if cursor else 0
            limit = kwargs["limit"]
            count = max(0, min(limit, outer._total - start))
            return {
                "executors": _rows(start, count),
                "next_cursor": (
                    str(start + count) if start + count < outer._total else None
                ),
            }


# ── the poll ──


def test_poll_tick_issues_a_single_search_request():
    """One SDS EXECUTORS tick costs one request — the token the limiter spends."""
    client = FakeClient(total=10_000)

    rows = asyncio.run(fetch_executors(client))

    assert len(client.calls) == 1, "the 2s poll must not fan out into a walk"
    assert client.calls[0]["limit"] == EXECUTORS_POLL_MAX
    assert client.calls[0].get("cursor") is None
    assert len(rows) == EXECUTORS_POLL_MAX


def test_sds_registered_fetch_for_executors_is_the_bounded_poll():
    """Driving the registered fetcher through SDS still costs one request."""
    client = FakeClient(total=10_000)

    async def _drive():
        sds = ServerDataService()

        async def _fake_get_client(server_name):
            return client

        sds._get_client = _fake_get_client
        sds.register_fetch(ServerDataType.EXECUTORS, fetch_executors)
        return await sds.get_or_fetch("srv", ServerDataType.EXECUTORS)

    result = asyncio.run(_drive())

    assert len(client.calls) == 1
    assert len(result) == EXECUTORS_POLL_MAX


def test_full_history_walk_keeps_its_unbounded_default():
    """``fetch_all_executors`` is untouched: it still walks to the safety cap."""
    client = FakeClient(total=10_000)

    rows = asyncio.run(fetch_all_executors(client))

    assert len(rows) == MAX_EXECUTORS_FETCH
    assert len(client.calls) == MAX_EXECUTORS_FETCH // EXECUTORS_POLL_MAX


# ── the scroll that used to read full history out of the cache ──


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        return self._client


@pytest.fixture
def page_env(monkeypatch):
    """Wire ``list_executors_page`` to a fresh SDS and a fake API client."""
    sds = ServerDataService()
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service", lambda: sds
    )

    def _bind(client):
        monkeypatch.setattr(
            "condor.web.routes.executors.get_config_manager", lambda: _FakeCM(client)
        )
        return sds

    return _bind


_USER = WebUser(id=1, role="admin")


def _page(cursor="", limit=50):
    """Call the endpoint the way FastAPI would: every query param resolved."""
    return asyncio.run(
        list_executors_page(
            name="srv",
            cursor=cursor,
            limit=limit,
            executor_type="",
            trading_pair="",
            status="",
            controller_id="",
            user=_USER,
        )
    )


def test_first_page_still_paints_from_the_cache(page_env):
    """The hot path is unchanged: first page served from cache, no API call."""
    client = FakeClient(total=10_000)
    sds = page_env(client)
    sds.put("srv", ServerDataType.EXECUTORS, _rows(0, EXECUTORS_POLL_MAX))

    result = _page()

    assert client.calls == [], "first page must not touch the API"
    assert len(result["executors"]) == 50
    assert result["next_cursor"] == "__sds_offset__50"


def test_scroll_continues_past_the_cached_prefix(page_env):
    """Past the poll's page budget the scroll keeps going against the API."""
    client = FakeClient(total=10_000)
    sds = page_env(client)
    sds.put("srv", ServerDataType.EXECUTORS, _rows(0, EXECUTORS_POLL_MAX))

    result = _page(cursor=f"__sds_offset__{EXECUTORS_POLL_MAX}")

    assert len(result["executors"]) == 50, "the scroll must not end at the cache length"
    assert result["next_cursor"] == f"__sds_offset__{EXECUTORS_POLL_MAX + 50}"
    assert client.calls, "the page past the cache comes from the API"
    ids = [e.id for e in result["executors"]]
    assert ids[0] == f"e{EXECUTORS_POLL_MAX}", "no rows skipped or repeated"


def test_scroll_ends_when_the_cache_holds_the_whole_history(page_env):
    """A cache shorter than the poll budget proves there is nothing past it."""
    client = FakeClient(total=20)
    sds = page_env(client)
    sds.put("srv", ServerDataType.EXECUTORS, _rows(0, 20))

    result = _page()

    assert len(result["executors"]) == 20
    assert result["next_cursor"] is None
    assert client.calls == []


def test_scroll_ends_at_the_true_end_of_the_stream(page_env):
    """Walking past the cache reports end-of-stream from the API, not the cache."""
    client = FakeClient(total=EXECUTORS_POLL_MAX + 30)
    sds = page_env(client)
    sds.put("srv", ServerDataType.EXECUTORS, _rows(0, EXECUTORS_POLL_MAX))

    result = _page(cursor=f"__sds_offset__{EXECUTORS_POLL_MAX}")

    assert len(result["executors"]) == 30
    assert result["next_cursor"] is None


def test_expired_cache_mid_scroll_does_not_restart_the_page(page_env):
    """An offset cursor with no cache resumes at the offset, not at row zero."""
    client = FakeClient(total=10_000)
    page_env(client)  # SDS left empty: the cache expired mid-scroll

    result = _page(cursor="__sds_offset__200")

    ids = [e.id for e in result["executors"]]
    assert ids[0] == "e200", "an expired cache must not rewind the scroll"
    assert result["next_cursor"] == "__sds_offset__250"
