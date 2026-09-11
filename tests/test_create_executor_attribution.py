"""The browser's create body has no attribution field, and cannot pretend to.

``CreateExecutorRequest`` used to declare ``controller_id: str = "main"``. No
route ever read it and no fetcher could have carried it, but its default was the
very value executors come back under, so it read as the mechanism that produced
that value — the decoy sitting on exactly the chain a reader would come here to
fix. It is gone; these tests keep it gone and keep the reason legible.

The three claims:

* the create body declares no ``controller_id`` — nothing to mistake;
* a request that sends one anyway is still accepted, and is *demonstrably*
  ignored: nothing named ``controller_id`` reaches the trading API, neither as
  an argument nor smuggled inside the executor config;
* the fetcher has no channel for one, which is why honouring the field was never
  a one-line fix — the ``main`` an executor comes back under is the trading
  API's server-side default, applied because the field is omitted entirely.

Attribution that actually works lives on the MCP path
(``mcp_servers/hummingbot_api/tools/executor_create.py``), which passes a
``controller_id`` straight to the client. This module is about the browser only.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from condor.fetchers.executors import create_executor
from condor.web.models import CreateExecutorRequest, WebUser
from condor.web.routes.executors import create_executor_endpoint

_USER = WebUser(id=1, role="admin")


class _RecordingClient:
    """API client that records exactly what the create call was handed."""

    def __init__(self):
        self.executors = self._Executors()

    class _Executors:
        def __init__(self):
            self.calls = []

        async def create_executor(self, **kw):
            self.calls.append(kw)
            return {"executor_id": "ex-1"}


class _FakeCM:
    def __init__(self, client):
        self._client = client

    async def get_client(self, name):
        return self._client


@pytest.fixture
def client(monkeypatch):
    """A bound create route with the deed writer silenced (it writes to disk)."""
    recorder = _RecordingClient()
    monkeypatch.setattr(
        "condor.web.routes.executors.get_config_manager", lambda: _FakeCM(recorder)
    )
    monkeypatch.setattr(
        "condor.web.routes.executors.record_ui_deed", lambda *a, **kw: None
    )
    return recorder


def _post(body: dict) -> None:
    asyncio.run(
        create_executor_endpoint(
            name="srv",
            body=CreateExecutorRequest.model_validate(body),
            user=_USER,
        )
    )


def test_the_create_body_declares_no_controller_id():
    assert "controller_id" not in CreateExecutorRequest.model_fields, (
        "a create-body field named controller_id reads as the attribution hook; "
        "the browser path has none"
    )


def test_a_controller_id_sent_anyway_never_reaches_the_trading_api(client):
    # What an older client, or someone who read the field's docs, would post.
    _post(
        {
            "executor_type": "grid_executor",
            "config": {"trading_pair": "SOL-USDC"},
            "controller_id": "agent-7",
        }
    )

    (call,) = client.executors.calls
    assert "controller_id" not in call, "no argument carries it"
    assert "controller_id" not in call["executor_config"], "nor does the config"
    assert "agent-7" not in str(call), "the id is gone, not relocated"


def test_an_unexpected_field_is_dropped_rather_than_rejected():
    """Removing the field changed no request that used to succeed.

    The model takes pydantic's default ``extra="ignore"``, so a body carrying a
    top-level ``controller_id`` still validates — exactly as it did when the
    field existed and the route ignored it. The only thing that changed is that
    nothing in the schema now claims to accept it.
    """
    body = CreateExecutorRequest.model_validate(
        {"executor_type": "grid_executor", "config": {}, "controller_id": "agent-7"}
    )

    assert not hasattr(body, "controller_id")
    assert body.account_name == "master_account"


def test_the_fetcher_has_no_channel_for_a_controller_id():
    """Why wiring the browser path is a feature, not a typo fix."""
    params = inspect.signature(create_executor).parameters

    assert "controller_id" not in params
    assert list(params) == ["client", "config", "account_name"]
