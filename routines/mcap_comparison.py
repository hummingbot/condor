"""Compare market cap, fees, and revenue for Solana DEX tokens (ORCA, MET, RAY)."""

CATEGORY = "Market Data"

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp
import pandas as pd
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

# Token configs: name -> (GeckoTerminal mint, Binance pair, DefiLlama slug)
DEFAULT_TOKENS = {
    "ORCA": {
        "mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        "trading_pair": "ORCA-USDT",
        "defillama": "orca",
    },
    "MET": {
        "mint": "METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL",
        "trading_pair": "MET-USDT",
        "defillama": "meteora",
    },
    "RAY": {
        "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        "trading_pair": "RAY-USDT",
        "defillama": "raydium",
    },
}

CONNECTOR = "binance"
DEFILLAMA_BASE = "https://api.llama.fi/summary/fees"


class Config(BaseModel):
    """Compare market cap and fee/revenue of DEX tokens over time. Default: ORCA, MET, RAY."""

    tokens: str = Field(
        default="ORCA,MET,RAY",
        description="Comma-separated token names (ORCA, MET, RAY)",
    )
    days: int = Field(default=90, description="Number of days of history")
    interval: str = Field(default="1d", description="Candle interval: 1d, 4h, 1h")
    normalize: bool = Field(
        default=True, description="Normalize to 100 at start for relative comparison"
    )
    send_to_telegram: bool = Field(default=True, description="Send chart to Telegram")


def _fmt_mcap(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v / 1e3:.0f}K"


def _fmt_pct(v: float) -> str:
    return f"{v:+.1f}%"


async def _fetch_token_info(session: aiohttp.ClientSession, mint: str) -> dict:
    """Fetch current token info (price, fdv, mcap) from GeckoTerminal."""
    url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}"
    async with session.get(url) as resp:
        data = await resp.json()
    attrs = data.get("data", {}).get("attributes", {})
    price = float(attrs.get("price_usd") or 0)
    fdv = float(attrs.get("fdv_usd") or 0)
    mcap = float(attrs.get("market_cap_usd") or 0)
    return {"price": price, "fdv": fdv, "mcap": mcap or fdv}


async def _fetch_defillama_fees(session: aiohttp.ClientSession, protocol: str) -> dict:
    """Fetch fee/revenue data from DefiLlama."""
    url = f"{DEFILLAMA_BASE}/{protocol}"
    async with session.get(url) as resp:
        data = await resp.json()

    # Extract time series: [[timestamp, value], ...]
    chart = data.get("totalDataChart", [])
    df = pd.DataFrame(chart, columns=["timestamp", "fees"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

    return {
        "total24h": data.get("total24h", 0),
        "total7d": data.get("total7d", 0),
        "total30d": data.get("total30d", 0),
        "totalAllTime": data.get("totalAllTime", 0),
        "chart": df,
    }


async def _fetch_candles(
    context: ContextTypes.DEFAULT_TYPE, trading_pair: str, interval: str, days: int
) -> pd.DataFrame:
    """Fetch candles from Binance via the Hummingbot backend API."""
    client = await get_client(context._chat_id)
    result = await client.market_data.get_candles_last_days(
        CONNECTOR, trading_pair, days=days, interval=interval
    )
    if isinstance(result, list):
        records = result
    elif isinstance(result, dict):
        records = result.get("data", result.get("candles", []))
    else:
        records = []
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "timestamp" in df.columns:
        ts = df["timestamp"]
        unit = "s" if ts.iloc[0] > 1e12 / 1000 else "ms"
        df["date"] = pd.to_datetime(ts, unit=unit, utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    return df


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    # Parse requested tokens
    requested = [t.strip().upper() for t in config.tokens.split(",") if t.strip()]
    active_tokens = {
        name: info for name, info in DEFAULT_TOKENS.items() if name in requested
    }
    if not active_tokens:
        return f"No known tokens in '{config.tokens}'. Available: {', '.join(DEFAULT_TOKENS.keys())}"

    token_info = {}
    fee_data = {}
    price_data = {}

    # Step 1: Fetch GeckoTerminal token info + DefiLlama fees (parallel, aiohttp)
    async with aiohttp.ClientSession() as session:
        # Token info from GeckoTerminal (sequential — rate limits)
        for name, td in active_tokens.items():
            try:
                token_info[name] = await _fetch_token_info(session, td["mint"])
                logger.info(
                    f"{name}: price=${token_info[name]['price']:.4f}, mcap={_fmt_mcap(token_info[name]['mcap'])}"
                )
            except Exception as e:
                logger.warning(f"Failed to fetch token info for {name}: {e}")
                token_info[name] = {"price": 0, "fdv": 0, "mcap": 0}
            await asyncio.sleep(1)

        # DefiLlama fees (parallel — no rate limit issues)
        fee_tasks = {
            name: _fetch_defillama_fees(session, td["defillama"])
            for name, td in active_tokens.items()
        }
        fee_results = await asyncio.gather(*fee_tasks.values(), return_exceptions=True)
        for name, result in zip(fee_tasks.keys(), fee_results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to fetch fees for {name}: {result}")
                fee_data[name] = {
                    "total24h": 0,
                    "total7d": 0,
                    "total30d": 0,
                    "totalAllTime": 0,
                    "chart": pd.DataFrame(),
                }
            else:
                fee_data[name] = result

    # Step 2: Fetch price candles from Binance (sequential — same connector)
    for name, td in active_tokens.items():
        try:
            df = await _fetch_candles(
                context, td["trading_pair"], config.interval, config.days
            )
            if df.empty:
                logger.warning(f"No candle data for {name}")
                continue

            info = token_info[name]
            if info["price"] > 0 and info["mcap"] > 0:
                implied_supply = info["mcap"] / info["price"]
                df["mcap"] = df["close"] * implied_supply
            else:
                df["mcap"] = 0

            df["token"] = name
            price_data[name] = df
        except Exception as e:
            logger.warning(f"Failed to fetch candles for {name}: {e}")

    if not price_data and not fee_data:
        return "Failed to fetch data for all tokens. Try again shortly."

    # Build summary
    lines = ["*Market Cap Comparison — Solana DEX Tokens*", ""]

    # MCap section
    for name in active_tokens:
        info = token_info.get(name, {})
        current_mcap = info.get("mcap", 0)
        if name in price_data and not price_data[name].empty:
            first_mcap = price_data[name]["mcap"].iloc[0]
            change = (
                ((current_mcap - first_mcap) / first_mcap * 100)
                if first_mcap > 0
                else 0
            )
            lines.append(
                f"- {name}: {_fmt_mcap(current_mcap)} ({_fmt_pct(change)} over {config.days}d)"
            )
        else:
            lines.append(
                f"- {name}: {_fmt_mcap(current_mcap) if current_mcap else 'no data'}"
            )

    # Fee/Revenue section
    lines.append("")
    lines.append("*Protocol Fees (DefiLlama)*")
    for name in active_tokens:
        fd = fee_data.get(name, {})
        f24 = fd.get("total24h", 0)
        f7d = fd.get("total7d", 0)
        f30d = fd.get("total30d", 0)
        lines.append(
            f"- {name}: 24h {_fmt_mcap(f24)} | 7d {_fmt_mcap(f7d)} | 30d {_fmt_mcap(f30d)}"
        )

    # MCap/Fees ratio (valuation)
    lines.append("")
    lines.append("*MCap / 30d Fees (lower = cheaper)*")
    for name in active_tokens:
        mcap = token_info.get(name, {}).get("mcap", 0)
        f30d = fee_data.get(name, {}).get("total30d", 0)
        if mcap > 0 and f30d > 0:
            annualized = f30d * 12
            ratio = mcap / annualized
            lines.append(f"- {name}: {ratio:.1f}x (ann. fees {_fmt_mcap(annualized)})")
        else:
            lines.append(f"- {name}: N/A")

    summary = "\n".join(lines)

    # Build charts
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        colors = {"ORCA": "#00D1FF", "MET": "#E8A317", "RAY": "#8B5CF6"}

        # Figure with 2 rows: mcap + fees
        fig = make_subplots(
            rows=2,
            cols=1,
            row_heights=[0.55, 0.45],
            shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=["Market Cap Evolution", "Daily Protocol Fees"],
        )

        # Row 1: Market cap
        y_label = "Market Cap (USD)"
        for name in active_tokens:
            if name not in price_data:
                continue
            df = price_data[name]
            if df.empty or df["mcap"].sum() == 0:
                continue
            y_values = df["mcap"]
            if config.normalize and y_values.iloc[0] > 0:
                y_values = (y_values / y_values.iloc[0]) * 100
                y_label = "Indexed (100 = start)"
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=y_values,
                    mode="lines",
                    name=name,
                    line=dict(color=colors.get(name, "#FFFFFF"), width=2),
                    hovertemplate=f"{name}<br>%{{x|%b %d}}<br>%{{y:,.0f}}<extra></extra>",
                    legendgroup=name,
                ),
                row=1,
                col=1,
            )

        # Row 2: Daily fees (7d rolling avg for smoothing)
        for name in active_tokens:
            fd = fee_data.get(name, {})
            chart_df = fd.get("chart", pd.DataFrame())
            if chart_df.empty:
                continue
            # Filter to requested days
            cutoff = datetime.now(timezone.utc).timestamp() - (config.days * 86400)
            chart_df = chart_df[chart_df["timestamp"] >= cutoff].copy()
            if chart_df.empty:
                continue
            chart_df["fees_7d_avg"] = chart_df["fees"].rolling(7, min_periods=1).mean()
            fig.add_trace(
                go.Scatter(
                    x=chart_df["date"],
                    y=chart_df["fees_7d_avg"],
                    mode="lines",
                    name=f"{name} fees",
                    line=dict(color=colors.get(name, "#FFFFFF"), width=2, dash="dot"),
                    hovertemplate=f"{name}<br>%{{x|%b %d}}<br>${{y:,.0f}}<extra></extra>",
                    legendgroup=name,
                    showlegend=False,
                ),
                row=2,
                col=1,
            )

        fig.update_layout(
            title=f"Solana DEX Comparison — {config.days}d",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=11),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1
            ),
            margin=dict(l=60, r=30, t=110, b=40),
            hovermode="x unified",
            height=700,
        )
        for row in [1, 2]:
            fig.update_xaxes(gridcolor="#21262d", row=row, col=1)
            fig.update_yaxes(gridcolor="#21262d", row=row, col=1)
        fig.update_yaxes(title_text=y_label, tickformat=",.0f", row=1, col=1)
        fig.update_yaxes(
            title_text="Daily Fees (7d avg, USD)", tickformat="$,.0f", row=2, col=1
        )

        # Send to Telegram
        if config.send_to_telegram and getattr(context, "bot", None):
            import io

            chat_id = context._chat_id if hasattr(context, "_chat_id") else None
            if chat_id:
                buf = io.BytesIO()
                fig.write_image(buf, format="png", width=900, height=700, scale=2)
                buf.seek(0)
                await context.bot.send_photo(
                    chat_id=chat_id, photo=buf, caption=summary, parse_mode="Markdown"
                )

        # Save HTML report
        try:
            from condor.reports import ReportBuilder

            builder = ReportBuilder(f"Solana DEX Comparison — {config.days}d")
            builder.source("routine", "mcap_comparison").tags(
                ["market-cap", "fees", "solana"] + [n.lower() for n in active_tokens]
            )
            builder.markdown(summary)
            builder.plotly(fig)

            table_rows = []
            for name in active_tokens:
                info = token_info.get(name, {})
                fd = fee_data.get(name, {})
                df = price_data.get(name, pd.DataFrame())
                first_mcap = df["mcap"].iloc[0] if not df.empty else 0
                change = (
                    ((info.get("mcap", 0) - first_mcap) / first_mcap * 100)
                    if first_mcap > 0
                    else 0
                )
                f30d = fd.get("total30d", 0)
                annualized = f30d * 12
                mcap = info.get("mcap", 0)
                table_rows.append(
                    {
                        "Token": name,
                        "Price": f"${info.get('price', 0):.4f}",
                        "MCap": _fmt_mcap(mcap),
                        f"{config.days}d Chg": _fmt_pct(change),
                        "FDV": _fmt_mcap(info.get("fdv", 0)),
                        "Fees 24h": _fmt_mcap(fd.get("total24h", 0)),
                        "Fees 30d": _fmt_mcap(f30d),
                        "Ann. Fees": _fmt_mcap(annualized),
                        "MCap/Ann.Fees": (
                            f"{mcap / annualized:.1f}x" if annualized > 0 else "N/A"
                        ),
                    }
                )
            builder.table(table_rows)
            await builder.save()
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")

    except Exception as e:
        logger.warning(f"Chart generation failed: {e}")

    return RoutineResult(text=summary)
