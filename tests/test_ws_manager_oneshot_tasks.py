"""SDS-driven dashboard broadcasts are tracked one-shot tasks (CORR-107).

`_on_data_update` is the hot path: every successful SDS poll fans out here for
the portfolio, prices and bots channels. Dispatching it with a bare
`ensure_future` left the task referenced only weakly by the event loop (it could
be collected mid-await, silently dropping a dashboard tick) and swallowed any
failure into asyncio's "Task exception was never retrieved" at GC time, so a
stalled channel left no trace on this module's logger.
"""

import asyncio
import inspect
import logging

from condor.server_data_service import CacheKey, ServerDataType
from condor.web.ws_manager import WebSocketManager, _Connection

WS_LOGGER = "condor.web.ws_manager"


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _key(data_type: str, server: str = "srv", **params) -> CacheKey:
    """The typed key SDS hands its listeners on every cache write."""
    return CacheKey.make(server, ServerDataType[data_type], **params)


def _manager_with_subscriber(channel: str) -> tuple[WebSocketManager, _FakeWS]:
    manager = WebSocketManager()
    ws = _FakeWS()
    conn = _Connection(ws, user_id=1)
    conn.channels.add(channel)
    manager._connections.append(conn)
    return manager, ws


async def _drain(manager: WebSocketManager) -> None:
    """Await whatever one-shot tasks the manager currently holds."""
    await asyncio.gather(*list(manager._oneshot_tasks), return_exceptions=True)
    # Done-callbacks are scheduled via call_soon; give the loop a turn to run them.
    await asyncio.sleep(0)


def test_sds_update_holds_a_strong_reference_until_the_broadcast_finishes():
    """The dispatched broadcast must be reachable from the manager, not only
    from the event loop's weak reference."""
    manager, ws = _manager_with_subscriber("portfolio:srv")

    async def scenario():
        manager._on_data_update(_key("PORTFOLIO"), {"total": 1})
        assert len(manager._oneshot_tasks) == 1, "broadcast task was not tracked"
        name = next(iter(manager._oneshot_tasks)).get_name()
        await _drain(manager)
        return name

    assert asyncio.run(scenario()) == "broadcast:portfolio:srv"
    assert [m["data"] for m in ws.sent] == [{"total": 1}]


def test_reference_is_released_across_repeated_polls():
    """`_oneshot_tasks` must not grow poll after poll."""
    manager, _ = _manager_with_subscriber("portfolio:srv")

    async def scenario():
        for i in range(5):
            manager._on_data_update(_key("PORTFOLIO"), {"total": i})
            await _drain(manager)

    asyncio.run(scenario())

    assert manager._oneshot_tasks == set()


def test_failing_broadcast_is_logged_on_this_modules_logger(caplog):
    """A crash in the fan-out must name the channel on the ws_manager logger,
    not just asyncio's "never retrieved" warning at GC time."""
    manager, _ = _manager_with_subscriber("bots:srv")

    async def boom(channel, data):
        raise RuntimeError("downstream exploded")

    manager._broadcast_update = boom

    async def scenario():
        manager._on_data_update(_key("BOTS_STATUS"), {})
        await _drain(manager)

    with caplog.at_level(logging.ERROR, logger=WS_LOGGER):
        asyncio.run(scenario())

    errors = [r for r in caplog.records if r.name == WS_LOGGER]
    assert errors, "a failing broadcast produced no ERROR on the ws_manager logger"
    assert "bots:srv" in errors[0].getMessage()
    assert "downstream exploded" in errors[0].getMessage()
    assert errors[0].exc_info is not None, "traceback was not attached"


def test_cancelled_oneshot_is_not_logged_as_an_error(caplog):
    """Shutdown cancels pending one-shots; that is not a failure."""
    manager, _ = _manager_with_subscriber("portfolio:srv")

    started = asyncio.Event()

    async def never_finishes(channel, data):
        started.set()
        await asyncio.Event().wait()

    manager._broadcast_update = never_finishes

    async def scenario():
        manager._on_data_update(_key("PORTFOLIO"), {})
        await started.wait()
        task = next(iter(manager._oneshot_tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=WS_LOGGER):
        asyncio.run(scenario())

    assert [r for r in caplog.records if r.name == WS_LOGGER] == []
    assert manager._oneshot_tasks == set()


def test_no_untracked_fire_and_forget_dispatch_remains():
    """Guards the regression: `ensure_future` was how this site escaped
    CORR-021's sweep of `create_task` call sites."""
    from condor.web import ws_manager

    assert "ensure_future" not in inspect.getsource(ws_manager)
