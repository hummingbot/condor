"""Core providers never put a raw trading-API exception in the tick prompt ([[SEC-665]]).

``aiohttp.ClientResponseError`` stringifies with the backend's own URL, and a
provider summary is embedded in the tick prompt and in ``snapshot_N.md``, which
any authenticated dashboard user can read. The positions and executors providers
surface only :func:`condor.fetchers.executors.describe_executor_error`'s message
and log the raw exception server-side. (The drift provider's pins live in
tests/test_drift_provider.py.)
"""

import asyncio
import logging

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from condor.agents import performance
from condor.agents.providers.executors import ExecutorsProvider
from condor.agents.providers.positions import PositionsProvider


def _http_error():
    url = URL("http://10.0.0.5:8000/x")
    info = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.ClientResponseError(
        info, (), status=500, message="Internal Server Error"
    )


class _Executors:
    def __init__(self, raises):
        self.raises = raises

    async def get_positions_summary(self, controller_id=None):
        raise self.raises


class _Client:
    def __init__(self, raises):
        self.executors = _Executors(raises)


def _positions(raises):
    return asyncio.run(
        PositionsProvider().execute(_Client(raises), {}, agent_id="acme.mm_1")
    )


def _executors(monkeypatch, raises):
    async def boom(*args, **kwargs):
        raise raises

    monkeypatch.setattr(performance, "fetch_agent_performance", boom)
    return asyncio.run(
        ExecutorsProvider().execute(
            object(), {"bot_name": "acme"}, agent_id="acme.mm_1"
        )
    )


@pytest.fixture(params=["positions", "executors"])
def run(request, monkeypatch):
    if request.param == "positions":
        return request.param, _positions
    return request.param, lambda raises: _executors(monkeypatch, raises)


def test_an_http_error_surfaces_the_api_message_without_the_url(run, caplog):
    name, execute = run
    logger = f"condor.agents.providers.{name}"
    with caplog.at_level(logging.WARNING, logger=logger):
        result = execute(_http_error())

    for text in (result.summary, result.data["error"]):
        assert "Internal Server Error" in text
        assert "10.0.0.5" not in text
        assert "http://" not in text
    assert "failed to fetch" in result.summary

    records = [r for r in caplog.records if r.name == logger]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "10.0.0.5" in logging.Formatter().formatException(records[0].exc_info)


def test_a_transport_error_is_the_generic_unreachable_line(run):
    _, execute = run
    result = execute(aiohttp.ClientConnectionError("connection reset"))
    assert result.data["error"] == "the trading API is unreachable"
    assert "the trading API is unreachable" in result.summary
    assert "connection reset" not in result.summary


def test_no_provider_serializes_a_raw_exception():
    from pathlib import Path

    providers = Path(__file__).resolve().parents[1] / "condor/agents/providers"
    for path in providers.glob("*.py"):
        source = path.read_text()
        assert "str(e)" not in source and "str(exc)" not in source, path.name
