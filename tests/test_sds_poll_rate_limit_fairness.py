"""DL-08: a spent rate budget must not silently starve the newest subscribers.

Once a server's subscriptions exceeded its request budget, tokens went out in
subscription (dict) order on every tick, so the earliest keys always won and
the most recently added ones — the trade screen's price, a market-page channel
— stopped refreshing. The skipped fetch returned without a log line or a
counter.

Due keys are now ordered by how overdue they are relative to their own
interval, and every skip is counted, logged (throttled) and exposed in
``get_stats``.
"""

import asyncio
import logging

from condor.server_data_service import ServerDataService, ServerDataType


class _TickBudget:
    """Rate limiter stand-in: ``budget`` tokens per tick, refilled by the test."""

    def __init__(self, budget):
        self.budget = budget
        self.left = budget

    def refill(self):
        self.left = self.budget

    async def acquire(self, timeout=0.5):
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def test_every_key_is_eventually_fetched_and_skips_are_counted(caplog):
    budget, n_keys, ticks = 3, 6, 6
    fetched = {}

    async def fetcher(client, **params):
        pair = params["trading_pair"]
        fetched[pair] = fetched.get(pair, 0) + 1
        return {"pair": pair, "n": fetched[pair]}

    async def _drive():
        sds = ServerDataService()

        async def _fake_get_client(server_name):
            return object()

        sds._get_client = _fake_get_client
        sds.register_fetch(ServerDataType.PRICES, fetcher)
        limiter = _TickBudget(budget)
        sds._rate_limiters["srv"] = limiter

        pairs = [f"P{i}-USDT" for i in range(n_keys)]
        for pair in pairs:
            await sds.subscribe(
                "srv", ServerDataType.PRICES, "sub", interval=1, trading_pair=pair
            )
        primed = dict(fetched)

        for _ in range(ticks):
            # Every key is due again, the earliest-subscribed no more than the rest.
            for entry in sds._cache.values():
                entry.fetched_at -= 10
            limiter.refill()
            await sds._poll_tick()
        return sds, pairs, primed

    with caplog.at_level(logging.WARNING, logger="condor.server_data_service"):
        sds, pairs, primed = asyncio.run(_drive())

    polled = {pair: fetched[pair] - primed[pair] for pair in pairs}
    assert sum(polled.values()) == budget * ticks
    assert all(polled.values()), f"a late subscriber was starved: {polled}"
    assert max(polled.values()) - min(polled.values()) <= 1, polled

    skipped = (n_keys - budget) * ticks
    assert sds.get_stats()["rate_limit_skips"] == {"srv": skipped}

    warnings = [r for r in caplog.records if "rate limit on srv" in r.getMessage()]
    assert len(warnings) == 1, "skips must be logged, and throttled per server"
