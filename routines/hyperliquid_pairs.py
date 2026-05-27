"""Fetch available Hyperliquid perpetual trading pairs from connector rules."""

CATEGORY = "Market Data"

import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """List tradable pairs from a connector (default: Hyperliquid perpetual)."""

    connector_name: str = Field(
        default="hyperliquid_perpetual",
        description="Connector to fetch trading pairs from",
    )
    quote_filter: str = Field(
        default="",
        description="Optional quote filter (e.g. USDC). Leave empty for all",
    )
    hip3_only: bool = Field(
        default=False,
        description="If true, only show HIP3 issuer:symbol pairs",
    )
    max_rows: int = Field(
        default=300,
        ge=10,
        le=2000,
        description="Maximum rows to return in table output",
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str | RoutineResult:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    try:
        trading_rules = await client.connectors.get_trading_rules(
            connector_name=config.connector_name
        )
    except Exception as e:
        return f"Failed to fetch trading rules for {config.connector_name}: {e}"

    if not isinstance(trading_rules, dict) or not trading_rules:
        return f"No trading rules returned for {config.connector_name}"

    all_pairs = sorted(list(trading_rules.keys()))
    filtered_pairs = all_pairs

    if config.hip3_only:
        filtered_pairs = [pair for pair in filtered_pairs if ":" in pair]

    quote_filter = config.quote_filter.strip().upper()
    if quote_filter:
        suffix = f"-{quote_filter}"
        filtered_pairs = [pair for pair in filtered_pairs if pair.upper().endswith(suffix)]

    hip3_count = sum(1 for pair in all_pairs if ":" in pair)
    returned_pairs = filtered_pairs[: config.max_rows]

    lines = [
        f"Connector Pair List — {config.connector_name}",
        f"Total pairs (raw): {len(all_pairs)}",
        f"HIP3 pairs (raw): {hip3_count}",
        f"Filters: hip3_only={config.hip3_only}, quote_filter={quote_filter or 'none'}",
        f"Returned rows: {len(returned_pairs)} / {len(filtered_pairs)}",
    ]
    if len(filtered_pairs) > config.max_rows:
        lines.append(f"Truncated by max_rows={config.max_rows}")
    text = "\n".join(lines)

    table_data = [
        {
            "Pair": pair,
            "HIP3": ":" in pair,
            "Quote": pair.rsplit("-", 1)[-1] if "-" in pair else "",
        }
        for pair in returned_pairs
    ]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Connector Pairs: {config.connector_name}")
        builder.source("routine", "hyperliquid_pairs").tags(
            ["pairs", config.connector_name]
        )
        builder.markdown(text)
        builder.table(table_data, columns=["Pair", "HIP3", "Quote"])
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(
        text=text,
        table_data=table_data,
        table_columns=["Pair", "HIP3", "Quote"],
        sections=[
            {"type": "kpi", "label": "Total Pairs", "value": str(len(all_pairs))},
            {"type": "kpi", "label": "HIP3 Pairs", "value": str(hip3_count)},
            {"type": "kpi", "label": "Returned Rows", "value": str(len(returned_pairs))},
        ],
    )
