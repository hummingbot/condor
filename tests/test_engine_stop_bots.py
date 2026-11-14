"""A graceful stop must stop this session's bots -- and never fail the stop doing it."""

import asyncio
import sys
import types

import pytest

from condor.agents import engine as engine_mod

sys.path.insert(0, "tests")


class _Ledger:
    def __init__(self, bases):
        self._bases = bases

    def bases(self):
        return list(self._bases)


def _fake_engine(bases, client_raises=False):
    async def _get_client():
        if client_raises:
            raise RuntimeError("api down")
        return object()

    return types.SimpleNamespace(
        ledger=_Ledger(bases),
        agent_id="acme.scalper_1",
        config={},
        _get_client=_get_client,
    )


def test_stop_stops_the_bots_this_session_deployed(monkeypatch):
    called = {}

    async def _stop_owned_bots(engine, client):
        called["agent"] = engine.agent_id
        return ["acme-bot-1"], []

    monkeypatch.setattr("condor.agents.shutdown._stop_owned_bots", _stop_owned_bots)
    asyncio.run(engine_mod.TickEngine._stop_bots_on_stop(_fake_engine(["acme-bot-1"])))
    assert called["agent"] == "acme.scalper_1"


def test_no_bots_deployed_touches_nothing(monkeypatch):
    async def _boom(engine, client):
        raise AssertionError("must not look for bots a session never deployed")

    monkeypatch.setattr("condor.agents.shutdown._stop_owned_bots", _boom)
    asyncio.run(engine_mod.TickEngine._stop_bots_on_stop(_fake_engine([])))


def test_an_unreachable_api_does_not_block_the_stop(monkeypatch):
    async def _boom(engine, client):
        raise AssertionError("must not be called without a client")

    monkeypatch.setattr("condor.agents.shutdown._stop_owned_bots", _boom)
    asyncio.run(
        engine_mod.TickEngine._stop_bots_on_stop(
            _fake_engine(["acme-bot-1"], client_raises=True)
        )
    )


def test_a_failing_bot_stop_does_not_raise(monkeypatch):
    async def _explode(engine, client):
        raise RuntimeError("stop-bot hung")

    monkeypatch.setattr("condor.agents.shutdown._stop_owned_bots", _explode)
    asyncio.run(engine_mod.TickEngine._stop_bots_on_stop(_fake_engine(["acme-bot-1"])))


def test_bot_stop_failures_are_logged_not_swallowed(monkeypatch, caplog):
    async def _partial(engine, client):
        return [], ["acme-bot-2: stop-bot hung"]

    monkeypatch.setattr("condor.agents.shutdown._stop_owned_bots", _partial)
    with caplog.at_level("WARNING"):
        asyncio.run(
            engine_mod.TickEngine._stop_bots_on_stop(_fake_engine(["acme-bot-2"]))
        )
    assert "bot stop failed" in caplog.text
