"""Hold, start or stop one directional position, answered by Drex when NACE_API_KEY is set."""

CATEGORY = "Analysis"

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.agents.drex_gate import decide
from config_manager import get_client


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


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    side = config.side.strip().upper()
    if side not in ("LONG", "SHORT"):
        return f"side must be LONG or SHORT, got {config.side!r}"

    client = await get_client(getattr(context, "_chat_id", None), context=context)
    if not client:
        return "No server available"

    _, text = await decide(
        client,
        connector_name=config.connector_name,
        trading_pair=config.trading_pair,
        interval=config.interval,
        side=side,
        entry_price=config.entry_price,
        start_when=config.start_when,
        stop_when=config.stop_when,
    )
    return text
