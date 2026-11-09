"""Purpose: verify deployment timeouts against a local API, never a live bot."""

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from hummingbot_api_client import HummingbotAPIClient

import mcp_servers.hummingbot_api.hummingbot_client as client_module
import mcp_servers.hummingbot_api.server as server
from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.settings import Settings


@pytest.mark.parametrize(
    "ordinary_timeout,deploy_timeout", [(30.0, 120.0), (180.0, 180.0)]
)
def test_deployment_budget_does_not_change_shared_client(
    monkeypatch, ordinary_timeout, deploy_timeout
):
    monkeypatch.setattr(
        client_module, "settings", Settings(connection_timeout=ordinary_timeout)
    )
    clients = []

    def make_client(**kwargs):
        client = AsyncMock()
        client.timeout = kwargs["timeout"]
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, "HummingbotAPIClient", make_client)

    async def run():
        wrapper = client_module.HummingbotClient()
        shared = await wrapper.get_client()
        async with wrapper.deployment_client() as deployment:
            assert deployment is not shared
            assert deployment.timeout.total == deploy_timeout
            assert shared.timeout.total == ordinary_timeout
            assert await wrapper.get_client() is shared
        deployment.close.assert_awaited_once()
        shared.close.assert_not_awaited()
        await wrapper.close()

    asyncio.run(run())
    assert len(clients) == 2


@pytest.mark.parametrize("timeout", [False, True])
def test_manage_bots_delayed_http_response_and_unknown_timeout(monkeypatch, timeout):
    """Use the real SDK/HTTP path, scaling the budgets to keep this test fast."""

    async def run():
        requests = []
        clients = []

        async def deploy(request):
            requests.append(await request.json())
            await asyncio.sleep(0.2)
            return web.json_response({"success": True, "message": "deployed"})

        async def accounts(request):
            return web.json_response(["test_account"])

        app = web.Application()
        app.router.add_post("/bot-orchestration/deploy-v2-controllers", deploy)
        app.router.add_get("/accounts/", accounts)
        async with TestServer(app) as api:
            monkeypatch.setattr(
                client_module,
                "settings",
                Settings(
                    api_url=str(api.make_url("")),
                    api_username="test",
                    api_password="test",
                    connection_timeout=0.05,
                ),
            )

            def make_client(**kwargs):
                if kwargs["timeout"].total == 120.0:
                    kwargs["timeout"] = aiohttp.ClientTimeout(
                        total=0.05 if timeout else 1.0
                    )
                client = HummingbotAPIClient(**kwargs)
                client.close = AsyncMock(wraps=client.close)
                clients.append(client)
                return client

            monkeypatch.setattr(client_module, "HummingbotAPIClient", make_client)
            wrapper = client_module.HummingbotClient()
            monkeypatch.setattr(server, "hummingbot_client", wrapper)
            shared = await wrapper.get_client()
            try:
                call = server.manage_bots(
                    action="deploy",
                    bot_name="test-bot",
                    controllers_config=["test-config"],
                    account_name="test_account",
                    max_global_drawdown_quote=10.0,
                    max_controller_drawdown_quote=5.0,
                    image="test-image",
                )
                if timeout:
                    with pytest.raises(ToolError) as caught:
                        await call
                    message = str(caught.value)
                    for text in (
                        "test-bot",
                        "TimeoutError",
                        "Outcome unknown",
                        "test-config",
                        "test_account",
                        "before retrying",
                        "No deployment retry was attempted",
                    ):
                        assert text in message
                    assert isinstance(caught.value.__cause__, TimeoutError)
                else:
                    assert "deployed" in await call
                assert len(clients) == 2
                clients[1].close.assert_awaited_once()
                assert await wrapper.get_client() is shared
                shared.close.assert_not_awaited()
                assert shared.timeout.total == 0.05
                # Wait for the mock backend to finish even if the client timed out.
                await asyncio.sleep(0.25)
                assert requests == [
                    {
                        "instance_name": "test-bot",
                        "credentials_profile": "test_account",
                        "controllers_config": ["test-config"],
                        "max_global_drawdown_quote": 10.0,
                        "max_controller_drawdown_quote": 5.0,
                        "image": "test-image",
                    }
                ]
            finally:
                await wrapper.close()

    asyncio.run(run())


@pytest.mark.parametrize("during_init", [True, False])
def test_deployment_client_closes_on_init_failure_or_cancellation(
    monkeypatch, during_init
):
    client = AsyncMock()
    monkeypatch.setattr(client_module, "HummingbotAPIClient", lambda **kwargs: client)
    if during_init:
        client.init.side_effect = RuntimeError("init failed")

    async def run():
        with pytest.raises(RuntimeError if during_init else asyncio.CancelledError):
            async with client_module.HummingbotClient().deployment_client():
                raise asyncio.CancelledError()
        client.close.assert_awaited_once()

    asyncio.run(run())


def test_other_bot_actions_keep_shared_client(monkeypatch):
    wrapper = AsyncMock()
    monkeypatch.setattr(server, "hummingbot_client", wrapper)
    status = AsyncMock(return_value={"total_bots": 0, "bots_table": "empty"})
    monkeypatch.setattr(server.bot_management_tools, "get_active_bots_status", status)
    assert "Total Active Bots: 0" in asyncio.run(server.manage_bots(action="status"))
    status.assert_awaited_once_with(wrapper.get_client.return_value)
    wrapper.deployment_client.assert_not_called()


def test_invalid_deploy_does_not_open_a_client(monkeypatch):
    wrapper = AsyncMock()
    monkeypatch.setattr(server, "hummingbot_client", wrapper)
    assert "bot_name" in asyncio.run(server.manage_bots(action="deploy"))
    assert "controllers_config" in asyncio.run(
        server.manage_bots(action="deploy", bot_name="test-bot")
    )
    wrapper.get_client.assert_not_called()
    wrapper.deployment_client.assert_not_called()
