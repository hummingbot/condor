"""Tests for ARCH-145: portfolio history is a ServerDataService data type.

``condor/web/routes/portfolio.py`` used to hold module-level polling state
(``_history_cache``/``_refresh_tasks``/``_last_request_time``) and hand-roll the
whole subscription lifecycle — prime, 120s refresh loop, idle timeout,
per-range TTL — while ``ws_manager`` imported that REST route module to start
and stop it. That is what SDS already does, minus the single-flight coalescing
the hand-rolled cache lacked.

The three things a silent regression would break, and that these tests pin:
the endpoint still serves the same payload, the poll really starts when a
portfolio WS subscriber appears and really stops when the last one leaves, and
a second subscriber never spawns a duplicate poll.
"""

import asyncio
from pathlib import Path

import pytest

from condor.fetchers.portfolio import (
    PORTFOLIO_HISTORY_INTERVAL,
    PORTFOLIO_HISTORY_RANGES,
    fetch_portfolio_history,
)
from condor.server_data_service import (
    _DEFAULTS,
    CacheKey,
    ServerDataService,
    ServerDataType,
)
from condor.web.models import WebUser
from condor.web.routes.portfolio import get_portfolio_history
from condor.web.ws_manager import WebSocketManager, _Connection

_USER = WebUser(id=1, role="admin")

# Two snapshots of one account: BTC 100 -> 120, ETH 50 -> 40.
_HISTORY = [
    {
        "timestamp": 1000,
        "state": {
            "master": {
                "binance": [
                    {"token": "BTC", "value": 100.0},
                    {"token": "ETH", "value": 50.0},
                ]
            }
        },
    },
    {
        "timestamp": 2000,
        "state": {
            "master": {
                "binance": [
                    {"token": "BTC", "value": 120.0},
                    {"token": "ETH", "value": 40.0},
                ]
            }
        },
    },
]


class _FakePortfolio:
    def __init__(self, client):
        self._client = client

    async def get_history(self, **kwargs):
        self._client.history_calls.append(kwargs)
        if self._client.delay:
            await asyncio.sleep(self._client.delay)
        return _HISTORY

    async def get_state(self, **kwargs):
        self._client.state_calls.append(kwargs)
        return {"master": {"binance": []}}


class FakeClient:
    """Hummingbot client recording every history request."""

    def __init__(self, delay: float = 0.0):
        self.history_calls: list[dict] = []
        self.state_calls: list[dict] = []
        self.delay = delay
        self.portfolio = _FakePortfolio(self)


class _FakeCM:
    def has_server_access(self, user_id, name, *a, **kw):
        return True


def _make_sds(client: FakeClient) -> ServerDataService:
    """Fresh (non-singleton) SDS wired to a fake client, history registered."""
    sds = ServerDataService()

    async def _fake_get_client(server_name):
        return client

    sds._get_client = _fake_get_client
    sds.register_fetch(ServerDataType.PORTFOLIO_HISTORY, fetch_portfolio_history)
    from condor.fetchers.portfolio import fetch_portfolio

    sds.register_fetch(ServerDataType.PORTFOLIO, fetch_portfolio)
    return sds


@pytest.fixture
def env(monkeypatch):
    """Bind the REST route and the WS manager to one fresh SDS + fake client."""

    def _bind(delay: float = 0.0):
        client = FakeClient(delay=delay)
        sds = _make_sds(client)
        monkeypatch.setattr(
            "condor.server_data_service.get_server_data_service", lambda: sds
        )
        monkeypatch.setattr(
            "condor.web.routes.portfolio.get_config_manager", lambda: _FakeCM()
        )
        return sds, client

    return _bind


# ── the payload the dashboard receives ──


def test_endpoint_serves_the_same_payload_through_sds(env):
    """Totals, timestamps and interval are unchanged by the migration."""
    sds, client = env()

    resp = asyncio.run(get_portfolio_history(name="srv", range="1D", user=_USER))

    assert resp.server == "srv"
    assert resp.interval == "5m"
    assert [(p.timestamp, p.total_usd) for p in resp.points] == [
        (1000.0, 150.0),
        (2000.0, 160.0),
    ]
    assert len(client.history_calls) == 1
    assert client.history_calls[0]["interval"] == "5m"
    assert client.history_calls[0]["limit"] == 500


def test_endpoint_still_builds_the_token_breakdown(env):
    """``breakdown=true`` keeps returning per-token values and top tokens."""
    sds, client = env()

    resp = asyncio.run(
        get_portfolio_history(name="srv", range="1D", breakdown=True, user=_USER)
    )

    assert resp.top_tokens == ["BTC", "ETH"]
    assert [p.tokens for p in resp.points] == [
        {"BTC": 100.0, "ETH": 50.0},
        {"BTC": 120.0, "ETH": 40.0},
    ]


def test_second_read_of_a_fresh_range_is_served_from_cache(env):
    """The cache still absorbs repeat reads — one backend call for two reads."""
    sds, client = env()

    async def _drive():
        await get_portfolio_history(name="srv", range="1W", user=_USER)
        return await get_portfolio_history(name="srv", range="1W", user=_USER)

    resp = asyncio.run(_drive())

    assert resp.interval == "1h"
    assert len(client.history_calls) == 1


def test_concurrent_expired_reads_coalesce_to_one_fetch(env):
    """The gap the hand-rolled TTL cache had: two concurrent cold reads of the
    same range must issue one backend request, not two."""
    sds, client = env(delay=0.05)

    async def _drive():
        return await asyncio.gather(
            get_portfolio_history(name="srv", range="1M", user=_USER),
            get_portfolio_history(name="srv", range="1M", user=_USER),
        )

    first, second = asyncio.run(_drive())

    assert len(client.history_calls) == 1, "concurrent cold reads must coalesce"
    assert first.points == second.points


def test_each_range_is_cached_separately(env):
    """Ranges are distinct cache keys — switching range refetches."""
    sds, client = env()

    async def _drive():
        for range_key in PORTFOLIO_HISTORY_RANGES:
            await get_portfolio_history(name="srv", range=range_key, user=_USER)

    asyncio.run(_drive())

    assert len(client.history_calls) == len(PORTFOLIO_HISTORY_RANGES)
    assert {c["interval"] for c in client.history_calls} == {
        interval for _, interval in PORTFOLIO_HISTORY_RANGES.values()
    }


def test_per_range_ttls_survived_the_move():
    """The old _RANGE_TTLS table now lives on the data type's defaults."""
    defaults = _DEFAULTS[ServerDataType.PORTFOLIO_HISTORY]

    assert defaults.interval == PORTFOLIO_HISTORY_INTERVAL
    assert {
        rk: defaults.ttl_for({"range_key": rk}) for rk in PORTFOLIO_HISTORY_RANGES
    } == {"1D": 120, "1W": 600, "1M": 3600, "3M": 7200}


# ── the lifecycle ws_manager drives ──


def _history_keys(server: str) -> list[CacheKey]:
    return [
        CacheKey.make(server, ServerDataType.PORTFOLIO_HISTORY, range_key=rk)
        for rk in PORTFOLIO_HISTORY_RANGES
    ]


async def _drain(manager: WebSocketManager) -> None:
    """Await the one-shot tasks the manager currently holds."""
    await asyncio.gather(*list(manager._oneshot_tasks), return_exceptions=True)
    await asyncio.sleep(0)


def _expire(sds: ServerDataService) -> None:
    """Age every cache entry past its interval so the next tick is due."""
    for entry in sds._cache.values():
        entry.fetched_at = 0.0


def test_poll_starts_with_the_portfolio_subscriber_and_stops_with_it(env):
    """A silently-never-started poll is a dead chart no shape test would catch:
    drive real poll ticks before and after the subscriber leaves."""
    sds, client = env()

    async def _drive():
        manager = WebSocketManager()
        conn = _Connection(_FakeWS(), user_id=1)
        conn.channels.add("portfolio:srv")
        manager._connections.append(conn)

        await manager._subscribe_sds("portfolio:srv")
        await _drain(manager)
        subscribed = len(sds._subscriptions)
        primed = len(client.history_calls)

        # The poll runs while the subscriber is there.
        _expire(sds)
        await sds._poll_tick()
        polled = len(client.history_calls)

        # Last subscriber leaves.
        conn.channels.discard("portfolio:srv")
        manager._maybe_unsub_sds("portfolio:srv")
        after_unsub = len(client.history_calls)

        _expire(sds)
        await sds._poll_tick()
        return subscribed, primed, polled, after_unsub, len(client.history_calls), sds

    subscribed, primed, polled, after_unsub, final, sds = asyncio.run(_drive())

    # One key per range, plus the PORTFOLIO subscription itself.
    assert subscribed == len(PORTFOLIO_HISTORY_RANGES) + 1
    assert primed == len(PORTFOLIO_HISTORY_RANGES), "every range primed on subscribe"
    assert polled == primed + len(PORTFOLIO_HISTORY_RANGES), "poll must be running"
    assert final == after_unsub, "poll must stop once the last subscriber leaves"
    assert not any(k in sds._subscriptions for k in _history_keys("srv"))


def test_history_subscriptions_use_the_previous_refresh_cadence(env):
    """The loop re-warmed every range every 120s; the subscriptions must too."""
    sds, _client = env()

    async def _drive():
        manager = WebSocketManager()
        await manager._subscribe_sds("portfolio:srv")
        await _drain(manager)
        return {
            key: [s.interval for s in subs.values()]
            for key, subs in sds._subscriptions.items()
            if key.data_type is ServerDataType.PORTFOLIO_HISTORY
        }

    intervals = asyncio.run(_drive())

    assert len(intervals) == len(PORTFOLIO_HISTORY_RANGES)
    assert all(iv == [PORTFOLIO_HISTORY_INTERVAL] for iv in intervals.values())


def test_second_subscriber_does_not_spawn_a_duplicate_poll(env):
    """Two clients on the same channel share one poll per range."""
    sds, client = env()

    async def _drive():
        manager = WebSocketManager()
        for user_id in (1, 2):
            conn = _Connection(_FakeWS(), user_id=user_id)
            conn.channels.add("portfolio:srv")
            manager._connections.append(conn)
            await manager._subscribe_sds("portfolio:srv")
            await _drain(manager)

        _expire(sds)
        before = len(client.history_calls)
        await sds._poll_tick()
        return before, len(client.history_calls) - before, sds

    primed, per_tick, sds = asyncio.run(_drive())

    assert primed == len(PORTFOLIO_HISTORY_RANGES), "no second priming round"
    assert per_tick == len(PORTFOLIO_HISTORY_RANGES), "one fetch per range per tick"
    for key in _history_keys("srv"):
        assert list(sds._subscriptions[key]) == ["ws_manager"]


def test_first_client_leaving_keeps_the_poll_alive_for_the_second(env):
    """Only the *last* subscriber leaving stops the history poll."""
    sds, _client = env()

    async def _drive():
        manager = WebSocketManager()
        conns = []
        for user_id in (1, 2):
            conn = _Connection(_FakeWS(), user_id=user_id)
            conn.channels.add("portfolio:srv")
            manager._connections.append(conn)
            conns.append(conn)
        await manager._subscribe_sds("portfolio:srv")
        await _drain(manager)

        manager.disconnect(conns[0])
        return all(k in sds._subscriptions for k in _history_keys("srv"))

    assert asyncio.run(_drive()) is True


def test_ws_manager_does_not_import_the_rest_route_module():
    """The coupling this item removed must not creep back in."""
    source = Path("condor/web/ws_manager.py").read_text()

    assert "routes.portfolio" not in source
    assert "routes import portfolio" not in source


def test_route_module_holds_no_polling_state():
    """No module-level cache, task dict or refresh loop left in the route."""
    import condor.web.routes.portfolio as route

    for leftover in (
        "_history_cache",
        "_refresh_tasks",
        "_last_request_time",
        "_refresh_loop",
        "warm_portfolio_history",
        "stop_history_refresh",
    ):
        assert not hasattr(route, leftover), f"{leftover} survived the migration"


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
