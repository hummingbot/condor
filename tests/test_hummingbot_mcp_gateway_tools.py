import asyncio
from decimal import Decimal

from mcp_servers.hummingbot_api import server
from mcp_servers.hummingbot_api.schemas import GatewayCLMMManageRequest
from mcp_servers.hummingbot_api.tools.gateway_clmm import manage_gateway_clmm


class _DummyHummingbotClient:
    pass


def test_server_registers_gateway_container_with_formatter_contract(monkeypatch):
    async def get_client():
        return _DummyHummingbotClient()

    async def impl(client, request):
        assert isinstance(client, _DummyHummingbotClient)
        assert request.action == "get_status"
        return {"action": request.action, "status": {"running": True}}

    formatter_calls = []

    def formatter(result):
        formatter_calls.append(result)
        return "formatted container"

    monkeypatch.setattr(server.hummingbot_client, "get_client", get_client)
    monkeypatch.setattr(server, "manage_gateway_container_impl", impl)
    monkeypatch.setattr(server, "format_gateway_container_result", formatter)

    result = asyncio.run(server.manage_gateway_container(action="get_status"))

    assert result == "formatted container"
    assert formatter_calls == [{"action": "get_status", "status": {"running": True}}]


def test_server_registers_gateway_config_with_formatter_contract(monkeypatch):
    async def get_client():
        return _DummyHummingbotClient()

    async def impl(client, request):
        assert isinstance(client, _DummyHummingbotClient)
        assert request.resource_type == "chains"
        assert request.action == "list"
        return {
            "resource_type": request.resource_type,
            "action": request.action,
            "result": {"chains": []},
        }

    formatter_calls = []

    def formatter(result):
        formatter_calls.append(result)
        return "formatted config"

    monkeypatch.setattr(server.hummingbot_client, "get_client", get_client)
    monkeypatch.setattr(server, "manage_gateway_config_impl", impl)
    monkeypatch.setattr(server, "format_gateway_config_result", formatter)

    result = asyncio.run(
        server.manage_gateway_config(resource_type="chains", action="list")
    )

    assert result == "formatted config"
    assert formatter_calls == [
        {"resource_type": "chains", "action": "list", "result": {"chains": []}}
    ]


def test_server_registers_gateway_clmm_tool(monkeypatch):
    async def get_client():
        return _DummyHummingbotClient()

    async def impl(client, request):
        assert isinstance(client, _DummyHummingbotClient)
        assert request.action == "search"
        assert request.status == "OPEN"
        return {"action": request.action, "result": {"data": []}}

    monkeypatch.setattr(server.hummingbot_client, "get_client", get_client)
    monkeypatch.setattr(server, "manage_gateway_clmm_impl", impl)
    monkeypatch.setattr(
        server,
        "format_gateway_clmm_result",
        lambda action, result: f"{action}: formatted",
    )

    result = asyncio.run(server.manage_gateway_clmm(action="search", status="OPEN"))

    assert result == "search: formatted"


def test_manage_gateway_clmm_opens_position_with_decimal_amounts():
    calls = {}

    class GatewayCLMM:
        async def open_position(self, **kwargs):
            calls.update(kwargs)
            return {"position_address": "pos-1", "transaction_hash": "tx-1"}

    class Client:
        gateway_clmm = GatewayCLMM()

    request = GatewayCLMMManageRequest(
        action="open_position",
        connector="meteora",
        network="solana-mainnet-beta",
        pool_address="pool-1",
        lower_price="10.5",
        upper_price="12.5",
        base_token_amount="1.25",
        quote_token_amount="50",
        slippage_pct="0.5",
        extra_params={"strategyType": 0},
    )

    result = asyncio.run(manage_gateway_clmm(Client(), request))

    assert result["action"] == "open_position"
    assert calls["connector"] == "meteora"
    assert calls["network"] == "solana-mainnet-beta"
    assert calls["pool_address"] == "pool-1"
    assert calls["lower_price"] == Decimal("10.5")
    assert calls["upper_price"] == Decimal("12.5")
    assert calls["base_token_amount"] == Decimal("1.25")
    assert calls["quote_token_amount"] == Decimal("50")
    assert calls["slippage_pct"] == Decimal("0.5")
    assert calls["extra_params"] == {"strategyType": 0}
