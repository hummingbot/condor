"""ARCH-586: a bots WS frame carries the same enrichment as the REST body.

The WS path used to call ``build_bots_page`` with no enrichment at all, so a
frame arrived with ``config={}``, no ``deployed_at``, the raw performance key
as the controller id and a connector/pair guessed by splitting that key on
underscores. The frontend patched every one of those fields back out of the
previous REST payload. Both paths now read one cached enrichment, so the frame
and the REST body are the same page.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.bots as bots_route
from condor.fetchers.bots import fetch_bots_enrichment
from condor.server_data_service import ServerDataService, ServerDataType
from condor.web.auth import require_server_access
from condor.web.models import BotsPageResponse, WebUser

SERVER = "srv"
USER = WebUser(id=7, username="u", first_name="U", role="user")

RAW_STATUS = {
    "status": "success",
    "data": {
        "epsilon": {
            "status": "running",
            "performance": {
                "pmm_binance_BTC-USDT_1": {
                    "status": "running",
                    "performance": {
                        "realized_pnl_quote": 1.5,
                        "unrealized_pnl_quote": -0.5,
                        "volume_traded": 1234.5,
                    },
                },
            },
            "error_logs": [],
            "general_logs": [],
        },
    },
}

CONFIG = {
    "id": "pmm_binance_BTC-USDT_1",
    "controller_name": "pmm_simple",
    "connector_name": "binance",
    "trading_pair": "BTC-USDT",
}

DEPLOYED_AT = "2026-07-01T00:00:00Z"


class _Client:
    """Upstream stub that counts every enrichment call it serves."""

    def __init__(self):
        self.calls: list[str] = []

    # Both namespaces are this object.
    @property
    def controllers(self):
        return self

    @property
    def bot_orchestration(self):
        return self

    async def get_active_bots_status(self):
        self.calls.append("status")
        return RAW_STATUS

    async def get_bot_controller_configs(self, bot_name):
        self.calls.append(f"configs:{bot_name}")
        return [CONFIG]

    async def get_bot_runs(self, **kw):
        self.calls.append("runs")
        return {"data": {"epsilon": {"deployed_at": DEPLOYED_AT}}}

    async def get_latest_controller_performance(self):
        self.calls.append("perf")
        return {
            "data": [
                {
                    "controller_id": "pmm_binance_BTC-USDT_1",
                    "performance": {"global_pnl_pct": 0.42},
                }
            ]
        }


@pytest.fixture
def sds(monkeypatch):
    """A private ServerDataService, primed with the raw status, no real client."""
    import condor.server_data_service as sds_mod

    client = _Client()
    service = ServerDataService()
    service.register_fetch(ServerDataType.BOTS_ENRICHMENT, fetch_bots_enrichment)

    async def _get_client(_server):
        return client

    monkeypatch.setattr(service, "_get_client", _get_client, raising=True)
    monkeypatch.setattr(sds_mod, "_instance", service, raising=False)
    service.put(SERVER, ServerDataType.BOTS_STATUS, RAW_STATUS)
    return service, client


def _rest_body(monkeypatch) -> dict:
    app = FastAPI()
    app.include_router(bots_route.router)
    app.dependency_overrides[require_server_access] = lambda: USER
    with TestClient(app) as api:
        r = api.get(f"/servers/{SERVER}/bots")
    assert r.status_code == 200
    return r.json()


def test_ws_frame_matches_the_rest_body(sds, monkeypatch):
    from condor.web.ws_manager import WebSocketManager

    body = _rest_body(monkeypatch)
    frame = asyncio.run(WebSocketManager._transform_bots(SERVER, RAW_STATUS))

    assert BotsPageResponse(**frame).model_dump(mode="json") == body

    # The fields the frontend used to repair are all really there.
    ctrl = frame["controllers"][0]
    assert ctrl["config"] == CONFIG
    assert ctrl["deployed_at"] == DEPLOYED_AT
    assert ctrl["controller_id"] == "pmm_binance_BTC-USDT_1"
    assert ctrl["connector"] == "binance"
    assert ctrl["trading_pair"] == "BTC-USDT"


def test_a_warm_enrichment_costs_the_5s_frame_no_round_trip(sds):
    from condor.web.ws_manager import WebSocketManager

    _service, client = sds

    asyncio.run(WebSocketManager._transform_bots(SERVER, RAW_STATUS))
    assert client.calls, "the first frame has to fetch the enrichment"

    after_first = list(client.calls)
    for _ in range(3):
        asyncio.run(WebSocketManager._transform_bots(SERVER, RAW_STATUS))

    assert client.calls == after_first


def test_a_failed_enrichment_still_renders_the_page(sds, monkeypatch):
    """The maps are optional: a server that cannot answer costs those columns."""
    from condor.web.ws_manager import WebSocketManager

    service, _client = sds

    async def _boom(_server):
        raise RuntimeError("no client")

    monkeypatch.setattr(service, "_get_client", _boom, raising=True)

    frame = asyncio.run(WebSocketManager._transform_bots(SERVER, RAW_STATUS))

    assert frame["controllers"][0]["config"] == {}
    assert frame["controllers"][0]["deployed_at"] is None
    assert frame["total_volume"] == 1234.5
