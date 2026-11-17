"""Shutdown is bounded and step-isolated (OP-04, WS-06).

``teardown()`` was one straight run of awaits: an exception out of an early
step skipped the engine stop, both state flushes and every client close, and a
step that never returned held the process open until it was killed.
"""

import asyncio
import sys
import types

import pytest

import main


def test_uvicorn_does_not_wait_for_in_flight_requests_without_limit():
    """An SSE agent turn only ends when its session does, which is *after* the drain."""
    config = main._web_server_config(object())
    assert config.timeout_graceful_shutdown == main.WEB_GRACEFUL_SHUTDOWN_SEC
    assert 0 < config.timeout_graceful_shutdown <= 30


@pytest.fixture
def stages(monkeypatch):
    """Stand-ins for everything teardown() touches, recording what ran."""
    ran: list[str] = []

    def record(name):
        def sync(*args, **kwargs):
            ran.append(name)

        async def coro(*args, **kwargs):
            ran.append(name)

        return sync, coro

    from condor import telemetry
    from condor.runtime import client as runtime
    from condor.runtime import confirmations, conversations, loops
    from condor.runtime import sessions as runtime_sessions
    from condor.runtime import state

    monkeypatch.setattr(runtime_sessions, "stop_health_monitor", record("health")[1])
    monkeypatch.setattr(
        confirmations,
        "get_registry",
        lambda: types.SimpleNamespace(stop=record("confirmations")[1]),
    )
    monkeypatch.setattr(runtime, "destroy_all", record("sessions")[1])
    monkeypatch.setattr(
        loops,
        "get_supervisor",
        lambda: types.SimpleNamespace(stop_all=record("engines")[1]),
    )
    monkeypatch.setattr(state, "flush_all", record("state-flush")[0])
    monkeypatch.setattr(conversations, "flush_all", record("conversation-flush")[0])

    import condor.server_data_service as sds
    import condor.web.ws_manager as ws_manager
    import config_manager

    monkeypatch.setattr(
        ws_manager,
        "get_ws_manager",
        lambda: types.SimpleNamespace(stop=record("ws")[0]),
    )
    monkeypatch.setattr(
        sds,
        "get_server_data_service",
        lambda: types.SimpleNamespace(stop=record("sds")[0]),
    )
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: types.SimpleNamespace(close_all_clients=record("api-clients")[1]),
    )
    fake_hb = types.ModuleType("mcp_servers.hummingbot_api.hummingbot_client")
    fake_hb.hummingbot_client = types.SimpleNamespace(close=record("mcp-client")[1])
    monkeypatch.setitem(
        sys.modules, "mcp_servers.hummingbot_api.hummingbot_client", fake_hb
    )
    monkeypatch.setattr(telemetry, "shutdown", record("telemetry-mark")[0])
    monkeypatch.setattr(telemetry, "flush", record("telemetry-flush")[1])
    return ran


ALL_STEPS = [
    "health",
    "confirmations",
    "sessions",
    "engines",
    "state-flush",
    "conversation-flush",
    "ws",
    "sds",
    "api-clients",
    "mcp-client",
    "telemetry-mark",
    "telemetry-flush",
]


def test_teardown_runs_every_step_in_order(stages):
    asyncio.run(main.teardown(object()))
    assert stages == ALL_STEPS


def test_a_failing_step_does_not_skip_the_engine_stop_or_the_flushes(
    stages, monkeypatch
):
    from condor.runtime import client as runtime

    async def boom():
        raise RuntimeError("destroy_all blew up")

    monkeypatch.setattr(runtime, "destroy_all", boom)
    asyncio.run(main.teardown(object()))

    assert stages == [s for s in ALL_STEPS if s != "sessions"]


def test_a_failing_sync_step_does_not_skip_the_rest(stages, monkeypatch):
    from condor.runtime import state

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(state, "flush_all", boom)
    asyncio.run(main.teardown(object()))

    assert stages == [s for s in ALL_STEPS if s != "state-flush"]


def test_a_step_that_never_returns_is_abandoned_within_its_budget(stages, monkeypatch):
    from condor.runtime import client as runtime

    cancelled = []

    async def hang():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(runtime, "destroy_all", hang)
    monkeypatch.setattr(main, "TEARDOWN_SESSIONS_TIMEOUT", 0.05)

    async def scenario():
        await asyncio.wait_for(main.teardown(object()), timeout=5)

    asyncio.run(scenario())

    assert cancelled == [True]
    assert stages == [s for s in ALL_STEPS if s != "sessions"]


def test_the_engine_stop_is_given_a_whole_wind_down_not_the_short_ceiling():
    """stop_all waits out an emergency wind-down; cutting it short strands positions."""
    from condor.runtime.timeouts import TIMEOUTS

    assert main._teardown_engines_timeout() >= TIMEOUTS.tick_default
    assert main._teardown_engines_timeout() > main.TEARDOWN_STEP_TIMEOUT
