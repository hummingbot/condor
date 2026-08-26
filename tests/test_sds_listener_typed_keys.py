"""Tests for ARCH-155: the SDS listener protocol speaks typed CacheKeys.

``_notify_listeners`` used to flatten each written key into a DataManager-era
string (``cex_prices:{connector}:{pair}``, ``cex_balances:{account}``,
``bots_status``, ``executors``) that its single listener — ws_manager's
``_on_data_update`` — split back apart to rebuild a *different* prefix. The key
already carries server, data type and params; the listener now receives it
whole, so a parameterized data type is addressable without string munging in
two files kept in sync by convention.

What a regression would break, and what these pin: the key survives the hop
byte-for-byte (params included, e.g. a portfolio-history range), a key that no
channel carries is dropped deliberately rather than misrouted onto a sibling
channel, and no per-data-type string synthesis creeps back into SDS.
"""

import asyncio
import inspect
import json

from condor.server_data_service import (
    CacheKey,
    ServerDataService,
    ServerDataType,
    get_server_data_service,
)
from condor.web.ws_manager import WebSocketManager, _Connection, channel_for_key


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        # The manager encodes each frame once per broadcast and fans out text
        # (PERF-210); decode here so assertions stay in terms of the payload.
        self.sent.append(json.loads(raw))


def _manager_with_subscriber(channel: str) -> tuple[WebSocketManager, _FakeWS]:
    manager = WebSocketManager()
    ws = _FakeWS()
    conn = _Connection(ws, user_id=1)
    conn.channels.add(channel)
    manager._connections.append(conn)
    return manager, ws


async def _drain(manager: WebSocketManager) -> None:
    await asyncio.gather(*list(manager._oneshot_tasks), return_exceptions=True)
    await asyncio.sleep(0)


def _capture(sds: ServerDataService) -> list[tuple]:
    seen: list[tuple] = []
    sds.add_listener(lambda key, value: seen.append((key, value)))
    return seen


def test_typed_key_round_trips_from_cache_write_to_listener():
    """The listener gets the key object itself — nothing is stringified."""
    sds = ServerDataService()
    seen = _capture(sds)

    sds.put(
        "srv",
        ServerDataType.PRICES,
        1.23,
        connector_name="binance",
        trading_pair="BTC-USDT",
    )

    assert len(seen) == 1
    key, value = seen[0]
    assert isinstance(key, CacheKey)
    assert key == CacheKey.make(
        "srv",
        ServerDataType.PRICES,
        connector_name="binance",
        trading_pair="BTC-USDT",
    )
    assert key.server == "srv"
    assert key.data_type is ServerDataType.PRICES
    assert value == 1.23
    # And the channel is derived from the key's params, not re-parsed from a string.
    assert channel_for_key(key) == "prices:srv:binance:BTC-USDT"


def test_parameterized_history_key_survives_the_listener_hop_intact():
    """A portfolio-history key keeps its range_key param end to end (ARCH-145)."""
    sds = ServerDataService()
    seen = _capture(sds)

    sds.put("srv", ServerDataType.PORTFOLIO_HISTORY, [{"v": 1}], range_key="1d")
    sds.put("srv", ServerDataType.PORTFOLIO_HISTORY, [{"v": 2}], range_key="3m")

    assert [k.params_dict for k, _ in seen] == [
        {"range_key": "1d"},
        {"range_key": "3m"},
    ]
    assert [v for _, v in seen] == [[{"v": 1}], [{"v": 2}]]
    # No WS channel streams history: it is served from the SDS cache over REST.
    assert [channel_for_key(k) for k, _ in seen] == [None, None]


def test_history_write_is_not_broadcast_onto_the_portfolio_channel():
    """The parameterized key must not collapse onto its data type's neighbours."""
    manager, ws = _manager_with_subscriber("portfolio:srv")

    async def scenario():
        manager._on_data_update(
            CacheKey.make("srv", ServerDataType.PORTFOLIO_HISTORY, range_key="1d"),
            [{"v": 1}],
        )
        await _drain(manager)

    asyncio.run(scenario())

    assert ws.sent == [], "portfolio history leaked onto the portfolio channel"
    assert len(manager._oneshot_tasks) == 0


def test_account_scoped_portfolio_is_not_broadcast_to_the_server_channel():
    """`portfolio:{server}` is the whole-server portfolio; an account-scoped
    write (Telegram's per-account balances) is a different dataset."""
    manager, ws = _manager_with_subscriber("portfolio:srv")

    async def scenario():
        manager._on_data_update(
            CacheKey.make("srv", ServerDataType.PORTFOLIO, account_name="master"),
            {"total": 999},
        )
        manager._on_data_update(
            CacheKey.make("srv", ServerDataType.PORTFOLIO), {"total": 1}
        )
        await _drain(manager)

    asyncio.run(scenario())

    assert [m["data"] for m in ws.sent] == [{"total": 1}]


def test_key_without_a_channel_is_dropped_deliberately_not_misrouted():
    """Data types the dashboard does not stream resolve to no channel at all."""
    for data_type in (
        ServerDataType.EXECUTORS,
        ServerDataType.POSITIONS,
        ServerDataType.TICKER_POOL,
    ):
        assert channel_for_key(CacheKey.make("srv", data_type)) is None

    # A price key missing the params that address its channel is not guessed at.
    assert channel_for_key(CacheKey.make("srv", ServerDataType.PRICES)) is None
    assert (
        channel_for_key(
            CacheKey.make("srv", ServerDataType.PRICES, connector_name="binance")
        )
        is None
    )


def test_listener_registration_survives_a_failing_listener():
    """One listener raising must not stop the others (or the cache write)."""
    sds = ServerDataService()
    sds.add_listener(lambda key, value: (_ for _ in ()).throw(RuntimeError("boom")))
    seen = _capture(sds)

    sds.put("srv", ServerDataType.BOTS_STATUS, {"bots": []})

    assert [k.data_type for k, _ in seen] == [ServerDataType.BOTS_STATUS]
    assert sds.get("srv", ServerDataType.BOTS_STATUS) == {"bots": []}


def test_sds_synthesizes_no_legacy_cache_key_strings():
    """Guards the regression: the DataManager-era key strings are gone for good."""
    import condor.server_data_service as sds_module

    source = inspect.getsource(sds_module)
    assert "cex_prices" not in source
    assert "cex_balances" not in source

    notify = inspect.getsource(sds_module.ServerDataService._notify_listeners)
    assert 'f"' not in notify, "per-data-type string formatting is back"


def test_ws_manager_listener_registers_and_unregisters_on_the_live_service():
    """The one registration site wires up — and unwires — against the singleton.

    ``_on_data_update`` is a bound method: every attribute access builds a new
    object, so removal has to compare by equality or ``stop()`` leaks a dead
    manager into the singleton's listener list.
    """
    manager = WebSocketManager()
    sds = get_server_data_service()
    before = len(sds._listeners)

    manager._sds_listener_registered = True  # skip the candle cleanup task
    sds.add_listener(manager._on_data_update)
    try:
        assert len(sds._listeners) == before + 1
        sig = inspect.signature(manager._on_data_update)
        assert list(sig.parameters) == ["key", "value"]
    finally:
        sds.remove_listener(manager._on_data_update)
    assert len(sds._listeners) == before
