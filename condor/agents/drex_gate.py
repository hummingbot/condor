"""Drex's hold, start or stop answer for one directional position.

Shared by directional_trader's ``position_decision`` routine, which an agent
runs when it wants the answer, and by the tick engine, which asks before every
tick of a loop that sets ``drex_gate`` and skips the model on a hold.
https://console.nace.ai/docs/drex
"""

from __future__ import annotations

import logging
import os

import aiohttp
import pandas as pd
import pandas_ta as ta  # noqa: F401

from condor.fetchers.market_data import normalize_candle

logger = logging.getLogger(__name__)

DREX_URL = "https://console.nace.ai/v1/systemone"
DREX_MODEL = "drex-max-finance"
DREX_TIMEOUT_S = 15
# Sent as X-Nace-Client and User-Agent; the version is pyproject.toml's.
DREX_CLIENT = "condor/0.1.0"

START_WHEN = {
    "LONG": (
        "The uptrend is intact and not stretched. All of: close above EMA 20, "
        "EMA 20 above EMA 50, RSI 14 between 50 and 70, close within 2 ATR of "
        "EMA 20."
    ),
    "SHORT": (
        "The downtrend is intact and not stretched. All of: close below EMA 20, "
        "EMA 20 below EMA 50, RSI 14 between 30 and 50, close within 2 ATR of "
        "EMA 20."
    ),
}
STOP_WHEN = {
    "LONG": (
        "The trend has turned against the long, even if it is in profit. Any of: "
        "close below EMA 50, RSI 14 below 40, open loss of more than 2 ATR."
    ),
    "SHORT": (
        "The trend has turned against the short, even if it is in profit. Any "
        "of: close above EMA 50, RSI 14 above 60, open loss of more than 2 ATR."
    ),
}


def build_snapshot(candles: list, side: str, entry_price: float) -> dict:
    """The numbers the criteria are read against, to 6 significant figures.

    Not decimal places: a coin at 0.08 has an ATR near 0.0005.
    """
    df = pd.DataFrame(candles)
    close = df["close"].iloc[-1]
    ema_20 = df.ta.ema(length=20).iloc[-1]
    ema_50 = df.ta.ema(length=50).iloc[-1]
    atr = df.ta.atr(length=14).iloc[-1]
    snapshot = {
        "side": side,
        "position": "open" if entry_price > 0 else "flat",
        "close": close,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "rsi_14": df.ta.rsi(length=14).iloc[-1],
        "atr_14": atr,
        "close_minus_ema_20_in_atr": (close - ema_20) / atr,
        "close_minus_ema_50_in_atr": (close - ema_50) / atr,
        # Stated, not left to compare: given only the two EMAs, Drex sometimes
        # misread which was above and answered start.
        "ema_20_minus_ema_50_in_atr": (ema_20 - ema_50) / atr,
    }
    if entry_price > 0:
        sign = 1 if side == "LONG" else -1
        snapshot["entry_price"] = entry_price
        snapshot["open_pnl_in_atr"] = sign * (close - entry_price) / atr
    return {
        k: float(f"{v:.6g}") if isinstance(v, float) else v for k, v in snapshot.items()
    }


def build_question(
    trading_pair: str,
    interval: str,
    side: str,
    flat: bool,
    start_when: str = "",
    stop_when: str = "",
) -> dict:
    """One choice question: hold or start when flat, hold or stop when open."""
    if flat:
        action, criteria = "start", start_when or START_WHEN[side]
    else:
        action, criteria = "stop", stop_when or STOP_WHEN[side]
    state = "flat" if flat else f"holding a {side} position"
    return {
        "type": "choice",
        "instructions": (
            f"A loop trading {side} on {trading_pair} {interval} "
            f"candles is {state}. What should it do this tick?"
        ),
        "criteria": {
            action: criteria,
            "hold": (
                "Do nothing this tick: the snapshot does not meet the condition "
                f"to {action}."
            ),
        },
    }


async def ask_drex(api_key: str, snapshot: dict, question: dict) -> dict:
    """Drex's answer to ``question``: ``choice`` plus ``probabilities`` per label."""
    payload = {
        "model": os.environ.get("NACE_DEFAULT_DECISION_MODEL") or DREX_MODEL,
        "state": snapshot,
        "questions": {"action": question},
    }
    timeout = aiohttp.ClientTimeout(total=DREX_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            DREX_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Nace-Client": DREX_CLIENT,
                "User-Agent": DREX_CLIENT,
            },
        ) as resp:
            resp.raise_for_status()
            body = await resp.json()
    answer = body["answers"]["action"]
    if answer.get("choice") not in question["criteria"]:
        raise ValueError(f"answer outside the asked labels: {answer.get('choice')!r}")
    return {"model": body.get("model", payload["model"]), **answer}


def describe(answer: dict) -> str:
    """``Drex (<model>): START — start 0.970, hold 0.030``, likeliest label first."""
    probs = ", ".join(
        f"{label} {p:.3f}"
        for label, p in sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
    )
    return f"Drex ({answer['model']}): {answer['choice'].upper()} — {probs}"


async def decide(
    client,
    *,
    connector_name: str,
    trading_pair: str,
    interval: str,
    side: str,
    entry_price: float = 0.0,
    start_when: str = "",
    stop_when: str = "",
) -> tuple[dict | None, str]:
    """Drex's answer for the position, or ``None``, and the text for the model.

    ``None`` without NACE_API_KEY, when Drex fails, or when the candles can't be
    read; the text then carries the snapshot and the criteria, or the reason,
    so the model makes the call instead.
    """
    try:
        raw = await client.market_data.get_candles(
            connector_name, trading_pair, interval, max_records=150
        )
    except Exception as e:
        logger.error(f"[drex_gate] candle fetch failed: {e}")
        return None, f"Error fetching candles: {e}"
    rows = raw if isinstance(raw, list) else (raw or {}).get("data", [])
    candles = [c for c in map(normalize_candle, rows) if c]
    # Twice EMA 50's length, so the EMA has settled away from its SMA seed.
    if len(candles) < 100:
        return None, f"Only {len(candles)} {interval} candles — need 100 to decide."

    flat = entry_price <= 0
    snapshot = build_snapshot(candles, side, entry_price)
    question = build_question(trading_pair, interval, side, flat, start_when, stop_when)
    header = (
        f"{trading_pair} {interval}, {side}, "
        f"{'flat' if flat else f'open at {entry_price:g}'}"
    )
    lines = "\n".join(f"{k}: {v}" for k, v in snapshot.items())

    api_key = os.environ.get("NACE_API_KEY", "")
    note = ""
    if api_key:
        try:
            answer = await ask_drex(api_key, snapshot, question)
            return answer, f"{header}\n{describe(answer)}\n\n{lines}"
        except Exception as e:
            logger.warning(f"[drex_gate] Drex request failed: {e}")
            note = f"Drex request failed ({e}). "

    criteria = "\n".join(f"- {k}: {v}" for k, v in question["criteria"].items())
    return None, f"{header}\n{note}Decide from the snapshot:\n{criteria}\n\n{lines}"
