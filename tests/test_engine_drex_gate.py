"""A loop with ``drex_gate`` runs its model only when Drex answers to act.

Drex is asked before each tick whether to hold, start or stop the loop's one
position. A hold ends the tick without the model; anything else, and any
reason Drex can't answer, leaves the tick to the model as before.
"""

import asyncio
import math
from types import SimpleNamespace

import aiohttp
import pytest

from condor.agents import drex_gate
from condor.agents.providers.base import ProviderResult

GATE = {"trading_pair": "BTC-USDT", "drex_gate": {"side": "LONG", "interval": "1h"}}


def _engine(tmp_path, monkeypatch, executors, **config):
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="directional_trader", name="Trend Position Loop")
    strategy.home.mkdir(parents=True, exist_ok=True)
    engine = TickEngine(
        agent=Agent(slug="directional_trader", name="Directional Trader"),
        strategy=strategy,
        config={"execution_mode": "loop", **config},
        chat_id=1,
        user_id=1,
    )

    async def no_adoption(*_args):
        return None

    async def providers(*_args, **_kwargs):
        data = {"executors": executors} if isinstance(executors, list) else executors
        return {"executors": ProviderResult(name="executors", data=data, summary="")}

    monkeypatch.setattr(engine, "_adopt_running_bots", no_adoption)
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", providers)
    return engine


def _client():
    """A hummingbot-api client that only serves a gentle uptrend of 1h candles."""

    async def get_candles(connector, pair, interval, max_records):
        rows = []
        for i in range(150):
            close = 100 + i * 0.2 + 2 * math.sin(i / 5)
            rows.append(
                {
                    "timestamp": 1_700_000_000 + i * 3600,
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 10.0,
                }
            )
        return {"data": rows}

    return SimpleNamespace(market_data=SimpleNamespace(get_candles=get_candles))


def _drex(monkeypatch, choice=None, probabilities=None, status=200):
    """Answer like Drex at the HTTP layer; return the requests made."""
    calls = []
    body = {
        "model": "drex-max-finance",
        "answers": {"action": {"choice": choice, "probabilities": probabilities}},
    }

    class _Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            if status >= 400:
                raise aiohttp.ClientError(f"HTTP {status}")

        async def json(self):
            return body

    class _Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(drex_gate.aiohttp, "ClientSession", _Session)
    return calls


def test_without_drex_gate_nothing_is_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, "hold", {"hold": 1.0, "start": 0.0})
    engine = _engine(tmp_path, monkeypatch, [], trading_pair="BTC-USDT")

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert calls == []
    assert ctx is not None and "drex_gate" not in ctx.core_data_summaries


def test_hold_ends_the_tick_without_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, "hold", {"hold": 0.9, "start": 0.1})
    engine = _engine(tmp_path, monkeypatch, [], **GATE)
    client = _client()

    async def get_client():
        return client

    async def model(*_args):
        raise AssertionError("the model ran on a Drex hold")

    monkeypatch.setattr(engine, "_get_client", get_client)
    monkeypatch.setattr(engine, "_run_model", model)

    asyncio.run(engine._tick())

    (call,) = calls
    assert call["headers"]["X-Nace-Client"] == "condor/0.1.0"
    assert set(call["json"]["questions"]["action"]["criteria"]) == {"start", "hold"}
    assert call["json"]["state"]["position"] == "flat"
    journal = engine.journal._path.read_text()
    assert "model_skipped" in journal
    assert "model skipped: Drex (drex-max-finance): HOLD — hold 0.900" in journal
    assert engine.journal.tick_count == 1


def test_start_runs_the_model_with_the_answer_in_its_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    _drex(monkeypatch, "start", {"start": 0.95, "hold": 0.05})
    engine = _engine(tmp_path, monkeypatch, [], **GATE)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert ctx is not None
    assert "[CORE DATA - drex_gate]" in ctx.prompt
    assert "Drex (drex-max-finance): START — start 0.950, hold 0.050" in ctx.prompt
    assert engine.journal.tick_count == 0


def test_open_position_asks_hold_or_stop_from_its_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, "stop", {"stop": 0.8, "hold": 0.2})
    position = {"pair": "BTC-USDT", "side": "BUY", "entry_price": 140.0}
    engine = _engine(tmp_path, monkeypatch, [position], **GATE)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    question = calls[0]["json"]["questions"]["action"]
    assert set(question["criteria"]) == {"stop", "hold"}
    assert question["criteria"]["stop"] == drex_gate.STOP_WHEN["LONG"]
    assert calls[0]["json"]["state"]["entry_price"] == 140.0
    assert "Drex (drex-max-finance): STOP" in ctx.prompt


def test_the_loop_s_own_rule_is_what_drex_and_the_model_get(tmp_path, monkeypatch):
    rule = "RSI 14 below 30 and close more than 2 ATR below EMA 20."
    gate = {"side": "LONG", "interval": "1h", "start_when": rule}
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, status=503)
    engine = _engine(tmp_path, monkeypatch, [], trading_pair="BTC-USDT", drex_gate=gate)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert calls[0]["json"]["questions"]["action"]["criteria"]["start"] == rule
    assert f"- start: {rule}" in ctx.prompt


@pytest.mark.parametrize(
    "executors",
    [
        [{"pair": "ETH-USDT", "side": "BUY", "entry_price": 3000.0}],
        [{"pair": "BTC-USDT", "side": "SELL", "entry_price": 140.0}],
        [{"pair": "BTC-USDT", "side": "BUY", "entry_price": 0.0}],
        [{"pair": "BTC-USDT", "side": "BUY", "entry_price": 140.0}] * 2,
        {"error": "backend unreachable"},
    ],
    ids=["other pair", "other side", "unfilled", "two positions", "no reading"],
)
def test_a_book_the_question_does_not_describe_is_left_to_the_model(
    tmp_path, monkeypatch, executors
):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, "hold", {"hold": 1.0, "start": 0.0})
    engine = _engine(tmp_path, monkeypatch, executors, **GATE)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert calls == []
    assert "Drex was not asked" in ctx.prompt


def test_drex_error_leaves_the_tick_to_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    _drex(monkeypatch, status=503)
    engine = _engine(tmp_path, monkeypatch, [], **GATE)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert "Drex request failed (HTTP 503). Decide from the snapshot:" in ctx.prompt
    assert drex_gate.START_WHEN["LONG"] in ctx.prompt


def test_without_key_the_model_decides_every_tick(tmp_path, monkeypatch):
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    calls = _drex(monkeypatch, "hold", {"hold": 1.0, "start": 0.0})
    engine = _engine(tmp_path, monkeypatch, [], **GATE)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert calls == []
    assert "Decide from the snapshot:" in ctx.prompt
    assert "Drex" not in ctx.core_data_summaries["drex_gate"]


def test_a_dry_run_is_shown_the_hold_instead_of_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    _drex(monkeypatch, "hold", {"hold": 0.9, "start": 0.1})
    engine = _engine(tmp_path, monkeypatch, [], execution_mode="dry_run", **GATE)

    ctx = asyncio.run(engine._gather_tick_context(_client()))

    assert ctx is not None
    assert "Drex (drex-max-finance): HOLD" in ctx.prompt
