"""Rank the markets of any CLOB venue for market making, spot or perp.

The question a market maker asks before quoting is not "is this liquid" but
"is the spread wide enough to clear what a round trip costs here, and is there
depth to quote into". Nothing else in the library answers it: the global
``market_scanner`` profiles volume and volatility with no spread, depth or fee
term at all, ``market_analyzer`` reads one pair's regime, and ``arb_check``
compares one pair across venues.

This is the HIP-3 scanner's ranking generalized. Three things change:

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

* **What "worth quoting" means.** Both older scanners asked whether the touch
  is already wider than a round trip. That is the test for someone joining the
  touch. The fly does not join it — it rests a quote away from mid and waits —
  so the question is whether the market *comes to it*: does a typical candle
  travel the round trip the fly must make, from mid down to its quote and back
  out through the take-profit? On Hyperliquid the touch test rejected all 120
  HIP-3 markets including the one the fly was quoting profitably by hand.

One honest limit. Reading a book costs a call, so only the top markets by
volume get read — and the busiest markets are not the most reachable. So
``prescreen`` is the knob that matters when nothing survives, and the report
says how deep it looked, and ranks the best of what it measured whether or not
anything cleared.
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
import statistics

from flybrain import venue
from flybrain.market import LiveMarket, depth_within
from flybrain.naming import pair_names
from flybrain.posture import base_levels_from_range, take_profit_floor_bps
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.fetchers.market_data import fetch_tickers
from condor.reports import ReportBuilder

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

# Reach saturates: past three cycles in a median candle the market is moving
# faster than a maker can requote, and the extra movement is adverse selection
# rather than income.
REACH_SCORE_CAP = 3.0

# A day of candles at the fly's own interval: enough for a stable median range
# and for the 24 h drift, from one call per market.
CANDLES_PER_DAY = 288


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
    # Every threshold below is bounded. A negative fee or a negative multiple
    # does not loosen a filter, it inverts it: the floor goes below zero and
    # every market that is not crossed "clears the fee", which is the one
    # mistake this routine exists to prevent.
    maker_fee_bps: float = Field(
        default=0.0,
        ge=0.0,
        description="Maker fee per side in bp; 0 uses the venue default",
    )
    min_range_over_cycle: float = Field(
        default=1.0,
        gt=0.0,
        description="Require a typical candle's range to be this multiple of the "
        "round trip the fly would have to travel (quote distance + take-profit). "
        "1.0 means a median candle completes one cycle",
    )
    candle_interval: str = Field(
        default="5m",
        description="Candle the range is measured on; use the fly's own interval",
    )
    min_volume_usd: float = Field(
        default=250_000.0, ge=0.0, description="Minimum 24h volume"
    )
    max_daily_drift_pct: float = Field(
        default=3.0, ge=0.0, description="Maximum 24h price drift %"
    )
    min_book_depth_usd: float = Field(
        default=10_000.0,
        ge=0.0,
        description="Minimum resting notional per side, within the band",
    )
    depth_within_bps: float = Field(
        default=10.0, gt=0.0, description="Band around mid for depth"
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


def cycle_bps(range_bps: float, fee_bps: float) -> float:
    """The round trip the fly must travel on this market, in bp.

    From mid down to where it would rest level 1 — half a typical bar's range,
    never inside the fee — and back out through the take-profit floor. This is
    the distance a market has to move for one completed pair.
    """
    entry = max(base_levels_from_range(range_bps)[0], fee_bps)
    return entry + take_profit_floor_bps(fee_bps)


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
        # One day of the fly's own candle gives both numbers: the drift across
        # it, and how far a typical one of them travels. The first ask for a
        # market hummingbot-api has not seen subscribes a candle feed and
        # returns 504 if it is not ready within 30 s. That is a cold feed, not
        # a missing market: the same call answers on the second attempt.
        for attempt in (1, 2):
            try:
                raw = await market.client.market_data.get_candles(
                    config.connector_name,
                    pair,
                    interval=config.candle_interval,
                    max_records=CANDLES_PER_DAY,
                )
                rows = (
                    raw
                    if isinstance(raw, list)
                    else raw.get("data", raw.get("candles"))
                )
                bars = [
                    (float(c["high"]), float(c["low"]), float(c["close"]))
                    for c in (rows or [])
                    if c.get("close") and float(c["close"]) > 0
                ]
                if len(bars) < 2:
                    raise ValueError(f"only {len(bars)} usable candles")
                row["drift_pct"] = abs(bars[-1][2] / bars[0][2] - 1) * 100
                # Median, not mean: one news bar should not make a quiet market
                # look reachable.
                row["range_bps"] = statistics.median(
                    (high - low) / close * 1e4 for high, low, close in bars
                )
                row["candles"] = len(bars)
                break
            except Exception as failure:  # external feed, one market
                row["drift_pct"] = None
                row["range_bps"] = None
                row["candle_error"] = (
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

    # ── 2. Book and candles, only for what survived — the expensive part ─────
    market = LiveMarket(
        client, config.connector_name, config.candle_interval, CANDLES_PER_DAY
    )
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
        drift = m.get("drift_pct")
        depth = min(m.get("bid_depth_usd", 0.0), m.get("ask_depth_usd", 0.0))
        spread = m.get("spread_bps", 0.0)
        # What the fly would actually do here: rest level 1 at max(2, S/2) bp,
        # never inside the fee, and close at the take-profit floor. The round
        # trip it must travel is the sum — down to the quote, then back up
        # through the exit.
        entry_bps = max(base_levels_from_range(range_bps or 0.0)[0], fee_bps)
        exit_bps = take_profit_floor_bps(fee_bps)
        cycle = cycle_bps(range_bps or 0.0, fee_bps)
        range_bps = m.get("range_bps")
        # How far a typical candle travels against that cycle. Above 1 the
        # median candle completes one; below it, the market does not come to
        # the fly often enough to matter, however wide its touch looks.
        reach = (range_bps / cycle) if range_bps else 0.0
        reasons = []
        if m.get("error"):
            reasons.append("book unreadable")
        elif not m.get("open"):
            reasons.append("book closed")
        if range_bps is None:
            reasons.append(f"candles unreadable: {m.get('candle_error', 'none')}")
        elif reach < config.min_range_over_cycle:
            reasons.append(
                f"range {range_bps:.1f} < {config.min_range_over_cycle:g}× the "
                f"{cycle:.1f} bp cycle"
            )
        if depth < config.min_book_depth_usd:
            reasons.append(f"depth ${depth:,.0f}")
        if drift is not None and drift > config.max_daily_drift_pct:
            reasons.append(f"drift {drift:.1f}%")
        rows.append(
            {
                "pair": pair,
                "volume": volume,
                "fee_bps": fee_bps,
                "entry_bps": entry_bps,
                "exit_bps": exit_bps,
                "cycle_bps": cycle,
                "range_bps": range_bps,
                "reach": reach,
                "spread_bps": spread,
                "depth_usd": depth,
                "drift_pct": drift,
                "survives": not reasons,
                "why_not": ", ".join(reasons),
                # Reach is what earns: a market that traverses the fly's cycle
                # twice as often is worth twice as much to it, up to the point
                # where the movement is adverse selection rather than noise.
                "score": (
                    math.log(max(volume, 1))
                    + 2.0 * min(reach, REACH_SCORE_CAP)
                    - 0.4 * (drift if drift is not None else 99.0)
                ),
            }
        )
    survivors = sorted((r for r in rows if r["survives"]), key=lambda r: -r["score"])[
        : config.top_n
    ]
    # Rank the best measured markets whether or not they clear, so a scan that
    # finds no survivor still says which markets came closest and by how much.
    ranked = sorted(rows, key=lambda r: -r["score"])[: config.top_n]
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
    cycles = [r["cycle_bps"] for r in rows]
    builder.section(
        "WHETHER THE MARKET COMES TO THE FLY",
        f"{market_type} venue · maker {fee_text} a side. The fly does not join "
        "the touch: it rests a quote and waits. So the test is not whether the "
        "spread is already wide, but whether a typical "
        f"{config.candle_interval} candle travels the round trip it would have "
        f"to — down to its quote and back out through the take-profit, "
        f"{min(cycles):.1f}–{max(cycles):.1f} bp here — at least "
        f"{config.min_range_over_cycle:g}× over.",
    )
    builder.kpi("Venue", config.connector_name)
    builder.kpi("Type", market_type)
    builder.kpi("Maker fee", fee_text)
    builder.kpi("Cycle to travel", f"{min(cycles):.1f}–{max(cycles):.1f} bp")
    builder.kpi("Listed", f"{listed:,}")
    builder.kpi("Book-checked", str(len(screened)))
    builder.kpi("Survivors", str(len([r for r in rows if r["survives"]])))

    builder.section(
        "RANKED",
        f"Best {len(ranked)} of the {len(screened)} measured, clearing or not — "
        "reach is the median candle's range over the cycle the fly must travel",
    )
    builder.table(
        [
            {
                "Pair": r["pair"],
                "Clears": "yes" if r["survives"] else "no",
                "24h volume": f"${r['volume']:,.0f}",
                "Median range": (f"{r['range_bps']:.2f} bp" if r["range_bps"] else "—"),
                "Cycle": f"{r['cycle_bps']:.2f} bp",
                "Reach": f"{r['reach']:.2f}×",
                "Spread": f"{r['spread_bps']:.2f} bp",
                "Depth/side": f"${r['depth_usd']:,.0f}",
                "Drift": (
                    f"{r['drift_pct']:.2f}%" if r["drift_pct"] is not None else "—"
                ),
            }
            for r in ranked
        ]
        or [{"Pair": "— nothing measured —"}],
        [
            "Pair",
            "Clears",
            "24h volume",
            "Median range",
            "Cycle",
            "Reach",
            "Spread",
            "Depth/side",
            "Drift",
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
        "_Reach says the market reaches the fly's quote, not that the fills are "
        "good ones: the same movement that fills a maker is what runs him over, "
        "and a median range hides the bar that gaps through both levels. Depth "
        "is filtered separately because a wide touch is usually a thin one. "
        "Drift is a proxy for the inventory a maker accumulates against a trend, "
        "not a forecast. Nothing here says a market is profitable._"
    )
    await builder.save()

    lines = [
        f"venue: {config.connector_name} ({market_type})",
        f"maker_fee_bps: {fee_text} a side",
        f"listed: {listed}, book_checked: {len(screened)}, survivors: {len(survivors)}",
    ]
    for n, r in enumerate(ranked, 1):
        lines.append(
            f"{n}. {r['pair']}: reach {r['reach']:.2f}× (median "
            + (f"{r['range_bps']:.2f}" if r["range_bps"] else "—")
            + f" bp range vs a {r['cycle_bps']:.2f} bp cycle), spread "
            f"{r['spread_bps']:.2f} bp, fee {r['fee_bps']:.2f} bp, depth "
            f"${r['depth_usd']:,.0f}/side, vol ${r['volume']:,.0f}"
            + ("" if r["survives"] else f" — REJECTED: {r['why_not']}")
        )
    if survivors:
        best = survivors[0]
        lines.append(
            f"TOP PICK: {best['pair']} at reach {best['reach']:.2f}×, "
            f"picked_ranges_bps={best['range_bps']:.2f}"
        )
    else:
        lines.append(
            "TOP PICK: none — no market travels the fly's own round trip often "
            "enough, with depth behind it"
        )
    return "\n".join(lines)
