"""Regression tests for the hummingbot MCP tool wrappers.

The case here is a shape mismatch against the Hummingbot API that failed silently
rather than loudly, so it survived manual use for a long time: every controller
upload was rejected with 422, because the source string was sent where the API
expects a Controller object.

This file also pinned the backtesting tools, whose bug was the same in kind (a
finished async task rendering as a bare "status=completed"). Those tools are gone
— FEAT-039 made the ``backtest_chart`` routine the only backtesting surface — and
the coverage moved with them, to test_backtest_one_surface.py, which asserts the
stored envelope's shape rather than a formatter's output.

The repo has no async test setup, so the coroutines are driven with
asyncio.run() instead of a pytest-asyncio marker.
"""

import asyncio
import time

import pytest

from mcp_servers.hummingbot_api.tools.controllers import modify_controllers
from mcp_servers.hummingbot_api.tools.portfolio import (
    MAX_CONCURRENT_POOL_FETCHES,
    get_portfolio_overview,
)


class FakeControllers:
    def __init__(self):
        self.uploaded = None

    async def list_controllers(self):
        return {"directional_trading": []}

    async def create_or_update_controller(
        self, controller_type, controller_name, controller_data
    ):
        self.uploaded = (controller_type, controller_name, controller_data)
        return {"message": "saved"}


class FakeControllerClient:
    def __init__(self):
        self.controllers = FakeControllers()


def test_controller_upload_sends_a_controller_object_not_a_bare_string():
    """POST /controllers/{type}/{name} takes {"content": ...}; a raw string is a 422."""
    client = FakeControllerClient()
    asyncio.run(
        modify_controllers(
            client,
            action="upsert",
            target="controller",
            controller_type="directional_trading",
            controller_name="ema_trend_v1",
            controller_code="class EmaTrendV1Config: pass",
        )
    )

    _, _, body = client.controllers.uploaded
    assert isinstance(body, dict), "the API rejects a bare source string with 422"
    assert body["content"] == "class EmaTrendV1Config: pass"
    assert body["type"] == "directional_trading"


def test_the_backtesting_tools_are_gone():
    """One surface: an agent backtests through the routine, not a second tool."""
    import mcp_servers.hummingbot_api.server as server

    assert not hasattr(server, "run_backtest")
    assert not hasattr(server, "manage_backtest_tasks")

    with pytest.raises(ImportError):
        import mcp_servers.hummingbot_api.tools.backtesting  # noqa: F401


POOL_LATENCY = 0.05


class FakeGatewayClmm:
    """CLMM surface where each pool read costs POOL_LATENCY, as an on-chain read does.

    ``failing_pool`` raises the way a dead RPC does, so the skip-and-continue
    behaviour of the serial loop stays pinned.
    """

    def __init__(self, pools, failing_pool=None):
        self.pools = pools
        self.failing_pool = failing_pool
        self.max_in_flight = 0
        self._in_flight = 0

    async def search_positions(self, limit, offset, status):
        return {
            "data": [
                {"connector": c, "network": n, "pool_address": p}
                for (c, n, p) in self.pools
            ]
        }

    async def get_positions_owned(
        self, connector, network, pool_address, wallet_address=None
    ):
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(POOL_LATENCY)
            if pool_address == self.failing_pool:
                raise RuntimeError("gateway RPC down")
            # No connector/network of its own: the tool stamps them per pool.
            return [
                {
                    "trading_pair": f"PAIR-{pool_address}",
                    "position_address": pool_address,
                }
            ]
        finally:
            self._in_flight -= 1


class FakeGatewayClient:
    def __init__(self, gateway_clmm):
        self.gateway_clmm = gateway_clmm


def _lp_overview(client):
    return asyncio.run(
        get_portfolio_overview(
            client,
            include_balances=False,
            include_perp_positions=False,
            include_lp_positions=True,
            include_active_orders=False,
        )
    )


def test_clmm_pools_are_fetched_concurrently_not_one_at_a_time():
    """K on-chain pool reads must cost ~1 round-trip, not K of them stacked."""
    pools = [("meteora", "mainnet-beta", f"pool{i}") for i in range(8)]
    clmm = FakeGatewayClmm(pools)

    start = time.monotonic()
    result = _lp_overview(FakeGatewayClient(clmm))
    elapsed = time.monotonic() - start

    serial = len(pools) * POOL_LATENCY
    assert (
        elapsed < serial / 2
    ), f"{len(pools)} pools took {elapsed:.3f}s; serial would be ~{serial:.3f}s"

    section = next(s for s in result["sections"] if s["title"] == "LP Positions (CLMM)")
    assert section["total_positions"] == len(pools)


def test_one_failing_pool_is_skipped_and_the_rest_still_return():
    """A dead pool degrades exactly as it did serially: logged, skipped, no propagation."""
    pools = [("meteora", "mainnet-beta", f"pool{i}") for i in range(4)]
    clmm = FakeGatewayClmm(pools, failing_pool="pool2")

    result = _lp_overview(FakeGatewayClient(clmm))

    section = next(s for s in result["sections"] if s["title"] == "LP Positions (CLMM)")
    assert section["total_positions"] == 3
    assert "pool2" not in section["content"]


def test_each_position_keeps_its_own_pools_connector_and_network():
    """The fan-out must not cross-stamp: position N carries pool N's connector."""
    pools = [
        ("meteora", "mainnet-beta", "poolA"),
        ("raydium", "devnet", "poolB"),
        ("uniswap", "base", "poolC"),
    ]
    clmm = FakeGatewayClmm(pools)

    result = _lp_overview(FakeGatewayClient(clmm))

    section = next(s for s in result["sections"] if s["title"] == "LP Positions (CLMM)")
    for connector, _network, pool_address in pools:
        line = next(
            ln for ln in section["content"].splitlines() if f"PAIR-{pool_address}" in ln
        )
        assert line.startswith(
            connector
        ), f"{pool_address} was stamped with the wrong connector"


def test_pool_fan_out_is_bounded_by_an_explicit_constant():
    """A many-pool account must not burst unlimited simultaneous Gateway requests."""
    pools = [
        ("meteora", "mainnet-beta", f"pool{i}")
        for i in range(MAX_CONCURRENT_POOL_FETCHES * 3)
    ]
    clmm = FakeGatewayClmm(pools)

    _lp_overview(FakeGatewayClient(clmm))

    assert clmm.max_in_flight <= MAX_CONCURRENT_POOL_FETCHES
    assert clmm.max_in_flight > 1, "the reads should still overlap"


def test_lp_branch_is_skipped_when_not_requested():
    """include_lp_positions=False must not touch Gateway at all."""
    clmm = FakeGatewayClmm([("meteora", "mainnet-beta", "poolA")])

    result = asyncio.run(
        get_portfolio_overview(
            FakeGatewayClient(clmm),
            include_balances=False,
            include_perp_positions=False,
            include_lp_positions=False,
            include_active_orders=False,
        )
    )

    assert clmm.max_in_flight == 0
    assert not any(s["title"] == "LP Positions (CLMM)" for s in result["sections"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
