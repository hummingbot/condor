import asyncio
import difflib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder

CATEGORY = "Analysis"

_INFRA_CATEGORIES = {"Bridge", "Chain", "Infrastructure", "Staking Pool", "RWA"}
# Slug suffixes that indicate a vault/pool variant rather than the main protocol
_VAULT_SUFFIXES = ("-hlp", "-pool", "-vault", "-lp", "-yield", "-amm", "-v1", "-v4")


class Config(BaseModel):
    """Side-by-side fundamental comparison of DeFi protocols and/or stock tickers."""

    assets: List[str] = Field(
        default=["uniswap", "COIN", "hyperliquid", "HOOD"],
        description="DeFi slugs or stock tickers (e.g. ['uniswap', 'COIN', 'hyperliquid', 'HOOD'])",
    )
    asset_types: Dict[str, str] = Field(
        default={},
        description="Optional type overrides: {'COIN': 'stock', 'uniswap': 'defi'}",
    )


# ── formatters ────────────────────────────────────────────────────────────────


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


def _sum_last(chart: list, n: int) -> Optional[float]:
    if not chart:
        return None
    tail = chart[-n:]
    total = sum(v for _, v in tail if v is not None)
    return total or None


# ── classification helpers ────────────────────────────────────────────────────


def _looks_like_ticker(s: str) -> bool:
    """True for 1–5 uppercase letters (stock tickers like COIN, HOOD, AAPL)."""
    return bool(re.match(r"^[A-Z]{1,5}$", s.strip()))


def _pick_best(candidates: list):
    """From a list of protocols, prefer non-infra non-vault, then highest TVL."""
    if not candidates:
        return None
    # Exclude infrastructure categories
    trading = [p for p in candidates if p.get("category") not in _INFRA_CATEGORIES]
    pool = trading if trading else candidates
    # Prefer the main protocol over vault/pool variants (e.g. prefer hyperliquid over hyperliquid-hlp)
    core = [
        p
        for p in pool
        if not any(p.get("slug", "").endswith(s) for s in _VAULT_SUFFIXES)
    ]
    preferred = core if core else pool
    return max(preferred, key=lambda p: p.get("tvl") or 0)


def _find_slug(slug_input: str, protocols: list) -> tuple:
    """Return (protocol_dict, matched_slug) for a given input. Returns (None, input) on miss."""
    inp = slug_input.lower().strip()

    exact = next((p for p in protocols if p.get("slug", "") == inp), None)
    if exact:
        return exact, inp

    prefix = [p for p in protocols if p.get("slug", "").startswith(inp)]
    if prefix:
        best = _pick_best(prefix)
        return best, best["slug"]

    contains = [
        p
        for p in protocols
        if inp in p.get("slug", "") and not p.get("slug", "").startswith(inp)
    ]
    if contains:
        best = _pick_best(contains)
        return best, best["slug"]

    name_c = [p for p in protocols if inp in p.get("name", "").lower()]
    if name_c:
        best = _pick_best(name_c)
        return best, best["slug"]

    slugs = [p.get("slug", "") for p in protocols]
    close = difflib.get_close_matches(inp, slugs, n=3, cutoff=0.5)
    if close:
        best_slug = max(
            close,
            key=lambda s: next(
                (p.get("tvl") or 0 for p in protocols if p["slug"] == s), 0
            ),
        )
        proto = next((p for p in protocols if p["slug"] == best_slug), None)
        return proto, best_slug

    return None, slug_input


def _find_gecko_id(
    matched_slug: str, user_input: str, all_protocols: list
) -> Optional[str]:
    """Find gecko_id via DefiLlama data. Falls back to user_input only as a last resort."""
    p = next((x for x in all_protocols if x.get("slug") == matched_slug), None)
    if p and p.get("gecko_id"):
        return p["gecko_id"]

    # Brand prefix scan (strip common version/variant suffixes)
    brand = (
        matched_slug.split("-v")[0]
        if "-v" in matched_slug
        else matched_slug.split("-")[0]
    )
    candidates = [
        x
        for x in all_protocols
        if x.get("slug", "").startswith(brand) and x.get("gecko_id")
    ]
    if candidates:
        return candidates[0]["gecko_id"]

    # Only use raw user_input if it looks like a clean coin slug (no spaces, no suffixes)
    inp = user_input.lower().strip()
    if inp and " " not in inp and "-" not in inp:
        return inp
    return None


# ── DeFi fetch ────────────────────────────────────────────────────────────────


async def _fetch_fees_chart(
    client: httpx.AsyncClient, slug: str, data_type: str
) -> list:
    try:
        r = await client.get(
            f"https://api.llama.fi/summary/fees/{slug}",
            params={"dataType": data_type},
        )
        if r.status_code == 200:
            return r.json().get("totalDataChart", [])
    except Exception:
        pass
    return []


async def _fetch_coingecko_mcap(
    client: httpx.AsyncClient, gecko_id: str
) -> Optional[float]:
    if not gecko_id:
        return None
    try:
        r = await client.get(
            f"https://api.coingecko.com/api/v3/coins/{gecko_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
            },
        )
        if r.status_code == 200:
            md = r.json().get("market_data", {})
            return md.get("market_cap", {}).get("usd")
    except Exception:
        pass
    return None


async def _fetch_defi_row(
    client: httpx.AsyncClient, slug_input: str, all_protocols: list
) -> dict:
    """Fetch one DeFi protocol → normalized row dict. Never raises."""
    try:
        proto, matched_slug = _find_slug(slug_input, all_protocols)
        if proto is None:
            return {
                "asset": slug_input,
                "type": "defi",
                "error": f"Protocol '{slug_input}' not found on DefiLlama",
            }

        # Fetch detail page, fees, and revenue in parallel
        r_detail, fees_chart, rev_chart = await asyncio.gather(
            client.get(f"https://api.llama.fi/protocol/{matched_slug}"),
            _fetch_fees_chart(client, matched_slug, "dailyFees"),
            _fetch_fees_chart(client, matched_slug, "dailyRevenue"),
        )
        detail = r_detail.json() if r_detail.status_code == 200 else {}

        tvl = proto.get("tvl")
        # mcap: protocols list → detail page → CoinGecko fallback
        mcap = proto.get("mcap") or detail.get("mcap")
        if not mcap:
            gecko_id = _find_gecko_id(matched_slug, slug_input, all_protocols)
            mcap = await _fetch_coingecko_mcap(client, gecko_id)

        fees_ttm = _sum_last(fees_chart, min(365, len(fees_chart)))
        rev_ttm = _sum_last(rev_chart, min(365, len(rev_chart)))
        # MCap/Rev uses protocol revenue; fall back to fees if no separate revenue stream
        mcap_rev_base = rev_ttm if rev_ttm else fees_ttm
        mcap_rev = _safe_div(mcap, mcap_rev_base)

        return {
            "asset": proto.get("name", matched_slug),
            "type": "defi",
            "mcap": mcap,
            "rev_ttm": fees_ttm,  # gross fees (user-paid) → "Rev TTM" column
            "net_or_prot": rev_ttm,  # fees kept by protocol → "Net Inc / Protocol Rev" column
            "mcap_rev": mcap_rev,
            "pe": None,  # N/A for DeFi
            "fcf_or_tvl": tvl,
            "fcf_label": "TVL",
            "error": None,
        }
    except Exception as e:
        return {"asset": slug_input, "type": "defi", "error": str(e)}


# ── Stock fetch ───────────────────────────────────────────────────────────────


def _ttm_sum(df, *labels) -> Optional[float]:
    """Sum 4 most recent quarterly values for the first matching label."""
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


def _fetch_stock_row(ticker: str) -> dict:
    """Sync yfinance fetch → normalized row dict. Call via run_in_executor."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        try:
            q_fin = t.quarterly_financials
        except Exception:
            q_fin = None
        try:
            q_cf = t.quarterly_cashflow
        except Exception:
            q_cf = None

        rev_ttm = _ttm_sum(q_fin, "Total Revenue")
        net_inc_ttm = _ttm_sum(q_fin, "Net Income", "Net Income Common Stockholders")
        fcf_ttm = _ttm_sum(q_cf, "Free Cash Flow")
        if fcf_ttm is None:
            op_cf = _ttm_sum(
                q_cf,
                "Operating Cash Flow",
                "Cash Flow From Continuing Operating Activities",
            )
            capex = _ttm_sum(q_cf, "Capital Expenditure", "Capital Expenditures")
            if op_cf is not None and capex is not None:
                fcf_ttm = op_cf + capex  # capex stored as negative
            elif op_cf is not None:
                fcf_ttm = op_cf

        mcap = info.get("marketCap")
        pe = info.get("trailingPE")
        ps = info.get("priceToSalesTrailing12Months")
        mcap_rev = (
            _safe_div(mcap, rev_ttm) if (mcap and rev_ttm) else (ps if ps else None)
        )

        return {
            "asset": info.get("longName") or info.get("shortName") or ticker.upper(),
            "type": "stock",
            "mcap": mcap,
            "rev_ttm": rev_ttm,
            "net_or_prot": net_inc_ttm,
            "mcap_rev": mcap_rev,
            "pe": pe,
            "fcf_or_tvl": fcf_ttm,
            "fcf_label": "FCF",
            "error": None,
        }
    except Exception as e:
        return {"asset": ticker.upper(), "type": "stock", "error": str(e)}


# ── main ──────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    now = datetime.now(timezone.utc)
    loop = asyncio.get_event_loop()

    # 1. Load DefiLlama protocol list (used for classification + slug resolution)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://api.llama.fi/protocols")
        r.raise_for_status()
        all_protocols = r.json()

    # 2. Classify each asset: defi vs stock
    #    - uppercase 1-5 letter strings default to stock unless there is an exact slug match
    #    - everything else tries _find_slug; if a protocol matches, it's defi
    #    - asset_types overrides both rules
    classified: Dict[str, str] = {}
    for asset in config.assets:
        override = (
            config.asset_types.get(asset)
            or config.asset_types.get(asset.upper())
            or config.asset_types.get(asset.lower())
        )
        if override:
            classified[asset] = override.lower()
        elif _looks_like_ticker(asset):
            exact = next(
                (
                    p
                    for p in all_protocols
                    if p.get("slug", "").lower() == asset.lower()
                ),
                None,
            )
            classified[asset] = "defi" if exact is not None else "stock"
        else:
            proto, _ = _find_slug(asset, all_protocols)
            classified[asset] = "defi" if proto is not None else "stock"

    defi_assets = [a for a in config.assets if classified[a] == "defi"]
    stock_assets = [a for a in config.assets if classified[a] == "stock"]

    # 3. Fetch all in parallel: async httpx for DeFi, run_in_executor for yfinance
    async with httpx.AsyncClient(timeout=60) as client:
        all_coros = [_fetch_defi_row(client, a, all_protocols) for a in defi_assets] + [
            loop.run_in_executor(None, _fetch_stock_row, a) for a in stock_assets
        ]
        raw_results = await asyncio.gather(*all_coros, return_exceptions=True)

    rows = []
    for res in raw_results:
        if isinstance(res, Exception):
            rows.append({"asset": "?", "type": "?", "mcap": None, "error": str(res)})
        else:
            rows.append(res)

    # 4. Sort by mcap descending (None → end)
    rows.sort(key=lambda x: (x.get("mcap") or -1), reverse=True)

    # 5. Build table rows
    columns = [
        "Asset",
        "Type",
        "MCap",
        "Rev TTM",
        "Net Inc / Protocol Rev",
        "MCap/Rev",
        "P/E",
        "FCF / TVL",
    ]
    table_data = []
    for row in rows:
        if row.get("error"):
            table_data.append(
                {
                    "Asset": row.get("asset", "?"),
                    "Type": (row.get("type") or "?").upper(),
                    "MCap": "N/A",
                    "Rev TTM": "N/A",
                    "Net Inc / Protocol Rev": "N/A",
                    "MCap/Rev": "N/A",
                    "P/E": "N/A",
                    "FCF / TVL": "N/A",
                }
            )
        else:
            table_data.append(
                {
                    "Asset": row["asset"],
                    "Type": row["type"].upper(),
                    "MCap": _fmt_usd(row.get("mcap")),
                    "Rev TTM": _fmt_usd(row.get("rev_ttm")),
                    "Net Inc / Protocol Rev": _fmt_usd(row.get("net_or_prot")),
                    "MCap/Rev": _fmt_mult(row.get("mcap_rev")),
                    "P/E": _fmt_mult(row.get("pe")),
                    "FCF / TVL": _fmt_usd(row.get("fcf_or_tvl")),
                }
            )

    # 6. Commentary: cheapest on MCap/Rev + highest stock net margin
    valid = [r for r in rows if not r.get("error")]
    with_mcap_rev = [r for r in valid if r.get("mcap_rev") is not None]
    cheapest = (
        min(with_mcap_rev, key=lambda x: float(x["mcap_rev"]))
        if with_mcap_rev
        else None
    )

    stock_with_margin = [
        r
        for r in valid
        if r.get("type") == "stock"
        and r.get("net_or_prot") is not None
        and r.get("rev_ttm")
        and r["rev_ttm"] != 0
    ]
    for r in stock_with_margin:
        r["_net_margin"] = r["net_or_prot"] / r["rev_ttm"]
    highest_margin = (
        max(stock_with_margin, key=lambda x: x["_net_margin"])
        if stock_with_margin
        else None
    )

    commentary_parts = []
    if cheapest:
        commentary_parts.append(
            f"**Cheapest on MCap/Rev:** {cheapest['asset']} ({_fmt_mult(cheapest['mcap_rev'])})"
        )
    if highest_margin:
        pct = highest_margin["_net_margin"] * 100
        commentary_parts.append(
            f"**Highest net margin (stocks):** {highest_margin['asset']} ({pct:.1f}%)"
        )
    commentary = (
        " · ".join(commentary_parts)
        if commentary_parts
        else "_Insufficient data for summary._"
    )

    # 7. Report
    errors = [r for r in rows if r.get("error")]

    builder = ReportBuilder("Peer Comparison")
    builder.source("routine", "peer_comparison")
    builder.tags(["fundamentals", "comparison", "defi", "equity"])

    builder.section(
        "01 / COMPARISON TABLE",
        f"{now.strftime('%Y-%m-%d %H:%M UTC')} · {len(rows)} assets "
        f"({len(defi_assets)} DeFi, {len(stock_assets)} stocks)",
    )
    builder.table(table_data, columns)
    builder.markdown(commentary)

    builder.section("02 / COLUMN GUIDE", "")
    builder.markdown(
        "- **Rev TTM** — DeFi: gross fees paid by users (TTM); Stock: total revenue (TTM)\n"
        "- **Net Inc / Protocol Rev** — DeFi: fees retained by the protocol (TTM); Stock: net income (TTM)\n"
        "- **MCap/Rev** — DeFi: MCap ÷ protocol revenue (falls back to fees if unavailable); Stock: MCap ÷ revenue ≈ P/S\n"
        "- **P/E** — trailing P/E for stocks; N/A for DeFi protocols\n"
        "- **FCF / TVL** — DeFi: total value locked; Stock: free cash flow (TTM)\n"
        "- DeFi MCap sources: DefiLlama protocols list → protocol detail page → CoinGecko"
    )

    if errors:
        builder.section("03 / FETCH ERRORS", "Assets that could not be retrieved")
        for err in errors:
            builder.markdown(f"- **{err.get('asset', '?')}**: {err['error']}")

    builder.section("04 / META", "")
    builder.kpi("Assets", ", ".join(config.assets))
    builder.kpi("DeFi Data", "DefiLlama + CoinGecko (mcap fallback)")
    builder.kpi("Stock Data", "Yahoo Finance (yfinance)")
    builder.kpi("Fetched At", now.strftime("%Y-%m-%d %H:%M UTC"))

    builder.manual_order()
    report_id = await builder.save()

    lines = [f"**Peer Comparison** — {now.strftime('%Y-%m-%d')}"]
    for row in rows:
        if row.get("error"):
            lines.append(f"• {row.get('asset', '?')} — ERROR: {row['error']}")
        else:
            lines.append(
                f"• {row['asset']} ({row['type'].upper()}) | "
                f"MCap: {_fmt_usd(row.get('mcap'))} | "
                f"Rev TTM: {_fmt_usd(row.get('rev_ttm'))} | "
                f"MCap/Rev: {_fmt_mult(row.get('mcap_rev'))}"
            )
    lines.append("")
    lines.append(commentary)
    lines.append(f"Report → {report_id}")
    return "\n".join(lines)
