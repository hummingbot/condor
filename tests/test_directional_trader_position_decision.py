"""directional_trader's position_decision: Drex answers only when its key is set."""

from __future__ import annotations

import asyncio
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import aiohttp

from condor.agents import drex_gate

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "agents" / "directional_trader" / "routines" / "position_decision.py"

spec = importlib.util.spec_from_file_location("position_decision", PATH)
decision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decision)


def _candles(n=150):
    """A gentle uptrend with a wobble, in hummingbot-api's dict row shape."""
    rows = []
    for i in range(n):
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
    return rows


class _Response:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientError(f"HTTP {self.status}")

    async def json(self):
        return self.body


def _drex(monkeypatch, status=200, body=None):
    """Replace aiohttp's session with one that answers like Drex; return the calls."""
    calls = []

    class _Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers})
            return _Response(status, body)

    monkeypatch.setattr(drex_gate.aiohttp, "ClientSession", _Session)
    return calls


def _run(monkeypatch, **config):
    client = SimpleNamespace(market_data=SimpleNamespace())

    async def get_candles(connector, pair, interval, max_records):
        return {"data": _candles()}

    async def fake_get_client(*_args, **_kwargs):
        return client

    client.market_data.get_candles = get_candles
    monkeypatch.setattr(decision, "get_client", fake_get_client)
    context = SimpleNamespace(_chat_id=7)
    return asyncio.run(decision.run(decision.Config(**config), context))


def _answer(choice, probabilities):
    return {
        "model": "drex-max-finance",
        "answers": {
            "action": {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities,
            }
        },
    }


def test_without_key_returns_the_snapshot_and_sends_nothing(monkeypatch):
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    calls = _drex(monkeypatch)

    output = _run(monkeypatch)

    assert calls == []
    assert "Drex" not in output
    assert "Decide from the snapshot:" in output
    assert f"- start: {drex_gate.START_WHEN['LONG']}" in output
    assert "position: flat" in output
    assert "ema_50: " in output


def test_flat_loop_gets_drex_choice_between_hold_and_start(monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, body=_answer("start", {"start": 0.9, "hold": 0.1}))

    output = _run(monkeypatch, trading_pair="SOL-USDT")

    assert "Drex (drex-max-finance): START — start 0.900, hold 0.100" in output
    (call,) = calls
    assert call["url"] == "https://console.nace.ai/v1/systemone"
    assert call["headers"] == {
        "Authorization": "Bearer nace_sk_test",
        "X-Nace-Client": "condor/0.1.0",
        "User-Agent": "condor/0.1.0",
    }
    assert call["json"]["model"] == "drex-max-finance"
    question = call["json"]["questions"]["action"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {"start", "hold"}
    assert question["criteria"]["start"] == drex_gate.START_WHEN["LONG"]
    assert call["json"]["state"]["position"] == "flat"
    # The uptrend's EMA order is stated, not left for Drex to work out.
    assert call["json"]["state"]["ema_20_minus_ema_50_in_atr"] > 0


def test_open_short_asks_hold_or_stop_with_loss_in_atr(monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    calls = _drex(monkeypatch, body=_answer("stop", {"stop": 0.8, "hold": 0.2}))

    output = _run(monkeypatch, side="short", entry_price=100.0)

    assert "Drex (drex-max-finance): STOP — stop 0.800, hold 0.200" in output
    state = calls[0]["json"]["state"]
    criteria = calls[0]["json"]["questions"]["action"]["criteria"]
    assert set(criteria) == {"stop", "hold"}
    assert criteria["stop"] == drex_gate.STOP_WHEN["SHORT"]
    assert state["side"] == "SHORT"
    assert state["entry_price"] == 100.0
    # Price rose from the entry, so the short is under water.
    assert state["open_pnl_in_atr"] < 0


def test_drex_error_leaves_the_decision_to_the_tick(monkeypatch, caplog):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    _drex(monkeypatch, status=503)

    output = _run(monkeypatch)

    assert "Drex request failed (HTTP 503). Decide from the snapshot:" in output
    assert f"- start: {drex_gate.START_WHEN['LONG']}" in output
    assert "Drex request failed" in caplog.text
    assert "nace_sk_test" not in output + caplog.text


def test_answer_outside_the_asked_labels_is_not_trusted(monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test")
    _drex(monkeypatch, body=_answer("stop", {"stop": 1.0}))

    output = _run(monkeypatch)

    assert "Drex request failed (answer outside the asked labels" in output


def test_a_sub_cent_coin_keeps_its_numbers():
    tiny = [
        {k: v * 1e-7 if k != "timestamp" else v for k, v in row.items()}
        for row in _candles()
    ]

    big = drex_gate.build_snapshot(_candles(), "LONG", 0.0)
    small = drex_gate.build_snapshot(tiny, "LONG", 0.0)

    assert small["atr_14"] > 0
    assert small["ema_20"] > small["ema_50"]
    assert small["ema_20_minus_ema_50_in_atr"] == big["ema_20_minus_ema_50_in_atr"]
