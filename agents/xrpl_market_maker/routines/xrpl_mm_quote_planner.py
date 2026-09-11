"""Plan XRPL CLOB maker quotes: reference fair value, spread bounds, viability verdict."""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.fetchers.market_data import fetch_current_price
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# Public XRPL JSON-RPC. Used only for amm_info (pool trading fee), which sets the
# spread ceiling. Falls back to the configured value when unreachable.
XRPL_RPC = "https://xrplcluster.com/"

# XRPL reserve schedule (validator-voted; verify if the ledger amends it).
BASE_RESERVE_XRP = 1.0
OWNER_RESERVE_XRP = 0.2

# Intraday volatility clock. Split-half persistence r=0.90 across XRP/BTC/ETH/SOL,
# 733 days of Bitget perp 1H bars. Values are vol relative to the daily average.
# Trough 04:00-11:00 UTC (Asia afternoon / pre-Europe); peak 13:00-15:00 UTC (US open).
HOUR_VOL_MULT = {
    0: 1.03,
    1: 1.10,
    2: 0.95,
    3: 0.89,
    4: 0.82,
    5: 0.85,
    6: 0.82,
    7: 0.82,
    8: 0.91,
    9: 0.83,
    10: 0.78,
    11: 0.82,
    12: 0.92,
    13: 1.28,
    14: 1.50,
    15: 1.40,
    16: 1.16,
    17: 1.21,
    18: 1.07,
    19: 1.04,
    20: 1.02,
    21: 0.97,
    22: 0.97,
    23: 0.82,
}
MS_PER_HOUR = 3_600_000


class Config(BaseModel):
    """Plan maker quotes for an XRPL CLOB pair against a CEX reference price."""

    xrpl_pair: str = Field(
        default="RLUSD-XRP", description="Pair as the xrpl connector names it"
    )
    reference_connector: str = Field(
        default="bitget_perpetual", description="CEX connector supplying fair value"
    )
    reference_pair: str = Field(
        default="XRP-USDT", description="Reference pair for XRP/USD"
    )
    tick_interval_sec: int = Field(default=300, description="Seconds between LLM ticks")
    requote_interval_sec: int = Field(
        default=0,
        description="Actual seconds a quote stays live; 0 = same as tick_interval_sec. "
        "In controller mode this is the controller's executor_refresh_time, NOT the LLM "
        "tick — the bot requotes without the agent, so the LLM tick must not set the floor",
    )
    levels_per_side: int = Field(default=3, description="Quote levels per side")
    total_amount_quote: float = Field(
        default=100.0, description="Capital to deploy, USD"
    )
    adverse_k: float = Field(
        default=1.0,
        description="Multiplier on expected adverse move; higher = wider floor",
    )
    use_vol_clock: bool = Field(
        default=True,
        description="Deseasonalise/re-seasonalise realized vol via the intraday clock",
    )
    amm_fee_pct_fallback: float = Field(
        default=0.1, description="Assumed AMM fee %% if amm_info is unreachable"
    )
    amm_asset2_issuer: str = Field(
        default="",
        description="Issuer address of the non-XRP asset (blank = skip amm_info)",
    )
    amm_asset2_currency: str = Field(
        default="RLUSD", description="Non-XRP asset currency code"
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _hour_mult_span(start_ms: int, end_ms: int) -> float:
    """Duration-weighted mean of HOUR_VOL_MULT over [start_ms, end_ms).

    Averaging across the span matters: a realized-vol lookback covers several hours,
    and a long tick interval means the quote stays live across several more. Using
    only the current hour would misprice both ends.
    """
    if end_ms <= start_ms:
        hour = datetime.fromtimestamp(start_ms / 1000, timezone.utc).hour
        return HOUR_VOL_MULT.get(hour, 1.0)
    total = weight = 0.0
    cursor = start_ms
    while cursor < end_ms:
        hour = datetime.fromtimestamp(cursor / 1000, timezone.utc).hour
        seg_end = min((cursor // MS_PER_HOUR + 1) * MS_PER_HOUR, end_ms)
        w = seg_end - cursor
        total += HOUR_VOL_MULT.get(hour, 1.0) * w
        weight += w
        cursor = seg_end
    return total / weight if weight else 1.0


def _realized_vol_per_sec(closes: list, interval_sec: int) -> float:
    """Std dev of log returns per bar, rescaled to per-second."""
    if len(closes) < 3:
        return 0.0
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var / interval_sec) if interval_sec > 0 else 0.0


async def _fetch_amm_fee_pct(currency: str, issuer: str) -> tuple[float | None, str]:
    """AMM trading fee in percent. Returns (fee_pct, note)."""
    if not issuer:
        return None, "no issuer configured — amm_info skipped"
    payload = {
        "method": "amm_info",
        "params": [
            {
                "asset": {"currency": "XRP"},
                "asset2": {"currency": currency, "issuer": issuer},
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(XRPL_RPC, json=payload)
            data = resp.json().get("result", {})
        amm = data.get("amm")
        if not amm:
            return None, f"amm_info returned no pool ({data.get('error', 'unknown')})"
        # TradingFee is in units of 1/100_000 -> 1000 == 1%
        return float(amm.get("TradingFee", 0)) / 1000.0, "live from amm_info"
    except Exception as exc:  # network/parse — fall back, never raise into a tick
        logger.warning("amm_info fetch failed: %s", exc)
        return None, f"amm_info unreachable ({type(exc).__name__})"


def _norm_candles(raw) -> list:
    return raw if isinstance(raw, list) else raw.get("data", raw.get("candles", []))


def _extract_ref_price(price, fallback: float) -> float:
    """Validate a fetched reference price, else ``fallback``.

    ``fetch_current_price`` already resolves the payload (including a venue
    answering a one-pair request under its own spelling of the pair); this
    only guards the number it hands back, since a non-numeric or non-positive
    answer would otherwise silently misplace every quote — so anything that
    isn't a positive number falls back to the last candle close.
    """
    try:
        num = float(price)
    except (TypeError, ValueError):
        return fallback
    return num if num > 0 else fallback


# ── routine ──────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    out: list[str] = []

    # 1. Reference fair value from the CEX — never from on-ledger data.
    try:
        candles_raw, ref_price = await asyncio.gather(
            client.market_data.get_candles(
                config.reference_connector,
                config.reference_pair,
                interval="1m",
                max_records=120,
            ),
            fetch_current_price(
                client, config.reference_connector, config.reference_pair, strict=True
            ),
        )
    except Exception as exc:
        return f"reference_price: ERROR fetching {config.reference_pair} — {exc}"

    candles = _norm_candles(candles_raw)
    closes = [float(c.get("close", 0)) for c in candles if float(c.get("close", 0)) > 0]
    if not closes:
        return f"reference_price: ERROR no candle data for {config.reference_pair}"

    ref_mid = _extract_ref_price(ref_price, closes[-1])

    # RLUSD/XRP is the inverse of XRP/USD (RLUSD pegged to 1 USD).
    implied_xrpl_price = (1.0 / ref_mid) if ref_mid > 0 else 0.0

    out.append("=== REFERENCE (fair value source) ===")
    out.append(
        f"reference_pair: {config.reference_pair} @ {config.reference_connector}"
    )
    out.append(f"reference_mid_usd: {ref_mid:.6f}")
    out.append(f"implied_{config.xrpl_pair}: {implied_xrpl_price:.6f}  (1 / XRP-USD)")

    # 2. Spread FLOOR — expected adverse move over one requote interval.
    #
    # The raw realized-vol estimate already embeds whatever hours it was measured
    # in, so multiplying it by the current hour's clock value would double-count the
    # seasonal component. Deseasonalise first (divide by the lookback's mean
    # multiplier), then re-seasonalise onto the window the quote will actually be
    # live for (multiply by the forward window's mean multiplier).
    # The floor is set by how long a quote stays exposed, which in controller mode is the
    # controller's executor_refresh_time — the bot requotes without the agent. Using the
    # LLM tick interval instead inflates the floor by sqrt(tick / refresh) and produces a
    # spurious `viable: false` that would block a deploy that is in fact viable.
    requote_sec = max(config.requote_interval_sec or config.tick_interval_sec, 1)

    vol_raw = _realized_vol_per_sec(closes, 60)
    now_ms = int(time.time() * 1000)
    look_start_ms = now_ms - len(closes) * 60_000
    fwd_end_ms = now_ms + requote_sec * 1000

    if config.use_vol_clock:
        mult_look = _hour_mult_span(look_start_ms, now_ms)
        mult_fwd = _hour_mult_span(now_ms, fwd_end_ms)
        vol_deseason = vol_raw / mult_look if mult_look > 0 else vol_raw
        vol_adj = vol_deseason * mult_fwd
    else:
        mult_look = mult_fwd = 1.0
        vol_deseason = vol_adj = vol_raw

    adverse_move = config.adverse_k * vol_adj * math.sqrt(requote_sec)
    floor_bps = adverse_move * 10_000

    def _utc(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M")

    out.append("")
    out.append("=== SPREAD FLOOR (adverse selection) ===")
    out.append(f"vol_clock_enabled: {str(config.use_vol_clock).lower()}")
    out.append(f"realized_vol_raw: {vol_raw * math.sqrt(60) * 100:.4f}% per minute")
    out.append(
        f"hour_mult_lookback: {mult_look:.3f}  "
        f"(UTC {_utc(look_start_ms)}-{_utc(now_ms)}, {len(closes)} min)"
    )
    out.append(
        f"vol_deseasonalized: {vol_deseason * math.sqrt(60) * 100:.4f}% per minute"
    )
    out.append(
        f"hour_mult_forward: {mult_fwd:.3f}  "
        f"(UTC {_utc(now_ms)}-{_utc(fwd_end_ms)}, quote live window)"
    )
    out.append(f"vol_adjusted: {vol_adj * math.sqrt(60) * 100:.4f}% per minute")
    out.append(
        f"tick_interval_sec: {config.tick_interval_sec}  (LLM reasoning cadence)"
    )
    out.append(
        f"requote_interval_sec: {requote_sec}  (quote exposure — THIS sets the floor)"
    )
    if config.requote_interval_sec:
        out.append(
            "note: floor computed from requote_interval_sec, not the LLM tick — correct for "
            "controller mode, where the bot requotes on its own executor_refresh_time"
        )
    else:
        out.append(
            "note: no requote_interval_sec given, so the LLM tick is assumed to be the "
            "exposure window (executor mode). In controller mode pass executor_refresh_time "
            "or the floor will be overstated by sqrt(tick / refresh)"
        )
    out.append(f"expected_adverse_move: {floor_bps:.2f} bps over one interval")
    out.append(f"spread_floor_bps: {floor_bps:.2f}  (per side, k={config.adverse_k})")

    # 3. Spread CEILING — the AMM fee we must undercut to win pathfinding.
    amm_fee_pct, fee_note = await _fetch_amm_fee_pct(
        config.amm_asset2_currency, config.amm_asset2_issuer
    )
    if amm_fee_pct is None:
        amm_fee_pct = config.amm_fee_pct_fallback
        fee_note = f"FALLBACK {amm_fee_pct}% — {fee_note}"
    ceiling_bps = amm_fee_pct * 100

    out.append("")
    out.append("=== SPREAD CEILING (AMM fee to undercut) ===")
    out.append(f"amm_trading_fee_pct: {amm_fee_pct:.4f}%   ({fee_note})")
    out.append(
        f"spread_ceiling_bps: {ceiling_bps:.2f}  (quote wider and flow routes to the AMM)"
    )

    # 4. Viability — the load-bearing verdict.
    viable = floor_bps < ceiling_bps
    headroom = ceiling_bps - floor_bps
    out.append("")
    out.append("=== VIABILITY ===")
    out.append(f"viable: {str(viable).lower()}")
    out.append(f"headroom_bps: {headroom:.2f}")
    if viable:
        target = floor_bps + headroom * 0.5  # sit mid-band
        out.append(f"suggested_spread_bps_per_side: {target:.2f}")
        out.append(f"suggested_bid: {implied_xrpl_price * (1 - target / 10_000):.6f}")
        out.append(f"suggested_ask: {implied_xrpl_price * (1 + target / 10_000):.6f}")
        # pmm_simple's buy_spreads/sell_spreads are FRACTIONS of the reference price
        # (order_price = reference_price * (1 +/- spread)), not bps. Emit the converted
        # values so a bps figure can never be pasted into a controller config: 22 bps
        # entered as 22 would quote at 2200%.
        #
        # Levels are spaced strictly inside (floor, ceiling) rather than as multiples of
        # the target. A geometric ladder walks straight through the AMM fee ceiling on the
        # outer level — 3 levels at 2x/3x a 7 bps target reaches 21 bps against a 10 bps
        # ceiling, and every offer above the ceiling loses the flow to the pool.
        levels = max(config.levels_per_side, 1)
        ladder_bps = [
            floor_bps + headroom * (i + 1) / (levels + 1) for i in range(levels)
        ]
        out.append(
            "controller_spreads (FRACTIONS for pmm_simple buy_spreads/sell_spreads — "
            "never paste bps here): "
            + ",".join(f"{b / 10_000:.6f}" for b in ladder_bps)
        )
        out.append(
            "ladder_bps: "
            + ",".join(f"{b:.2f}" for b in ladder_bps)
            + f"  (all strictly between floor {floor_bps:.2f} and ceiling {ceiling_bps:.2f})"
        )
    else:
        out.append("suggested_action: DO NOT QUOTE")
        out.append(
            "reason: adverse-selection floor meets or exceeds the AMM fee ceiling. "
            "Either shorten the exposure window or wait for vol to fall — in controller "
            "mode that means lowering executor_refresh_time, in executor mode "
            "frequency_sec. Note a long LLM tick does NOT itself widen the floor in "
            "controller mode; only executor_refresh_time does."
        )

    # 5. Reserves and deployable capital.
    n_offers = config.levels_per_side * 2
    reserve_xrp = BASE_RESERVE_XRP + OWNER_RESERVE_XRP * n_offers
    reserve_usd = reserve_xrp * ref_mid
    per_level = config.total_amount_quote / n_offers if n_offers else 0.0

    out.append("")
    out.append("=== RESERVES & SIZING ===")
    out.append(f"levels_per_side: {config.levels_per_side}  (offers: {n_offers})")
    out.append(f"reserve_locked_xrp: {reserve_xrp:.2f}  (~${reserve_usd:.2f})")
    out.append(f"capital_usd: ${config.total_amount_quote:.2f}")
    out.append(f"per_level_notional: ${per_level:.2f}")
    out.append("note: size against FREE balance — reserved XRP is not spendable")

    # A controller's total_amount_quote is denominated in the pair's QUOTE asset. On
    # RLUSD-XRP that is XRP, not USD — passing a USD figure straight through oversizes
    # the deployment by the XRP price (~3x), silently breaching the risk limit.
    quote_asset = (
        config.xrpl_pair.split("-")[-1].upper() if "-" in config.xrpl_pair else ""
    )
    if quote_asset == "XRP" and ref_mid > 0:
        amount_in_quote = config.total_amount_quote / ref_mid
        out.append(
            f"controller_total_amount_quote: {amount_in_quote:.4f}  "
            f"({quote_asset}, = ${config.total_amount_quote:.2f} / {ref_mid:.6f} XRP-USD)"
        )
        out.append(
            "WARNING: quote asset is XRP. A controller's total_amount_quote is in the "
            "QUOTE asset — pass the converted figure above, never the USD number, or the "
            "deploy is oversized by the XRP price."
        )
    else:
        out.append(
            f"controller_total_amount_quote: {config.total_amount_quote:.4f}  "
            f"(quote asset '{quote_asset or 'unknown'}' — verify it is USD-denominated "
            "before passing this to a controller)"
        )

    # 6. Live XRPL book state. Absence is a hard stop, not a warning.
    out.append("")
    out.append("=== XRPL BOOK ===")
    try:
        # Signature is get_order_book(connector_name, trading_pair, depth=10) —
        # passing the pair positionally binds it to connector_name and collides
        # with the keyword ("got multiple values for argument 'connector_name'").
        book = await client.market_data.get_order_book("xrpl", config.xrpl_pair)
        bids = (book or {}).get("bids") or []
        asks = (book or {}).get("asks") or []
        if bids and asks:
            best_bid = float(
                bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0]["price"]
            )
            best_ask = float(
                asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0]["price"]
            )
            mid = (best_bid + best_ask) / 2
            out.append(f"best_bid: {best_bid:.6f}")
            out.append(f"best_ask: {best_ask:.6f}")
            out.append(f"book_spread_bps: {(best_ask - best_bid) / mid * 10_000:.2f}")
            if implied_xrpl_price > 0:
                div = (mid / implied_xrpl_price - 1) * 10_000
                out.append(f"divergence_vs_reference_bps: {div:+.2f}")
                out.append(
                    "note: large divergence means STALE DATA, not opportunity — verify before acting"
                )
        else:
            out.append("status: EMPTY OR UNAVAILABLE — do not quote blind")
    except Exception as exc:
        out.append(f"status: ERROR — {exc}")
        out.append(
            "action: treat as a hard stop; do not place offers without live book state"
        )

    return "\n".join(out)
