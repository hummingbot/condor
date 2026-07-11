from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
from pathlib import Path
import csv
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CATEGORY = "Monitoring"


class Config(BaseModel):
    """Appends current funding rates and mark prices to a CSV for historical tracking."""
    connector_name: str = Field(default="hyperliquid_perpetual", description="Exchange connector")
    trading_pairs: str = Field(default="SOL-USD,BTC-USD,ETH-USD", description="Comma-separated trading pairs")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    pairs = [p.strip() for p in config.trading_pairs.split(",") if p.strip()]
    ts = datetime.now(timezone.utc).isoformat()

    rows = []
    errors = []
    for pair in pairs:
        try:
            info = await client.market_data.get_funding_info(config.connector_name, pair)
            funding_rate = info.get("funding_rate")
            mark_price = info.get("mark_price")
            rows.append({
                "ts_iso": ts,
                "connector": config.connector_name,
                "pair": pair,
                "funding_rate": funding_rate,
                "mark_price": mark_price,
            })
        except Exception as e:
            errors.append(f"{pair}: {e}")

    # CSV lives at agents/funding_rate_watcher/store/funding_history.csv
    store_dir = Path(__file__).resolve().parent.parent / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    csv_path = store_dir / "funding_history.csv"

    columns = ["ts_iso", "connector", "pair", "funding_rate", "mark_price"]
    write_header = not csv_path.exists()

    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with csv_path.open("r") as f:
        total_rows = sum(1 for _ in f) - 1  # subtract header row

    summary = f"logged {len(rows)} pairs @ {ts}; file now {total_rows} rows"
    if errors:
        summary += f"; errors: {'; '.join(errors)}"

    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder("Funding Snapshot")
        builder.source("routine", "funding_logger").tags(["funding", "monitoring"])
        builder.kpi("Pairs Logged", str(len(rows)))
        builder.kpi("Timestamp", ts)
        builder.kpi("Total CSV Rows", str(total_rows))
        if rows:
            builder.table(
                [
                    {
                        "Pair": r["pair"],
                        "Funding Rate": f"{float(r['funding_rate']):.6f}" if r["funding_rate"] is not None else "N/A",
                        "Mark Price": f"{float(r['mark_price']):,.4f}" if r["mark_price"] is not None else "N/A",
                    }
                    for r in rows
                ],
                ["Pair", "Funding Rate", "Mark Price"],
            )
        if errors:
            builder.markdown("**Errors:**\n" + "\n".join(f"- {e}" for e in errors))
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary
