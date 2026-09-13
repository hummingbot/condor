"""Rank the markets of any CLOB venue for market making, spot or perp.

The question a market maker asks before quoting is not "is this liquid" but
"is the spread wide enough to clear what a round trip costs here, and is there
depth to quote into". Nothing else in the library answers it: the global
``market_scanner`` profiles volume and volatility with no spread, depth or fee
term at all, ``market_analyzer`` reads one pair's regime, and ``arb_check``
compares one pair across venues.

This is the HIP-3 scanner's ranking generalized. Two things change:

* **Where the numbers come from.** That routine reads one Hyperliquid call
  which returns every market in an issuer's dex. Condor's own ticker fetcher
  turns out to enumerate the same markets — 285 issuer-prefixed pairs come
  back for ``hyperliquid_perpetual`` alongside its native ones — so one code
  path now covers HIP-3 and every other venue. Book depth comes from
  ``LiveMarket.levels``, which already knows which source serves which market.
* **What "wide enough" means.** That routine hardcodes 3 bp, which is unrelated
  to what trading costs. Here the threshold is a multiple of the venue's own
  round-trip maker fee, and that fee differs by a factor of five between a
  HIP-3 perp and a spot book.

One honest limit. Reading a book costs a call, so only the top markets by
volume get read — and the widest markets are rarely the busiest. Scanning the
120 HIP-3 markets, the eight heaviest all quote under 1.4 bp, far too tight to
clear a 2.6 bp round trip; the first market that cleared it sat thirtieth by
volume. So ``prescreen`` is the knob that matters when nothing survives, and
the report says how deep it looked.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import asyncio
import logging
import math

from flybrain import venue
from flybrain.market import LiveMarket, depth_within
from flybrain.naming import pair_names
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.fetchers.market_data import fetch_tickers
from condor.reports import ReportBuilder

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

# The spread term saturates: past this, more spread says more about how thin
# the book is than about how much a maker earns.
SPREAD_SCORE_CAP_BPS = 8.0


class Config(BaseModel):
    """Rank a venue's markets for market making: spread vs fee, depth, drift."""

    connector_name: str = Field(
        default="hyperliquid_perpetual", description="Any CLOB connector, spot or perp"
    )
    pairs: str = Field(
        default="",
        description="Comma-separated pairs to rank; blank ranks everything the venue lists",
    )
    quote: str = Field(
        default="",
        description="Only pairs quoted in this asset, e.g. USDT (blank = any)",
    )
    issuer: str = Field(
        default="",
        description="Only pairs with this issuer prefix, e.g. xyz for HIP-3 (blank = any)",
    )
    maker_fee_bps: float = Field(
        default=0.0, description="Maker fee per side in bp; 0 uses the venue default"
    )
    min_spread_over_fee: float = Field(
        default=1.5,
        description="Require the spread to be this multiple of the round-trip fee",
    )
    min_volume_usd: float = Field(default=250_000.0, description="Minimum 24h volume")
    max_daily_drift_pct: float = Field(
        default=3.0, description="Maximum 24h price drift %"
    )
    min_book_depth_usd: float = Field(
        default=10_000.0,
        description="Minimum resting notional per side, within the band",
    )
    depth_within_bps: float = Field(
        default=10.0, description="Band around mid for depth"
    )
    prescreen: int = Field(
        default=30,
        ge=1,
        le=120,
        description="How many of the highest-volume markets to read the book of. "
        "Raise it when nothing survives: the widest markets are rarely the "
        "busiest, so a low prescreen sees only tight books",
    )
    top_n: int = Field(default=5, ge=1, le=25, description="Markets to report")


async def _measure(market: LiveMarket, pair: str, config: Config, sem) -> dict:
    """Spread, depth and drift for one market. Never raises: a market whose
    book or candles cannot be read is reported as unreadable, not dropped
    silently and not allowed to kill the scan."""
    row: dict = {"pair": pair}
    async with sem:
        try:
            bids, asks = await market.levels(pair, depth=50)
            bid_usd, ask_usd, spread_bps = depth_within(
                bids, asks, config.depth_within_bps
            )
            row.update(
                {
                    "spread_bps": spread_bps,
                    "bid_depth_usd": bid_usd,
                    "ask_depth_usd": ask_usd,
                    "open": bool(bids and asks),
                }
            )
        except Exception as failure:  # external feed, one market
            row["error"] = repr(failure)[:80]
            return row
        # The first ask for a market hummingbot-api has not seen subscribes a
        # candle feed and returns 504 if it is not ready within 30 s. That is a
        # cold feed, not a missing market: the same call answers on the second
        # attempt. Asking twice is the difference between a scan that ranks the
        # venue and one that rejects all of it for "no candles".
        for attempt in (1, 2):
            try:
                raw = await market.client.market_data.get_candles(
                    config.connector_name, pair, interval="1h", max_records=25
                )
                rows = (
                    raw
                    if isinstance(raw, list)
                    else raw.get("data", raw.get("candles"))
                )
                closes = [float(c["close"]) for c in (rows or []) if c.get("close")]
                row["drift_pct"] = (
                    abs(closes[-1] / closes[0] - 1) * 100 if len(closes) >= 2 else None
                )
                break
            except Exception as failure:  # external feed, one market
                row["drift_pct"] = None
                row["drift_error"] = (
                    str(getattr(failure, "message", "") or failure)[:70]
                    or repr(failure)[:70]
                )
    return row


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    from config_manager import get_client

    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    market_type = venue.market_type_for(config.connector_name)

    # ── 1. Enumerate and screen on volume, which is one call ──────────────────
    if config.pairs.strip():
        candidates = {p.strip(): None for p in config.pairs.split(",") if p.strip()}
        tickers = (await fetch_tickers(client, config.connector_name)).get(
            "tickers"
        ) or {}
        volumes = {
            p: float((tickers.get(p) or {}).get("usd_volume", 0) or 0)
            for p in candidates
        }
    else:
        tickers = (await fetch_tickers(client, config.connector_name)).get(
            "tickers"
        ) or {}
        if not tickers:
            return (
                f"No tickers for {config.connector_name}; is the connector supported?"
            )
        volumes = {}
        for pair, row in tickers.items():
            try:
                names = pair_names(pair)
            except ValueError:
                continue  # not a shape this agent can quote
            if config.quote and names.quote != config.quote.upper():
                continue
            if config.issuer and names.issuer != config.issuer.lower():
                continue
            volumes[pair] = float((row or {}).get("usd_volume", 0) or 0)

    listed = len(volumes)
    screened = sorted(
        ((p, v) for p, v in volumes.items() if v >= config.min_volume_usd),
        key=lambda kv: -kv[1],
    )[: config.prescreen]
    if not screened:
        return (
            f"{config.connector_name}: {listed} markets listed, none above "
            f"${config.min_volume_usd:,.0f} of 24h volume"
        )

    # ── 2. Book and drift, only for what survived — this is the expensive part ─
    market = LiveMarket(client, config.connector_name, "1h", 25)
    sem = asyncio.Semaphore(6)
    measured = await asyncio.gather(
        *(_measure(market, pair, config, sem) for pair, _ in screened)
    )
    by_pair = {m["pair"]: m for m in measured}

    # One connector can serve two fee families — a Hyperliquid core perp costs
    # twice what one of its HIP-3 markets does — so each market is ranked
    # against its own round trip, not the venue's average.
    fees = {
        pair: config.maker_fee_bps
        or await venue.maker_fee_bps(config.connector_name, market_type, pair)
        for pair, _ in screened
    }

    rows = []
    for pair, volume in screened:
        m = by_pair[pair]
        fee_bps = fees[pair]
        round_trip_bps = 2 * fee_bps
        min_spread_bps = round_trip_bps * config.min_spread_over_fee
        drift = m.get("drift_pct")
        depth = min(m.get("bid_depth_usd", 0.0), m.get("ask_depth_usd", 0.0))
        spread = m.get("spread_bps", 0.0)
        reasons = []
        if m.get("error"):
            reasons.append("book unreadable")
        elif not m.get("open"):
            reasons.append("book closed")
        if spread < min_spread_bps:
            reasons.append(f"spread {spread:.1f} < {min_spread_bps:.1f} bp")
        if depth < config.min_book_depth_usd:
            reasons.append(f"depth ${depth:,.0f}")
        if drift is None:
            reasons.append(f"drift unreadable: {m.get('drift_error', 'no candles')}")
        elif drift > config.max_daily_drift_pct:
            reasons.append(f"drift {drift:.1f}%")
        rows.append(
            {
                "pair": pair,
                "volume": volume,
                "fee_bps": fee_bps,
                "floor_bps": min_spread_bps,
                "spread_bps": spread,
                "spread_over_fee": spread / round_trip_bps if round_trip_bps else 0.0,
                "depth_usd": depth,
                "drift_pct": drift,
                "survives": not reasons,
                "why_not": ", ".join(reasons),
                "score": (
                    math.log(max(volume, 1))
                    + 0.3 * min(spread, SPREAD_SCORE_CAP_BPS)
                    - 0.4 * (drift if drift is not None else 99.0)
                ),
            }
        )
    survivors = sorted((r for r in rows if r["survives"]), key=lambda r: -r["score"])[
        : config.top_n
    ]
    rejected = sorted(
        (r for r in rows if not r["survives"]), key=lambda r: -r["volume"]
    )

    builder = ReportBuilder(f"MM markets — {config.connector_name}")
    builder.source("routine", "mm_market_scanner")
    builder.tags(["market-making", "scanner", config.connector_name])
    builder.manual_order()
    cheapest, dearest = min(fees.values()), max(fees.values())
    fee_text = (
        f"{cheapest:.2f} bp"
        if cheapest == dearest
        else f"{cheapest:.2f}–{dearest:.2f} bp"
    )
    builder.section(
        "WHAT A ROUND TRIP COSTS HERE",
        f"{market_type} venue · maker {fee_text} a side · each market must quote "
        f"{config.min_spread_over_fee}× its own round trip to clear it"
        + (
            ""
            if cheapest == dearest
            else ". This venue charges different markets differently, so the floor "
            "below is per market rather than venue-wide"
        ),
    )
    builder.kpi("Venue", config.connector_name)
    builder.kpi("Type", market_type)
    builder.kpi("Maker fee", fee_text)
    builder.kpi("Round trip", f"{2 * cheapest:.2f}–{2 * dearest:.2f} bp")
    builder.kpi(
        "Spread floor",
        f"{2 * cheapest * config.min_spread_over_fee:.2f}–"
        f"{2 * dearest * config.min_spread_over_fee:.2f} bp",
    )
    builder.kpi("Listed", f"{listed:,}")
    builder.kpi("Book-checked", str(len(screened)))
    builder.kpi("Survivors", str(len([r for r in rows if r["survives"]])))

    builder.section(
        "RANKED", f"Top {len(survivors)} by score — volume, spread over fee, and drift"
    )
    builder.table(
        [
            {
                "Pair": r["pair"],
                "24h volume": f"${r['volume']:,.0f}",
                "Spread": f"{r['spread_bps']:.2f} bp",
                "Maker fee": f"{r['fee_bps']:.2f} bp",
                "× round trip": f"{r['spread_over_fee']:.2f}×",
                "Depth/side": f"${r['depth_usd']:,.0f}",
                "Drift": (
                    f"{r['drift_pct']:.2f}%" if r["drift_pct"] is not None else "—"
                ),
                "Score": f"{r['score']:.2f}",
            }
            for r in survivors
        ]
        or [{"Pair": "— none survived —"}],
        [
            "Pair",
            "24h volume",
            "Spread",
            "Maker fee",
            "× round trip",
            "Depth/side",
            "Drift",
            "Score",
        ],
    )
    builder.section("REJECTED", "Why each screened market did not make it")
    builder.table(
        [
            {
                "Pair": r["pair"],
                "24h volume": f"${r['volume']:,.0f}",
                "Spread": f"{r['spread_bps']:.2f} bp",
                "Depth/side": f"${r['depth_usd']:,.0f}",
                "Reason": r["why_not"],
            }
            for r in rejected
        ]
        or [{"Pair": "— none rejected —"}],
        ["Pair", "24h volume", "Spread", "Depth/side", "Reason"],
    )
    builder.markdown(
        f"_Books were read for the {len(screened)} highest-volume markets of "
        f"{listed} listed. The widest markets are rarely the busiest, so raise "
        "`prescreen` when nothing survives — it is the only thing standing "
        "between this scan and the rest of the venue._"
    )
    builder.markdown(
        "_A wide spread is necessary, not sufficient: it is often wide because the "
        "book is thin, which is why depth is filtered separately. Drift is a proxy "
        "for how much inventory a maker would accumulate against the trend, not a "
        "forecast. Nothing here says a market is profitable._"
    )
    await builder.save()

    lines = [
        f"venue: {config.connector_name} ({market_type})",
        f"maker_fee_bps: {fee_text} a side",
        f"listed: {listed}, book_checked: {len(screened)}, survivors: {len(survivors)}",
    ]
    for n, r in enumerate(survivors, 1):
        lines.append(
            f"{n}. {r['pair']}: spread {r['spread_bps']:.2f} bp vs a "
            f"{r['floor_bps']:.2f} bp floor ({r['spread_over_fee']:.2f}× round trip), "
            f"fee {r['fee_bps']:.2f} bp, depth ${r['depth_usd']:,.0f}/side, "
            f"vol ${r['volume']:,.0f}, drift "
            + (f"{r['drift_pct']:.2f}%" if r["drift_pct"] is not None else "—")
        )
    if survivors:
        lines.append(
            f"TOP PICK: {survivors[0]['pair']} at {survivors[0]['spread_bps']:.2f} bp"
        )
    else:
        lines.append("TOP PICK: none — no market cleared the fee with depth behind it")
    return "\n".join(lines)
