"""Pre-trade Hyperliquid pair validation for execution safety."""

CATEGORY = "Execution Safety"

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult


class Config(BaseModel):
    """Validate that a Hyperliquid pair is available for execution."""

    connector_name: str = Field(
        default="hyperliquid_perpetual",
        description="Execution connector to validate",
    )
    trading_pair: str = Field(
        default="BTC-USD",
        description="Execution trading pair to validate (Hyperliquid uses -USD)",
    )
    check_interval: str = Field(
        default="1m",
        description="Candle interval used for readiness check",
    )
    max_records: int = Field(
        default=20,
        ge=5,
        le=200,
        description="Number of candles to request for readiness check",
    )


async def run(
    config: Config, context: ContextTypes.DEFAULT_TYPE
) -> str | RoutineResult:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    price_error = ""
    candles_error = ""
    latest_price = None
    candles_count = 0

    try:
        prices = await client.market_data.get_prices(
            connector_name=config.connector_name,
            trading_pairs=[config.trading_pair],
        )
        if isinstance(prices, dict):
            latest_price = (prices.get("prices") or {}).get(config.trading_pair)
    except Exception as e:
        price_error = str(e)

    try:
        candles = await client.market_data.get_candles(
            connector_name=config.connector_name,
            trading_pair=config.trading_pair,
            interval=config.check_interval,
            max_records=config.max_records,
        )
        if isinstance(candles, dict):
            candles = candles.get("data", [])
        candles_count = len(candles) if isinstance(candles, list) else 0
    except Exception as e:
        candles_error = str(e)

    has_price = latest_price is not None
    has_candles = candles_count > 0
    ready = has_price and has_candles
    status = "READY" if ready else "UNAVAILABLE"

    lines = [
        f"Hyperliquid Pair Check — {config.trading_pair}",
        f"Connector: {config.connector_name}",
        f"Status: {status}",
        f"Price available: {'yes' if has_price else 'no'}",
        f"Candles available: {'yes' if has_candles else 'no'} (count={candles_count})",
    ]
    if has_price:
        lines.append(f"Latest price: {latest_price}")
    if price_error:
        lines.append(f"Price check error: {price_error}")
    if candles_error:
        lines.append(f"Candle check error: {candles_error}")
    if not ready:
        lines.append(
            "Reason: execution feed unavailable or still warming up. Treat as temporary and skip this pair for now."
        )

    text = "\n".join(lines)

    table_columns = [
        "Pair",
        "Connector",
        "Status",
        "Price Available",
        "Candles Available",
        "Candle Count",
        "Latest Price",
    ]
    table_data = [
        {
            "Pair": config.trading_pair,
            "Connector": config.connector_name,
            "Status": status,
            "Price Available": has_price,
            "Candles Available": has_candles,
            "Candle Count": candles_count,
            "Latest Price": latest_price if latest_price is not None else "",
        }
    ]

    return RoutineResult(
        text=text,
        table_data=table_data,
        table_columns=table_columns,
        sections=[
            {
                "type": "kpi",
                "label": "Execution Readiness",
                "value": status,
                "trend": "positive" if ready else "negative",
            }
        ],
    )
