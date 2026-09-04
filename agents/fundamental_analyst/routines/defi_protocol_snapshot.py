import asyncio
import difflib
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder

CATEGORY = "Analysis"

# Categories considered "non-trading" — deprioritised in slug resolution
_INFRA_CATEGORIES = {"Bridge", "Chain", "Infrastructure", "Staking Pool", "RWA"}


class Config(BaseModel):
    """DeFi protocol fundamental snapshot — TVL, fees, revenue, and valuation multiples from DefiLlama."""

    protocol: str = Field(
        default="uniswap-v3",
        description="DefiLlama protocol slug or brand name (e.g. 'uniswap', 'aave', 'hyperliquid')",
    )
    include_competitors: bool = Field(
        default=True,
        description="Include top 3 peers by TVL in the same category",
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _fmt_usd(val) -> str:
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
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
    return f"{val:+.1f}%"


def _fmt_mult(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:.2f}x"


def _sum_last(chart, n) -> float | None:
    if not chart:
        return None
    tail = chart[-n:]
    total = sum(v for _, v in tail if v is not None)
    return total or None


def _safe_div(num, denom):
    if num and denom and denom != 0:
        return num / denom
    return None


def _pick_best(candidates: list) -> tuple | None:
    """From a list of protocols, prefer non-infra categories, then pick highest TVL."""
    if not candidates:
        return None
    trading = [p for p in candidates if p.get("category") not in _INFRA_CATEGORIES]
    pool = trading if trading else candidates
    return max(pool, key=lambda p: p.get("tvl") or 0)


def _find_slug(slug_input: str, protocols: list) -> tuple:
    """Return (protocol_dict, matched_slug)."""
    inp = slug_input.lower().strip()

    # 1. Exact slug match
    exact = next((p for p in protocols if p.get("slug", "") == inp), None)
    if exact:
        return exact, inp

    # 2. Slug prefix match (prefer non-infra)
    prefix = [p for p in protocols if p.get("slug", "").startswith(inp)]
    if prefix:
        best = _pick_best(prefix)
        return best, best["slug"]

    # 3. Slug contains (but not already covered by prefix)
    contains = [
        p
        for p in protocols
        if inp in p.get("slug", "") and not p.get("slug", "").startswith(inp)
    ]
    if contains:
        best = _pick_best(contains)
        return best, best["slug"]

    # 4. Name contains
    name_c = [p for p in protocols if inp in p.get("name", "").lower()]
    if name_c:
        best = _pick_best(name_c)
        return best, best["slug"]

    # 5. Fuzzy on slug
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
) -> str | None:
    """Find gecko_id: direct → brand-prefix scan → raw user input as gecko_id."""
    # Direct hit
    p = next((x for x in all_protocols if x.get("slug") == matched_slug), None)
    if p and p.get("gecko_id"):
        return p["gecko_id"]

    # Brand prefix scan (strip -vN or -suffix)
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

    # Fall back to the raw user input as the gecko_id (works for e.g. "hyperliquid")
    inp = user_input.lower().strip()
    if inp and " " not in inp:
        return inp

    return None


async def _fetch_fees(client: httpx.AsyncClient, slug: str, data_type: str) -> list:
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


async def _fetch_coingecko(client: httpx.AsyncClient, gecko_id: str) -> dict:
    if not gecko_id:
        return {}
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
            data = r.json()
            md = data.get("market_data", {})
            return {
                "mcap": md.get("market_cap", {}).get("usd"),
                "fdv": md.get("fully_diluted_valuation", {}).get("usd"),
                "circulating_supply": md.get("circulating_supply"),
                "total_supply": md.get("total_supply"),
            }
    except Exception:
        pass
    return {}


# ── main ─────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    BASE = "https://api.llama.fi"
    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=45) as client:
        # 1. Protocol list
        r_list = await client.get(f"{BASE}/protocols")
        r_list.raise_for_status()
        all_protocols = r_list.json()

        # 2. Resolve slug
        proto, matched_slug = _find_slug(config.protocol, all_protocols)
        if proto is None:
            slugs = [p.get("slug", "") for p in all_protocols]
            suggestions = difflib.get_close_matches(
                config.protocol.lower(), slugs, n=5, cutoff=0.3
            )
            hint = f" Suggestions: {', '.join(suggestions)}" if suggestions else ""
            return f"Protocol '{config.protocol}' not found on DefiLlama.{hint}"

        slug_note = (
            f" (matched '{matched_slug}')" if matched_slug != config.protocol else ""
        )
        gecko_id = _find_gecko_id(matched_slug, config.protocol, all_protocols)

        # 3. Parallel fetch: detail + fees + revenue + CoinGecko
        r_detail, fees_chart, rev_chart, cg_data = await asyncio.gather(
            client.get(f"{BASE}/protocol/{matched_slug}"),
            _fetch_fees(client, matched_slug, "dailyFees"),
            _fetch_fees(client, matched_slug, "dailyRevenue"),
            _fetch_coingecko(client, gecko_id),
        )
        detail = r_detail.json() if r_detail.status_code == 200 else {}

        # 4. TVL metrics
        tvl_current = proto.get("tvl")
        tvl_hist = detail.get("tvl", [])

        def _tvl_at(days_ago):
            if not tvl_hist:
                return None
            target = now.timestamp() - days_ago * 86400
            entry = min(tvl_hist, key=lambda x: abs(x["date"] - target))
            return entry.get("totalLiquidityUSD")

        tvl_7d_val = _tvl_at(7)
        tvl_30d_val = _tvl_at(30)

        def _tvl_chg(past):
            if past and tvl_current and past != 0:
                return (tvl_current - past) / past * 100
            return None

        # 5. Fee metrics
        fee_24h = fees_chart[-1][1] if fees_chart else None
        fee_7d = _sum_last(fees_chart, 7)
        fee_30d = _sum_last(fees_chart, 30)
        fee_ttm = _sum_last(fees_chart, min(365, len(fees_chart)))
        fee_30d_ann = fee_30d * 365 / 30 if fee_30d else None

        # 6. Revenue metrics
        rev_24h = rev_chart[-1][1] if rev_chart else None
        rev_7d = _sum_last(rev_chart, 7)
        rev_30d = _sum_last(rev_chart, 30)
        rev_ttm = _sum_last(rev_chart, min(365, len(rev_chart)))

        # 7. Valuation — DefiLlama first, CoinGecko fallback
        mcap = proto.get("mcap") or detail.get("mcap") or cg_data.get("mcap")
        fdv = detail.get("fdv") or proto.get("fdv") or cg_data.get("fdv")
        mcap_source = (
            "CoinGecko"
            if cg_data.get("mcap") and not (proto.get("mcap") or detail.get("mcap"))
            else "DefiLlama"
        )
        treasury = detail.get("treasury")

        circ_supply = cg_data.get("circulating_supply")
        total_supply = cg_data.get("total_supply")
        circ_pct = (
            f"{circ_supply / total_supply * 100:.1f}%"
            if circ_supply and total_supply
            else f"{mcap / fdv * 100:.1f}%" if mcap and fdv else "N/A"
        )

        mult_mcap_fees = _safe_div(mcap, fee_ttm)
        mult_fdv_fees = _safe_div(fdv, fee_ttm)
        mult_mcap_rev = _safe_div(mcap, rev_ttm)
        mult_fdv_rev = _safe_div(fdv, rev_ttm)
        mult_p_tvl = _safe_div(mcap, tvl_current)

        category = proto.get("category") or detail.get("category") or "Unknown"
        chains = detail.get("chains") or proto.get("chains") or []
        name = proto.get("name", matched_slug)

        # 8. Competitors
        comp_rows = []
        if config.include_competitors:
            peers = sorted(
                [
                    p
                    for p in all_protocols
                    if p.get("category") == category
                    and p.get("slug") != matched_slug
                    and (p.get("tvl") or 0) > 0
                ],
                key=lambda p: p.get("tvl") or 0,
                reverse=True,
            )[:3]

            if peers:
                peer_fees = await asyncio.gather(
                    *[_fetch_fees(client, p["slug"], "dailyFees") for p in peers]
                )
                for peer, pf in zip(peers, peer_fees):
                    p_fee_ttm = _sum_last(pf, min(365, len(pf)))
                    p_mcap = peer.get("mcap")
                    comp_rows.append(
                        {
                            "Protocol": peer.get("name", peer.get("slug")),
                            "TVL": _fmt_usd(peer.get("tvl")),
                            "MCap": _fmt_usd(p_mcap),
                            "Fees TTM": _fmt_usd(p_fee_ttm),
                            "MCap/Fees": _fmt_mult(_safe_div(p_mcap, p_fee_ttm)),
                        }
                    )

        # ── Build report ──────────────────────────────────────────────────────
        builder = ReportBuilder(f"{name} — Fundamental Snapshot")
        builder.source("routine", "defi_protocol_snapshot")
        builder.tags(["defi", "fundamentals", category.lower().replace(" ", "-")])

        builder.section(
            "01 / OVERVIEW", f"{name}{slug_note} | {now.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        builder.kpi("Category", category)
        builder.kpi("Active Chains", str(len(chains)))
        builder.kpi("Chains", ", ".join(chains[:6]) + ("…" if len(chains) > 6 else ""))
        builder.kpi("TVL", _fmt_usd(tvl_current))
        builder.kpi("TVL 7d Δ", _fmt_pct(_tvl_chg(tvl_7d_val)))
        builder.kpi("TVL 30d Δ", _fmt_pct(_tvl_chg(tvl_30d_val)))

        builder.section("02 / GROSS FEES", "Total fees paid by users to the protocol")
        builder.kpi("Fees 24h", _fmt_usd(fee_24h))
        builder.kpi("Fees 7d", _fmt_usd(fee_7d))
        builder.kpi("Fees 30d", _fmt_usd(fee_30d))
        builder.kpi("Fees 30d Annualised", _fmt_usd(fee_30d_ann))
        builder.kpi("Fees TTM", _fmt_usd(fee_ttm))

        builder.section(
            "03 / PROTOCOL REVENUE", "Fees retained by the protocol / treasury"
        )
        if rev_chart:
            builder.kpi("Revenue 24h", _fmt_usd(rev_24h))
            builder.kpi("Revenue 7d", _fmt_usd(rev_7d))
            builder.kpi("Revenue 30d", _fmt_usd(rev_30d))
            builder.kpi("Revenue TTM", _fmt_usd(rev_ttm))
        else:
            builder.markdown(
                "_No separate revenue stream — protocol does not split fees from revenue._"
            )

        builder.section("04 / VALUATION", f"Market cap and FDV (source: {mcap_source})")
        builder.kpi("Market Cap", _fmt_usd(mcap))
        builder.kpi("FDV", _fmt_usd(fdv))
        builder.kpi("Circulating Supply %", circ_pct)
        if treasury is not None:
            builder.kpi("Treasury", _fmt_usd(treasury))

        builder.section(
            "05 / MULTIPLES", "Lower = cheaper. N/A = missing mcap/fdv data."
        )
        builder.kpi("MCap / Fees (TTM)", _fmt_mult(mult_mcap_fees))
        builder.kpi("FDV / Fees (TTM)", _fmt_mult(mult_fdv_fees))
        builder.kpi("MCap / Revenue (TTM)", _fmt_mult(mult_mcap_rev))
        builder.kpi("FDV / Revenue (TTM)", _fmt_mult(mult_fdv_rev))
        builder.kpi("Price / TVL", _fmt_mult(mult_p_tvl))

        if comp_rows:
            builder.section(
                "06 / PEER COMPARISON",
                f"Top 3 peers in {category} by TVL (★ = subject)",
            )
            subject_row = {
                "Protocol": f"★ {name}",
                "TVL": _fmt_usd(tvl_current),
                "MCap": _fmt_usd(mcap),
                "Fees TTM": _fmt_usd(fee_ttm),
                "MCap/Fees": _fmt_mult(mult_mcap_fees),
            }
            builder.table(
                [subject_row] + comp_rows,
                ["Protocol", "TVL", "MCap", "Fees TTM", "MCap/Fees"],
            )

        builder.section("07 / META", "")
        builder.kpi("Data Source", "DefiLlama + CoinGecko")
        builder.kpi("Slug Used", matched_slug)
        builder.kpi("Gecko ID", gecko_id or "N/A")
        builder.kpi("Fetched At", now.strftime("%Y-%m-%d %H:%M UTC"))

        builder.manual_order()
        report_id = await builder.save()

        return (
            f"**{name}** ({category}){slug_note}\n"
            f"TVL: {_fmt_usd(tvl_current)} | Fees TTM: {_fmt_usd(fee_ttm)} | Rev TTM: {_fmt_usd(rev_ttm)}\n"
            f"MCap: {_fmt_usd(mcap)} ({mcap_source}) | FDV: {_fmt_usd(fdv)}\n"
            f"MCap/Fees: {_fmt_mult(mult_mcap_fees)} | Price/TVL: {_fmt_mult(mult_p_tvl)}\n"
            f"Report → {report_id}"
        )
