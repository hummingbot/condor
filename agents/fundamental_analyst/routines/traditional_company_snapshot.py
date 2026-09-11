import asyncio
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder

CATEGORY = "Analysis"

_INDUSTRY_PEERS: dict[str, list[str]] = {
    "Internet Content & Information": ["GOOGL", "META", "SNAP"],
    "Software—Application": ["MSFT", "ORCL", "SAP"],
    "Software—Infrastructure": ["MSFT", "CSCO", "VMW"],
    "Semiconductors": ["NVDA", "AMD", "INTC", "TSM", "QCOM"],
    "Consumer Electronics": ["AAPL", "SONY", "SSNGY"],
    "Financial Data & Stock Exchanges": ["MSCI", "SPGI", "ICE"],
    "Capital Markets": ["GS", "MS", "SCHW"],
    "Banks—Diversified": ["JPM", "BAC", "WFC"],
    "Credit Services": ["V", "MA", "AXP"],
    "Cryptocurrency": ["COIN", "MSTR", "HOOD"],
    "Financial Services": ["PYPL", "SQ", "AFRM"],
    "Insurance—Life": ["MET", "PRU", "LNC"],
    "Drug Manufacturers—General": ["JNJ", "PFE", "MRK"],
    "Biotechnology": ["AMGN", "GILD", "REGN"],
    "Online Retail": ["AMZN", "EBAY", "ETSY"],
    "Specialty Retail": ["AMZN", "TGT", "WMT"],
    "Auto Manufacturers": ["TSLA", "GM", "F"],
    "Oil & Gas—Integrated": ["XOM", "CVX", "BP"],
    "Telecom Services": ["T", "VZ", "TMUS"],
    "Entertainment": ["NFLX", "DIS", "WBD"],
}


class Config(BaseModel):
    """Public company fundamental snapshot — revenue, margins, multiples via yfinance."""

    ticker: str = Field(
        default="AAPL",
        description="Stock ticker symbol (e.g. 'AAPL', 'COIN', 'NVDA')",
    )
    include_competitors: bool = Field(
        default=True,
        description="Include 2-3 sector peers in the comparison table",
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _fmt_usd(val) -> str:
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if abs(val) >= 1e12:
        return f"${val / 1e12:.2f}T"
    if abs(val) >= 1e9:
        return f"${val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.2f}M"
    if abs(val) >= 1e3:
        return f"${val / 1e3:.2f}K"
    return f"${val:.2f}"


def _fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_mult(val) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.2f}x"
    except (TypeError, ValueError):
        return "N/A"


def _safe_div(num, denom):
    try:
        n, d = float(num), float(denom)
        return n / d if d != 0 else None
    except (TypeError, ValueError):
        return None


def _ttm_sum(df, *labels) -> float | None:
    """Sum 4 most recent quarterly values for any matching label."""
    if df is None or df.empty:
        return None
    for label in labels:
        if label in df.index:
            row = df.loc[label]
            vals = []
            for v in row.iloc[:4]:
                try:
                    f = float(v)
                    if f == f:  # not NaN
                        vals.append(f)
                except (TypeError, ValueError):
                    pass
            if vals:
                return sum(vals)
    return None


def _get_ticker_data(symbol: str) -> dict:
    """Synchronous yfinance fetch. Call via run_in_executor."""
    import yfinance as yf

    t = yf.Ticker(symbol)
    info = t.info or {}

    try:
        q_fin = t.quarterly_financials
    except Exception:
        q_fin = None
    try:
        q_cf = t.quarterly_cashflow
    except Exception:
        q_cf = None
    try:
        ann_fin = t.financials
    except Exception:
        ann_fin = None

    # TTM income statement
    rev_ttm = _ttm_sum(q_fin, "Total Revenue")
    gross_ttm = _ttm_sum(q_fin, "Gross Profit")
    op_inc_ttm = _ttm_sum(q_fin, "Operating Income", "Operating Income Loss")
    net_inc_ttm = _ttm_sum(q_fin, "Net Income", "Net Income Common Stockholders")

    # FCF TTM — prefer direct row, fall back to Op CF - Capex
    fcf_ttm = _ttm_sum(q_cf, "Free Cash Flow")
    if fcf_ttm is None:
        op_cf = _ttm_sum(
            q_cf,
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
        )
        capex = _ttm_sum(q_cf, "Capital Expenditure", "Capital Expenditures")
        if op_cf is not None and capex is not None:
            fcf_ttm = op_cf + capex  # capex is stored as negative
        elif op_cf is not None:
            fcf_ttm = op_cf  # best-effort if no capex line

    # Annual revenue for growth
    ann_rev = []
    if ann_fin is not None and not ann_fin.empty and "Total Revenue" in ann_fin.index:
        row = ann_fin.loc["Total Revenue"]
        for col in list(row.index)[:3]:
            try:
                v = float(row[col])
                if v == v:
                    ann_rev.append((str(col)[:4], v))
            except (TypeError, ValueError):
                pass

    rev_growth_yoy = None
    if len(ann_rev) >= 2:
        r1, r0 = ann_rev[0][1], ann_rev[1][1]
        if r0 and r0 != 0:
            rev_growth_yoy = (r1 - r0) / abs(r0)

    mcap = info.get("marketCap")
    ev = info.get("enterpriseValue")
    pe = info.get("trailingPE")
    ps = info.get("priceToSalesTrailing12Months")
    ev_rev = info.get("enterpriseToRevenue")
    ev_ebitda = info.get("enterpriseToEbitda")
    pfcf = _safe_div(mcap, fcf_ttm) if mcap and fcf_ttm else None
    net_margin = _safe_div(net_inc_ttm, rev_ttm)
    gross_margin = _safe_div(gross_ttm, rev_ttm)
    fcf_yield = _safe_div(fcf_ttm, mcap) if mcap else None

    return {
        "symbol": symbol.upper(),
        "name": info.get("longName") or info.get("shortName") or symbol.upper(),
        "sector": info.get("sector") or "N/A",
        "industry": info.get("industry") or "N/A",
        "mcap": mcap,
        "ev": ev,
        "shares_outstanding": info.get("sharesOutstanding"),
        "rev_ttm": rev_ttm,
        "gross_ttm": gross_ttm,
        "op_inc_ttm": op_inc_ttm,
        "net_inc_ttm": net_inc_ttm,
        "fcf_ttm": fcf_ttm,
        "ann_rev": ann_rev,
        "rev_growth_yoy": rev_growth_yoy,
        "pe": pe,
        "ps": ps,
        "ev_rev": ev_rev,
        "ev_ebitda": ev_ebitda,
        "pfcf": pfcf,
        "net_margin": net_margin,
        "gross_margin": gross_margin,
        "fcf_yield": fcf_yield,
        "div_yield": info.get("dividendYield"),
        "payout_ratio": info.get("payoutRatio"),
    }


def _get_peer_tickers(data: dict) -> list[str]:
    symbol = data["symbol"]
    industry = data["industry"]
    if industry in _INDUSTRY_PEERS:
        return [t for t in _INDUSTRY_PEERS[industry] if t != symbol][:3]
    sector = data["sector"]
    for key, tickers in _INDUSTRY_PEERS.items():
        if sector.lower() in key.lower() or key.lower() in sector.lower():
            return [t for t in tickers if t != symbol][:3]
    return []


# ── main ─────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    now = datetime.now(timezone.utc)
    ticker_upper = config.ticker.upper().strip()
    loop = asyncio.get_event_loop()

    try:
        data = await loop.run_in_executor(None, _get_ticker_data, ticker_upper)
    except Exception as e:
        return f"Failed to fetch data for '{ticker_upper}': {e}"

    if not data.get("mcap") and not data.get("rev_ttm"):
        return f"No financial data found for ticker '{ticker_upper}'. Check the symbol."

    # Peers
    peer_rows = []
    if config.include_competitors:
        peer_tickers = _get_peer_tickers(data)
        if peer_tickers:
            peer_results = await asyncio.gather(
                *[
                    loop.run_in_executor(None, _get_ticker_data, t)
                    for t in peer_tickers
                ],
                return_exceptions=True,
            )
            for pr in peer_results:
                if isinstance(pr, Exception):
                    continue
                peer_rows.append(
                    {
                        "Company": pr["name"],
                        "Ticker": pr["symbol"],
                        "MCap": _fmt_usd(pr.get("mcap")),
                        "Rev TTM": _fmt_usd(pr.get("rev_ttm")),
                        "Net Margin": _fmt_pct(pr.get("net_margin")),
                        "P/E": _fmt_mult(pr.get("pe")),
                        "EV/Rev": _fmt_mult(pr.get("ev_rev")),
                    }
                )

    # ── Report ────────────────────────────────────────────────────────────────
    name = data["name"]
    mcap = data["mcap"]
    ev = data["ev"]
    rev_ttm = data["rev_ttm"]
    net_inc_ttm = data["net_inc_ttm"]
    fcf_ttm = data["fcf_ttm"]

    builder = ReportBuilder(f"{name} ({ticker_upper}) — Fundamental Snapshot")
    builder.source("routine", "traditional_company_snapshot")
    builder.tags(["equity", "fundamentals", data["sector"].lower().replace(" ", "-")])

    builder.section("01 / COMPANY", f"{name} | {now.strftime('%Y-%m-%d %H:%M UTC')}")
    builder.kpi("Ticker", ticker_upper)
    builder.kpi("Sector", data["sector"])
    builder.kpi("Industry", data["industry"])
    builder.kpi("Market Cap", _fmt_usd(mcap))
    builder.kpi("Enterprise Value", _fmt_usd(ev))
    builder.kpi("Shares Outstanding", _fmt_usd(data.get("shares_outstanding")))

    builder.section(
        "02 / INCOME (TTM)", "Trailing twelve months — sum of 4 most recent quarters"
    )
    builder.kpi("Revenue TTM", _fmt_usd(rev_ttm))
    builder.kpi("Gross Profit TTM", _fmt_usd(data.get("gross_ttm")))
    builder.kpi("Operating Income TTM", _fmt_usd(data.get("op_inc_ttm")))
    builder.kpi("Net Income TTM", _fmt_usd(net_inc_ttm))
    builder.kpi("Free Cash Flow TTM", _fmt_usd(fcf_ttm))

    ann_rev = data.get("ann_rev", [])
    if ann_rev:
        builder.section("03 / REVENUE TREND", "Last 3 annual fiscal years")
        for year, val in ann_rev:
            builder.kpi(f"Revenue {year}", _fmt_usd(val))
        if data.get("rev_growth_yoy") is not None:
            builder.kpi("YoY Revenue Growth", _fmt_pct(data["rev_growth_yoy"]))

    builder.section("04 / MARGINS & YIELD", "")
    builder.kpi("Gross Margin", _fmt_pct(data.get("gross_margin")))
    builder.kpi("Net Margin", _fmt_pct(data.get("net_margin")))
    if data.get("fcf_yield"):
        builder.kpi("FCF Yield", _fmt_pct(data["fcf_yield"]))
    if data.get("div_yield"):
        builder.kpi("Dividend Yield", _fmt_pct(data["div_yield"]))
        if data.get("payout_ratio"):
            builder.kpi("Payout Ratio", _fmt_pct(data["payout_ratio"]))

    builder.section("05 / MULTIPLES", "Yahoo Finance TTM or trailing 12m multiples")
    builder.kpi("P/E (trailing)", _fmt_mult(data.get("pe")))
    builder.kpi("P/S (trailing 12m)", _fmt_mult(data.get("ps")))
    builder.kpi("P/FCF", _fmt_mult(data.get("pfcf")))
    builder.kpi("EV / Revenue", _fmt_mult(data.get("ev_rev")))
    builder.kpi("EV / EBITDA", _fmt_mult(data.get("ev_ebitda")))

    if peer_rows:
        subject_row = {
            "Company": f"★ {name}",
            "Ticker": ticker_upper,
            "MCap": _fmt_usd(mcap),
            "Rev TTM": _fmt_usd(rev_ttm),
            "Net Margin": _fmt_pct(data.get("net_margin")),
            "P/E": _fmt_mult(data.get("pe")),
            "EV/Rev": _fmt_mult(data.get("ev_rev")),
        }
        builder.section(
            "06 / PEER COMPARISON", f"Peers in {data['industry']} (★ = subject)"
        )
        builder.table(
            [subject_row] + peer_rows,
            ["Company", "Ticker", "MCap", "Rev TTM", "Net Margin", "P/E", "EV/Rev"],
        )

    builder.section("07 / META", "")
    builder.kpi("Data Source", "Yahoo Finance (yfinance)")
    builder.kpi("Fetched At", now.strftime("%Y-%m-%d %H:%M UTC"))

    builder.manual_order()
    report_id = await builder.save()

    return (
        f"**{name}** ({ticker_upper}) | {data['sector']}\n"
        f"MCap: {_fmt_usd(mcap)} | EV: {_fmt_usd(ev)}\n"
        f"Rev TTM: {_fmt_usd(rev_ttm)} | Net Inc: {_fmt_usd(net_inc_ttm)} | FCF: {_fmt_usd(fcf_ttm)}\n"
        f"P/E: {_fmt_mult(data.get('pe'))} | EV/Rev: {_fmt_mult(data.get('ev_rev'))} | Net Margin: {_fmt_pct(data.get('net_margin'))}\n"
        f"Report → {report_id}"
    )
