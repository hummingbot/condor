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
    MAX_CONCURRENT_POSITION_FETCHES,
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
    """CLMM surface where each venue read costs POOL_LATENCY, as an on-chain read does.

    ``get_positions_owned`` mirrors the real SDK signature exactly — connector,
    network, wallet_address, and NO pool_address. Gateway's positions-owned has
    no pool filter; every returned row carries its own ``pool_address``. A fake
    that accepted one hid a live TypeError at two call sites.

    ``failing_venue`` raises the way a dead RPC does, so the skip-and-continue
    behaviour stays pinned.
    """

    def __init__(self, pools, failing_venue=None):
        self.pools = pools
        self.failing_venue = failing_venue
        self.max_in_flight = 0
        self._in_flight = 0

    async def search_positions(self, limit, offset, status):
        return {
            "data": [
                {"connector": c, "network": n, "pool_address": p}
                for (c, n, p) in self.pools
            ]
        }

    async def get_positions_owned(self, connector, network, wallet_address=None):
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(POOL_LATENCY)
            if (connector, network) == self.failing_venue:
                raise RuntimeError("gateway RPC down")
            # No connector/network of its own: the tool stamps them per venue.
            return [
                {
                    "trading_pair": f"PAIR-{pool_address}",
                    "position_address": pool_address,
                    "pool_address": pool_address,
                }
                for (c, n, pool_address) in self.pools
                if (c, n) == (connector, network)
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


def test_clmm_venues_are_fetched_concurrently_not_one_at_a_time():
    """K on-chain venue reads must cost ~1 round-trip, not K of them stacked."""
    pools = [("meteora", f"net{i}", f"pool{i}") for i in range(8)]
    clmm = FakeGatewayClmm(pools)

    start = time.monotonic()
    result = _lp_overview(FakeGatewayClient(clmm))
    elapsed = time.monotonic() - start

    serial = len(pools) * POOL_LATENCY
    assert (
        elapsed < serial / 2
    ), f"{len(pools)} venues took {elapsed:.3f}s; serial would be ~{serial:.3f}s"

    section = next(s for s in result["sections"] if s["title"] == "LP Positions (CLMM)")
    assert section["total_positions"] == len(pools)


def test_one_failing_venue_is_skipped_and_the_rest_still_return():
    """A dead venue degrades gracefully: logged, skipped, no propagation."""
    pools = [("meteora", f"net{i}", f"pool{i}") for i in range(4)]
    clmm = FakeGatewayClmm(pools, failing_venue=("meteora", "net2"))

    result = _lp_overview(FakeGatewayClient(clmm))

    section = next(s for s in result["sections"] if s["title"] == "LP Positions (CLMM)")
    assert section["total_positions"] == 3
    assert "pool2" not in section["content"]


def test_one_venue_with_many_pools_is_read_once_and_not_duplicated():
    """positions-owned takes no pool filter: querying per pool would repeat the
    same list once per pool. One call per venue, each position exactly once."""
    pools = [("meteora", "mainnet-beta", f"pool{i}") for i in range(5)]
    clmm = FakeGatewayClmm(pools)

    result = _lp_overview(FakeGatewayClient(clmm))

    assert clmm.max_in_flight == 1, "five pools on one venue is one Gateway read"
    section = next(s for s in result["sections"] if s["title"] == "LP Positions (CLMM)")
    assert section["total_positions"] == len(pools)
    for _, _, pool_address in pools:
        assert section["content"].count(f"PAIR-{pool_address}") == 1


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


def test_position_fan_out_is_bounded_by_an_explicit_constant():
    """A many-venue account must not burst unlimited simultaneous Gateway requests."""
    pools = [
        ("meteora", f"net{i}", f"pool{i}")
        for i in range(MAX_CONCURRENT_POSITION_FETCHES * 3)
    ]
    clmm = FakeGatewayClmm(pools)

    _lp_overview(FakeGatewayClient(clmm))

    assert clmm.max_in_flight <= MAX_CONCURRENT_POSITION_FETCHES
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


# ---------------------------------------------------------------------------
# onchain_executor through manage_executors: the MCP layer must forward the
# config to the API untouched (it is the API that knows the executor), reject
# fields the schema does not list, and make the type discoverable.
# ---------------------------------------------------------------------------

ONCHAIN_SCHEMA = {
    "properties": {
        key: {}
        for key in (
            "type",
            "controller_id",
            "chain_id",
            "mode",
            "calls",
            "notional_quote",
            "max_gas_quote",
            "app",
            "operation",
            "arguments",
            "commit",
        )
    }
}


class FakeExecutorsRouter:
    def __init__(self, schema=ONCHAIN_SCHEMA):
        self.schema = schema
        self.created = None

    async def get_executor_config_schema(self, executor_type):
        assert executor_type == "onchain_executor"
        return self.schema

    async def create_executor(self, executor_config, account_name, controller_id):
        self.created = (executor_config, account_name, controller_id)
        return {"executor_id": "onchain-0001"}


class FakeExecutorClient:
    def __init__(self):
        self.executors = FakeExecutorsRouter()


@pytest.fixture
def isolated_preferences(tmp_path, monkeypatch):
    """Point the tool at a scratch preferences file so ~/.hummingbot_mcp is untouched."""
    from mcp_servers.hummingbot_api import executor_preferences as prefs_module
    from mcp_servers.hummingbot_api.tools import executors as tool_module

    manager = prefs_module.ExecutorPreferencesManager(tmp_path / "prefs.md")
    monkeypatch.setattr(tool_module, "executor_preferences", manager)
    return manager


ONCHAIN_CONFIG = {
    "chain_id": 8453,
    "mode": "calls",
    "calls": [
        {
            "to": "0x" + "11" * 20,
            "description": "self-transfer",
            "data": {"signature": "", "args": [], "raw": ""},
            "value": "0",
        }
    ],
    "notional_quote": 1,
    "max_gas_quote": 1,
    "commit": True,
}


def test_onchain_create_forwards_the_config_unchanged_plus_type(isolated_preferences):
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors

    client = FakeExecutorClient()
    result = asyncio.run(
        manage_executors(
            client,
            ManageExecutorsRequest(
                action="create",
                executor_type="onchain_executor",
                executor_config=dict(ONCHAIN_CONFIG),
                controller_id="agent-a",
            ),
        )
    )

    assert "error" not in result, result
    config, account, controller_id = client.executors.created
    assert config == {**ONCHAIN_CONFIG, "type": "onchain_executor"}
    assert config["calls"][0]["value"] == "0"  # a wei string stays a string
    assert account == "master_account"
    assert controller_id == "agent-a"
    assert "onchain-0001" in result["formatted_output"]


def test_onchain_create_takes_controller_id_from_the_config_too(isolated_preferences):
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors

    client = FakeExecutorClient()
    asyncio.run(
        manage_executors(
            client,
            ManageExecutorsRequest(
                action="create",
                executor_type="onchain_executor",
                executor_config={**ONCHAIN_CONFIG, "controller_id": "e2e-agent"},
            ),
        )
    )

    config, _, controller_id = client.executors.created
    assert controller_id == "e2e-agent"
    assert "controller_id" not in config


def test_onchain_create_rejects_a_field_the_schema_does_not_list(isolated_preferences):
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors

    client = FakeExecutorClient()
    result = asyncio.run(
        manage_executors(
            client,
            ManageExecutorsRequest(
                action="create",
                executor_type="onchain_executor",
                executor_config={**ONCHAIN_CONFIG, "trading_pair": "ETH-USDT"},
            ),
        )
    )

    assert "Unknown field 'trading_pair'" in result["error"]
    assert client.executors.created is None


def test_list_types_mentions_onchain_executor(isolated_preferences):
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors

    result = asyncio.run(
        manage_executors(FakeExecutorClient(), ManageExecutorsRequest())
    )

    assert result["action"] == "list_types"
    assert "onchain_executor" in result["formatted_output"]
    assert "Aomi" in result["formatted_output"]


def test_onchain_guide_documents_the_chain_id(isolated_preferences):
    guide = isolated_preferences.get_executor_guide("onchain_executor")

    assert guide is not None
    assert "chain_id" in guide
    for term in ("notional_quote", "custom_info.tx_hashes", "awaiting_wallet", "8453"):
        assert term in guide, term


def test_onchain_schema_stage_prints_the_guide_and_the_schema(isolated_preferences):
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors

    result = asyncio.run(
        manage_executors(
            FakeExecutorClient(),
            ManageExecutorsRequest(executor_type="onchain_executor"),
        )
    )

    assert result["action"] == "show_schema"
    assert "### Onchain Executor" in result["formatted_output"]
    assert "chain_id" in result["formatted_output"]


def test_default_preferences_template_carries_an_onchain_section(isolated_preferences):
    from mcp_servers.hummingbot_api.executor_preferences import (
        DEFAULT_PREFERENCES_TEMPLATE,
    )

    assert "### Onchain Executor Defaults" in DEFAULT_PREFERENCES_TEMPLATE
    # Every value is commented out: the template must not silently seed a chain.
    assert isolated_preferences.get_defaults("onchain_executor") == {}


def test_server_docstring_lists_onchain_executor():
    import mcp_servers.hummingbot_api.server as server

    assert "onchain_executor" in (server.manage_executors.__doc__ or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
