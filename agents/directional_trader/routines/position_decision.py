"""Hold, start or stop one directional position, answered by Drex when NACE_API_KEY is set."""

CATEGORY = "Analysis"

import logging
import os

import aiohttp
import pandas as pd
import pandas_ta as ta  # noqa: F401
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.fetchers.market_data import normalize_candle
from config_manager import get_client

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


class Config(BaseModel):
    """Hold, start or stop a LONG or SHORT position on one pair from EMA 20/50, RSI 14 and ATR 14.

    With NACE_API_KEY set, Drex (https://console.nace.ai/docs/drex) picks the
    action and gives a probability for each. Without it, or when Drex fails, the
    snapshot and the criteria come back for the tick to decide.
    """

    connector_name: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    interval: str = Field(default="1h", description="Candle interval")
    side: str = Field(default="LONG", description="LONG or SHORT: what START opens")
    entry_price: float = Field(
        default=0.0, description="Entry price of the open position, 0 when flat"
    )
    start_when: str = Field(
        default="", description="When to open; empty uses the EMA/RSI rule for side"
    )
    stop_when: str = Field(
        default="", description="When to close; empty uses the EMA/RSI rule for side"
    )


def build_snapshot(candles: list, side: str, entry_price: float) -> dict:
    """The numbers the criteria are read against, rounded for the prompt."""
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
    }
    if entry_price > 0:
        sign = 1 if side == "LONG" else -1
        snapshot["entry_price"] = entry_price
        snapshot["open_pnl_in_atr"] = sign * (close - entry_price) / atr
    return {
        k: round(float(v), 4) if isinstance(v, float) else v
        for k, v in snapshot.items()
    }


def build_question(config: Config, side: str, flat: bool) -> dict:
    """One choice question: hold or start when flat, hold or stop when open."""
    if flat:
        action, criteria = "start", config.start_when or START_WHEN[side]
    else:
        action, criteria = "stop", config.stop_when or STOP_WHEN[side]
    state = "flat" if flat else f"holding a {side} position"
    return {
        "type": "choice",
        "instructions": (
            f"A loop trading {side} on {config.trading_pair} {config.interval} "
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


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    side = config.side.strip().upper()
    if side not in ("LONG", "SHORT"):
        return f"side must be LONG or SHORT, got {config.side!r}"

    client = await get_client(getattr(context, "_chat_id", None), context=context)
    if not client:
        return "No server available"

    try:
        raw = await client.market_data.get_candles(
            config.connector_name,
            config.trading_pair,
            config.interval,
            max_records=150,
        )
    except Exception as e:
        logger.error(f"[position_decision] candle fetch failed: {e}")
        return f"Error fetching candles: {e}"
    rows = raw if isinstance(raw, list) else (raw or {}).get("data", [])
    candles = [c for c in map(normalize_candle, rows) if c]
    # Twice EMA 50's length, so the EMA has settled away from its SMA seed.
    if len(candles) < 100:
        return f"Only {len(candles)} {config.interval} candles — need 100 to decide."

    flat = config.entry_price <= 0
    snapshot = build_snapshot(candles, side, config.entry_price)
    question = build_question(config, side, flat)
    header = (
        f"{config.trading_pair} {config.interval}, {side}, "
        f"{'flat' if flat else f'open at {config.entry_price:g}'}"
    )
    lines = "\n".join(f"{k}: {v}" for k, v in snapshot.items())

    api_key = os.environ.get("NACE_API_KEY", "")
    note = ""
    if api_key:
        try:
            answer = await ask_drex(api_key, snapshot, question)
            probs = ", ".join(
                f"{label} {p:.3f}"
                for label, p in sorted(
                    answer["probabilities"].items(), key=lambda kv: -kv[1]
                )
            )
            return (
                f"{header}\nDrex ({answer['model']}): {answer['choice'].upper()} "
                f"— {probs}\n\n{lines}"
            )
        except Exception as e:
            logger.warning(f"[position_decision] Drex request failed: {e}")
            note = f"Drex request failed ({e}). "

    criteria = "\n".join(f"- {k}: {v}" for k, v in question["criteria"].items())
    return f"{header}\n{note}Decide from the snapshot:\n{criteria}\n\n{lines}"
