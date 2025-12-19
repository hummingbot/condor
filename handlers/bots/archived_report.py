"""
Archived Bots Report Generation

Saves comprehensive reports to local filesystem:
- JSON file with all bot data
- PNG chart file with performance visualization
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

# Reports directory in project root
REPORTS_DIR = Path("reports")


def ensure_reports_dir() -> Path:
    """Create reports directory if it doesn't exist."""
    REPORTS_DIR.mkdir(exist_ok=True)
    return REPORTS_DIR


def _extract_bot_name(db_path: str) -> str:
    """Extract readable bot name from database path."""
    name = os.path.basename(db_path)
    if name.endswith(".sqlite"):
        name = name[:-7]
    elif name.endswith(".db"):
        name = name[:-3]
    return name


def generate_report_filename(db_path: str) -> str:
    """
    Generate a unique filename for the report.

    Format: {bot_name}_{YYYYMMDD_HHMMSS}
    """
    # Extract bot name from db_path
    bot_name = _extract_bot_name(db_path)
    # Sanitize bot name for filename
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in bot_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_name}_{timestamp}"


def _serialize_datetime(obj):
    """JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def build_report_json(
    db_path: str,
    summary: Dict[str, Any],
    performance: Optional[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    executors: List[Dict[str, Any]],
    pnl_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the complete JSON report structure.

    Args:
        db_path: Path to the database file
        summary: BotSummary data
        performance: BotPerformanceResponse data
        trades: List of TradeDetail objects
        orders: List of OrderDetail objects
        executors: List of ExecutorInfo objects
        pnl_data: Calculated PnL data from trades

    Returns:
        Complete report dictionary
    """
    # Calculate time range from trades
    start_time = None
    end_time = None
    if trades:
        timestamps = [t.get("timestamp") for t in trades if t.get("timestamp")]
        if timestamps:
            # Convert milliseconds to ISO format
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            if min_ts > 1e12:
                min_ts = min_ts / 1000
            if max_ts > 1e12:
                max_ts = max_ts / 1000
            start_time = datetime.fromtimestamp(min_ts).isoformat()
            end_time = datetime.fromtimestamp(max_ts).isoformat()

    return {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "db_path": db_path,
            "bot_name": _extract_bot_name(db_path),
            "generator": "condor",
            "version": "1.1.0",
        },
        "summary": summary,
        "calculated_pnl": pnl_data or {},
        "period": {
            "start": start_time,
            "end": end_time,
        },
        "trades": trades,
        "orders": orders,
        "executors": executors,
        "statistics": {
            "total_trades": len(trades),
            "total_orders": len(orders),
            "total_executors": len(executors),
        },
    }


async def save_full_report(
    client,
    db_path: str,
    include_chart: bool = True
) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch all data and save comprehensive report.

    Args:
        client: API client with archived_bots router
        db_path: Path to the database file
        include_chart: Whether to generate and save a PNG chart

    Returns:
        Tuple of (json_path, png_path) or (json_path, None) if no chart
    """
    try:
        # Ensure reports directory exists
        ensure_reports_dir()

        # Fetch all data
        logger.info(f"Fetching data for report: {db_path}")

        summary = await client.archived_bots.get_database_summary(db_path)
        if not summary:
            logger.error(f"Could not fetch summary for {db_path}")
            return None, None

        # Performance may fail for some databases - continue anyway
        try:
            performance = await client.archived_bots.get_database_performance(db_path)
        except Exception as e:
            logger.warning(f"Could not fetch performance for {db_path}: {e}")
            performance = None

        # Fetch trades with pagination (get all)
        all_trades = []
        offset = 0
        limit = 500
        while True:
            trades_response = await client.archived_bots.get_database_trades(
                db_path, limit=limit, offset=offset
            )
            if not trades_response:
                break
            trades = trades_response.get("trades", [])
            if not trades:
                break
            all_trades.extend(trades)
            if len(trades) < limit:
                break
            offset += limit
        logger.info(f"Fetched {len(all_trades)} trades")

        # Fetch orders with pagination
        all_orders = []
        offset = 0
        while True:
            orders_response = await client.archived_bots.get_database_orders(
                db_path, limit=limit, offset=offset
            )
            if not orders_response:
                break
            orders = orders_response.get("orders", [])
            if not orders:
                break
            all_orders.extend(orders)
            if len(orders) < limit:
                break
            offset += limit
        logger.info(f"Fetched {len(all_orders)} orders")

        # Fetch executors
        executors_response = await client.archived_bots.get_database_executors(db_path)
        executors = executors_response.get("executors", []) if executors_response else []
        logger.info(f"Fetched {len(executors)} executors")

        # Calculate PnL from trades
        from .archived_chart import calculate_pnl_from_trades
        pnl_data = calculate_pnl_from_trades(all_trades)
        logger.info(f"Calculated PnL: ${pnl_data.get('total_pnl', 0):.2f}")

        # Build report
        filename = generate_report_filename(db_path)

        report_data = build_report_json(
            db_path=db_path,
            summary=summary,
            performance=performance,
            trades=all_trades,
            orders=all_orders,
            executors=executors,
            pnl_data=pnl_data,
        )

        # Save JSON
        json_path = REPORTS_DIR / f"{filename}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, default=_serialize_datetime)
        logger.info(f"Saved JSON report to {json_path}")

        # Generate and save chart
        png_path = None
        if include_chart:
            try:
                from .archived_chart import generate_report_chart

                chart_bytes = generate_report_chart(
                    summary=summary,
                    performance=performance,
                    trades=all_trades,
                    executors=executors,
                    db_path=db_path,
                )

                if chart_bytes:
                    png_path = REPORTS_DIR / f"{filename}.png"
                    with open(png_path, "wb") as f:
                        f.write(chart_bytes.read())
                    logger.info(f"Saved chart to {png_path}")
                    png_path = str(png_path)
            except Exception as e:
                logger.error(f"Error generating chart for report: {e}", exc_info=True)

        return str(json_path), png_path

    except Exception as e:
        logger.error(f"Error saving report: {e}", exc_info=True)
        return None, None


def list_reports() -> List[Dict[str, Any]]:
    """
    List all saved reports in the reports directory.

    Returns:
        List of report metadata dicts
    """
    reports = []
    try:
        if not REPORTS_DIR.exists():
            return []

        for json_file in REPORTS_DIR.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                metadata = data.get("metadata", {})
                summary = data.get("summary", {})

                # Check for corresponding PNG
                png_file = json_file.with_suffix(".png")
                has_chart = png_file.exists()

                reports.append({
                    "filename": json_file.name,
                    "path": str(json_file),
                    "chart_path": str(png_file) if has_chart else None,
                    "generated_at": metadata.get("generated_at"),
                    "bot_name": summary.get("bot_name"),
                    "db_path": metadata.get("db_path"),
                })
            except Exception as e:
                logger.debug(f"Error reading report {json_file}: {e}")
                continue

        # Sort by generation time, newest first
        reports.sort(key=lambda r: r.get("generated_at", ""), reverse=True)

    except Exception as e:
        logger.error(f"Error listing reports: {e}", exc_info=True)

    return reports


def load_report(filename: str) -> Optional[Dict[str, Any]]:
    """
    Load a saved report by filename.

    Args:
        filename: Report filename (with or without .json extension)

    Returns:
        Report data dict or None if not found
    """
    try:
        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        report_path = REPORTS_DIR / filename

        if not report_path.exists():
            return None

        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        logger.error(f"Error loading report {filename}: {e}", exc_info=True)
        return None


def delete_report(filename: str) -> bool:
    """
    Delete a saved report and its chart.

    Args:
        filename: Report filename (with or without .json extension)

    Returns:
        True if deleted successfully
    """
    try:
        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        json_path = REPORTS_DIR / filename
        png_path = json_path.with_suffix(".png")

        deleted = False

        if json_path.exists():
            json_path.unlink()
            deleted = True
            logger.info(f"Deleted report {json_path}")

        if png_path.exists():
            png_path.unlink()
            logger.info(f"Deleted chart {png_path}")

        return deleted

    except Exception as e:
        logger.error(f"Error deleting report {filename}: {e}", exc_info=True)
        return False
