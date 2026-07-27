"""Plan XRPL CLOB maker quotes: reference fair value, spread bounds, viability verdict."""
import asyncio
import logging
import math

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# Public XRPL JSON-RPC. Used only for amm_info (pool trading fee), which sets the
# spread ceiling. Falls back to the configured value when unreachable.
XRPL_RPC = "https://xrplcluster.com/"

# XRPL reserve schedule (validator-voted; verify if the ledger amends it).
BASE_RESERVE_XRP = 1.0
OWNER_RESERVE_XRP = 0.2


class Config(BaseModel):
    """Plan maker quotes for an XRPL CLOB pair against a CEX reference price."""

    xrpl_pair: str = Field(default="RLUSD-XRP", description="Pair as the xrpl connector names it")
    reference_connector: str = Field(
        default="bitget_perpetual", description="CEX connector supplying fair value"
    )
    reference_pair: str = Field(default="XRP-USDT", description="Reference pair for XRP/USD")
    tick_interval_sec: int = Field(default=300, description="Seconds between requotes")
    levels_per_side: int = Field(default=3, description="Quote levels per side")
    total_amount_quote: float = Field(default=100.0, description="Capital to deploy, USD")
    adverse_k: float = Field(
        default=1.0, description="Multiplier on expected adverse move; higher = wider floor"
    )
    amm_fee_pct_fallback: float = Field(
        default=0.1, description="Assumed AMM fee %% if amm_info is unreachable"
    )
    amm_asset2_issuer: str = Field(
        default="", description="Issuer address of the non-XRP asset (blank = skip amm_info)"
    )
    amm_asset2_currency: str = Field(default="RLUSD", description="Non-XRP asset currency code")


# ── helpers ──────────────────────────────────────────────────────────────────


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
        "params": [{"asset": {"currency": "XRP"}, "asset2": {"currency": currency, "issuer": issuer}}],
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


# ── routine ──────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    out: list[str] = []

    # 1. Reference fair value from the CEX — never from on-ledger data.
    try:
        candles_raw, ref_prices = await asyncio.gather(
            client.market_data.get_candles(
                config.reference_connector, config.reference_pair, interval="1m", max_records=120
            ),
            client.market_data.get_prices(config.reference_connector, [config.reference_pair]),
        )
    except Exception as exc:
        return f"reference_price: ERROR fetching {config.reference_pair} — {exc}"

    candles = _norm_candles(candles_raw)
    closes = [float(c.get("close", 0)) for c in candles if float(c.get("close", 0)) > 0]
    if not closes:
        return f"reference_price: ERROR no candle data for {config.reference_pair}"

    if isinstance(ref_prices, dict):
        ref_mid = float(
            ref_prices.get(config.reference_pair)
            or next(iter(ref_prices.values()), 0)
            or closes[-1]
        )
    else:
        ref_mid = closes[-1]

    # RLUSD/XRP is the inverse of XRP/USD (RLUSD pegged to 1 USD).
    implied_xrpl_price = (1.0 / ref_mid) if ref_mid > 0 else 0.0

    out.append("=== REFERENCE (fair value source) ===")
    out.append(f"reference_pair: {config.reference_pair} @ {config.reference_connector}")
    out.append(f"reference_mid_usd: {ref_mid:.6f}")
    out.append(f"implied_{config.xrpl_pair}: {implied_xrpl_price:.6f}  (1 / XRP-USD)")

    # 2. Spread FLOOR — expected adverse move over one requote interval.
    vol_per_sec = _realized_vol_per_sec(closes, 60)
    adverse_move = config.adverse_k * vol_per_sec * math.sqrt(max(config.tick_interval_sec, 1))
    floor_bps = adverse_move * 10_000

    out.append("")
    out.append("=== SPREAD FLOOR (adverse selection) ===")
    out.append(f"realized_vol_1m: {vol_per_sec * math.sqrt(60) * 100:.4f}% per minute")
    out.append(f"tick_interval_sec: {config.tick_interval_sec}")
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
    out.append(f"spread_ceiling_bps: {ceiling_bps:.2f}  (quote wider and flow routes to the AMM)")

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
    else:
        out.append("suggested_action: DO NOT QUOTE")
        out.append(
            "reason: adverse-selection floor meets or exceeds the AMM fee ceiling. "
            "Either requote faster (lower tick_interval_sec) or wait for vol to fall."
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
    out.append(f"total_amount_quote: ${config.total_amount_quote:.2f}")
    out.append(f"per_level_notional: ${per_level:.2f}")
    out.append("note: size against FREE balance — reserved XRP is not spendable")

    # 6. Live XRPL book state. Absence is a hard stop, not a warning.
    out.append("")
    out.append("=== XRPL BOOK ===")
    try:
        book = await client.market_data.get_order_book(config.xrpl_pair, connector_name="xrpl")
        bids = (book or {}).get("bids") or []
        asks = (book or {}).get("asks") or []
        if bids and asks:
            best_bid = float(bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0]["price"])
            best_ask = float(asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0]["price"])
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
        out.append("action: treat as a hard stop; do not place offers without live book state")

    return "\n".join(out)
