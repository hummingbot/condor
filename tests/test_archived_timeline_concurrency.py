"""The archived-bots timeline fans its per-database fetches out (PERF-243).

``show_timeline_chart`` used to loop over every healthy archived database one
at a time, awaiting the summary and then the fully paginated trade walk before
touching the next bot — a strictly serial chain of N x (1 + pages) round trips.

These pin the fix: summaries and trade histories are gathered concurrently
under the existing ``MAX_CONCURRENT_DB_FETCHES`` semaphore, a database whose
fetch fails is skipped without taking the timeline down, cached summaries are
still preferred over a refetch, and ``bots_data`` keeps its shape and order.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.bots import archived, archived_chart


class FakeBackend:
    """Records overlap so a serial implementation is distinguishable."""

    def __init__(self, delay: float = 0.02):
        self.delay = delay
        self.summary_calls = []
        self.trade_calls = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.summary_failures = set()
        self.trade_failures = set()

    async def _work(self):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1

    async def summary(self, client, db_path):
        self.summary_calls.append(db_path)
        await self._work()
        if db_path in self.summary_failures:
            return None
        return {"bot_name": f"bot-{db_path}"}

    async def trades(self, client, db_path):
        self.trade_calls.append(db_path)
        await self._work()
        if db_path in self.trade_failures:
            raise RuntimeError(f"boom for {db_path}")
        return [{"db": db_path}]


@pytest.fixture
def wired(monkeypatch):
    """Patch the timeline's collaborators and capture the rendered bots_data."""
    backend = FakeBackend()
    captured = {}

    monkeypatch.setattr(archived, "fetch_database_summary", backend.summary)
    monkeypatch.setattr(archived, "fetch_all_trades", backend.trades)
    monkeypatch.setattr(
        archived, "get_bots_client", AsyncMock(return_value=(object(), None))
    )
    monkeypatch.setattr(
        archived_chart, "calculate_pnl_from_trades", lambda trades: {"total_pnl": 1.0}
    )

    def _generate(bots_data):
        captured["bots_data"] = bots_data
        return b"png"

    monkeypatch.setattr(archived_chart, "generate_timeline_chart", _generate)
    return backend, captured


def _update_and_context(databases, summaries):
    loading_msg = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock(), text=None)
    query = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock(return_value=loading_msg))
    )
    update = SimpleNamespace(
        callback_query=query, effective_chat=SimpleNamespace(id=42)
    )
    context = SimpleNamespace(
        user_data={"archived_databases": databases, "archived_summaries": summaries},
        bot=SimpleNamespace(send_photo=AsyncMock()),
    )
    return update, context, loading_msg


@pytest.mark.asyncio
async def test_timeline_fetches_databases_concurrently(wired):
    backend, captured = wired
    databases = [f"db{i}" for i in range(6)]
    update, context, _ = _update_and_context(databases, {})

    await archived.show_timeline_chart(update, context)

    # Serial execution would never put more than one call in flight.
    assert backend.max_in_flight > 1
    assert backend.max_in_flight <= archived.MAX_CONCURRENT_DB_FETCHES
    assert sorted(backend.summary_calls) == databases
    assert sorted(backend.trade_calls) == databases

    bots = captured["bots_data"]
    assert [b["db_path"] for b in bots] == databases
    assert bots[0].keys() == {"db_path", "summary", "trades", "pnl_data"}
    assert bots[0]["trades"] == [{"db": "db0"}]
    assert bots[0]["pnl_data"] == {"total_pnl": 1.0}


@pytest.mark.asyncio
async def test_timeline_concurrency_is_capped(wired):
    backend, _ = wired
    databases = [f"db{i}" for i in range(archived.MAX_CONCURRENT_DB_FETCHES + 7)]
    update, context, _ = _update_and_context(databases, {})

    await archived.show_timeline_chart(update, context)

    assert backend.max_in_flight <= archived.MAX_CONCURRENT_DB_FETCHES


@pytest.mark.asyncio
async def test_failed_database_is_skipped_and_rest_render(wired):
    backend, captured = wired
    backend.summary_failures = {"db1"}
    backend.trade_failures = {"db2"}
    databases = ["db0", "db1", "db2", "db3"]
    update, context, _ = _update_and_context(databases, {})

    await archived.show_timeline_chart(update, context)

    bots = captured["bots_data"]
    # db1 has no summary at all, so it is dropped; db2 keeps its summary and
    # degrades to an empty trade list rather than blowing up the timeline.
    assert [b["db_path"] for b in bots] == ["db0", "db2", "db3"]
    assert [b["db_path"] for b in bots if b["trades"] == []] == ["db2"]
    # No trade walk is started for a database without a summary.
    assert "db1" not in backend.trade_calls


@pytest.mark.asyncio
async def test_cached_summaries_are_preferred_over_refetch(wired):
    backend, captured = wired
    databases = ["db0", "db1"]
    cached = {"db0": {"bot_name": "cached-bot"}}
    update, context, _ = _update_and_context(databases, cached)

    await archived.show_timeline_chart(update, context)

    assert backend.summary_calls == ["db1"]
    bots = captured["bots_data"]
    assert bots[0]["summary"] == {"bot_name": "cached-bot"}
    assert [b["db_path"] for b in bots] == databases
