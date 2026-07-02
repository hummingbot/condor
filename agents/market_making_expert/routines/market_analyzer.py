"""Multi-timeframe market regime analyzer for MM decision-making."""
import logging
import math
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Analyze market regime across multiple timeframes for a trading pair."""
    trading_pair: str = Field(default="JTO-USDT", description="Trading pair to analyze")
    connector_name: str = Field(default="binance_perpetual", description="Exchange connector")


# ── Technical indicator helpers ──────────────────────────────────────────────

def _calc_atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i].get("high", 0))
        low = float(candles[i].get("low", 0))
        prev_close = float(candles[i - 1].get("close", 0))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs[-period:]))


def _calc_sma(values: list, period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


def _calc_ema(values: list, period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
    return ema


def _calc_ema_series(values: list, period: int) -> list:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    seed = sum(values[:period]) / period if len(values) >= period else sum(values) / len(values)
    result = [None] * (period - 1)
    result.append(seed)
    ema = seed
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
        result.append(ema)
    return result


def _calc_sma_series(values: list, period: int) -> list:
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_adx(candles: list, period: int = 14) -> float:
    if len(candles) < period * 2:
        return 0.0
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(candles)):
        high = float(candles[i].get("high", 0))
        low = float(candles[i].get("low", 0))
        prev_high = float(candles[i - 1].get("high", 0))
        prev_low = float(candles[i - 1].get("low", 0))
        prev_close = float(candles[i - 1].get("close", 0))
        plus_dm = max(high - prev_high, 0)
        minus_dm = max(prev_low - low, 0)
        if plus_dm > minus_dm:
            minus_dm = 0
        elif minus_dm > plus_dm:
            plus_dm = 0
        else:
            plus_dm = minus_dm = 0
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(trs) < period:
        return 0.0
    atr = sum(trs[-period:]) / period
    if atr == 0:
        return 0.0
    plus_di = (sum(plus_dms[-period:]) / period) / atr * 100
    minus_di = (sum(minus_dms[-period:]) / period) / atr * 100
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0.0
    dx = abs(plus_di - minus_di) / di_sum * 100
    return dx


def _calc_bbw(closes: list, period: int = 20) -> float:
    if len(closes) < period:
        return 0.0
    window = closes[-period:]
    sma = sum(window) / period
    if sma == 0:
        return 0.0
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    return (4 * std / sma) * 100


def _classify_regime(adx: float, rsi: float, bbw: float, price_vs_sma: float, vol_ratio: float) -> str:
    if adx > 25 and abs(price_vs_sma) > 1.0:
        if price_vs_sma > 0:
            return "trending_up"
        return "trending_down"
    if bbw > 6.0 or vol_ratio > 1.5:
        return "volatile"
    if adx < 18 and bbw < 3.0:
        return "quiet"
    return "ranging"


def _spread_recommendation(regime: str) -> str:
    if regime in ("trending_up", "trending_down"):
        return "widen (skew toward trend side)"
    if regime == "volatile":
        return "widen both sides or pause"
    if regime == "quiet":
        return "tighten (capture more flow)"
    return "moderate symmetric"


def _analyze_timeframe(candles: list, label: str) -> dict:
    if not candles or len(candles) < 5:
        return {"timeframe": label, "error": "insufficient data"}

    closes = [float(c.get("close", 0)) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]
    last_price = closes[-1]

    sma_20 = _calc_sma(closes, 20)
    ema_9 = _calc_ema(closes, 9)
    rsi = _calc_rsi(closes)
    adx = _calc_adx(candles)
    atr = _calc_atr(candles)
    bbw = _calc_bbw(closes)

    price_vs_sma = ((last_price - sma_20) / sma_20 * 100) if sma_20 else 0
    atr_pct = (atr / last_price * 100) if last_price else 0

    avg_vol = _calc_sma(volumes, 20)
    recent_vol = _calc_sma(volumes[-5:], 5) if len(volumes) >= 5 else avg_vol
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

    if len(closes) >= 2:
        pct_change = (closes[-1] - closes[0]) / closes[0] * 100
    else:
        pct_change = 0.0

    regime = _classify_regime(adx, rsi, bbw, price_vs_sma, vol_ratio)

    return {
        "timeframe": label,
        "regime": regime,
        "price": round(last_price, 6),
        "pct_change": round(pct_change, 2),
        "rsi": round(rsi, 1),
        "adx": round(adx, 1),
        "atr_pct": round(atr_pct, 3),
        "bbw": round(bbw, 2),
        "vol_ratio": round(vol_ratio, 2),
        "price_vs_sma20": round(price_vs_sma, 2),
        "ema9_vs_sma20": round(((ema_9 - sma_20) / sma_20 * 100) if sma_20 else 0, 3),
        "candles_analyzed": len(candles),
    }


# ── Order book helpers ───────────────────────────────────────────────────────

def _normalize_levels(raw: list) -> list:
    result = []
    for entry in raw:
        if isinstance(entry, dict):
            p = entry.get("price", entry.get("p", 0))
            s = entry.get("amount", entry.get("quantity", entry.get("size", entry.get("s", 0))))
        else:
            p, s = entry[0], entry[1]
        p, s = float(p), float(s)
        if s > 0:
            result.append([p, s])
    return result


def _cumulative_volume(levels: list, mid: float, pct: float, side: str) -> float:
    threshold = mid * pct / 100
    total = 0.0
    for price, size in levels:
        if side == "bid" and (mid - price) <= threshold:
            total += size
        elif side == "ask" and (price - mid) <= threshold:
            total += size
    return total


def _find_walls(levels: list, mid: float, threshold_mult: float = 3.0) -> list:
    if not levels:
        return []
    sizes = [s for _, s in levels]
    avg_size = sum(sizes) / len(sizes)
    walls = []
    for price, size in levels:
        if size >= avg_size * threshold_mult:
            dist_pct = abs(price - mid) / mid * 100
            walls.append({
                "price": price,
                "size": size,
                "dist_pct": dist_pct,
                "mult": size / avg_size if avg_size > 0 else 0,
            })
    walls.sort(key=lambda w: w["size"], reverse=True)
    return walls[:5]


def _analyze_orderbook(ob: dict) -> dict:
    bids = _normalize_levels(ob.get("bids", []))
    asks = _normalize_levels(ob.get("asks", []))
    if not bids or not asks:
        return {"error": "insufficient ob data"}

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10000

    total_bid_vol = sum(s for _, s in bids)
    total_ask_vol = sum(s for _, s in asks)
    imbalance_pct = (
        (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol) * 100
        if (total_bid_vol + total_ask_vol) > 0 else 0
    )
    imb_label = (
        "Bullish" if imbalance_pct > 10
        else "Bearish" if imbalance_pct < -10
        else "Neutral"
    )

    liq = {}
    for pct in [1.0, 2.0, 5.0]:
        bid_liq = _cumulative_volume(bids, mid, pct, "bid")
        ask_liq = _cumulative_volume(asks, mid, pct, "ask")
        liq[pct] = {"bid": bid_liq, "ask": ask_liq, "total": bid_liq + ask_liq}

    return {
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": spread_bps,
        "imbalance_pct": imbalance_pct,
        "imb_label": imb_label,
        "liq": liq,
        "bid_walls": _find_walls(bids, mid)[:3],
        "ask_walls": _find_walls(asks, mid)[:3],
    }


# ── Return distribution helpers ──────────────────────────────────────────────

def _percentile(sorted_data: list, p: float) -> float:
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * p / 100
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[lo] + (idx - lo) * (sorted_data[hi] - sorted_data[lo])


def _analyze_returns(closes: list, periods_per_day: int = 24) -> dict:
    if len(closes) < 3:
        return {"error": "insufficient data"}
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, len(closes))]
    sorted_r = sorted(returns)
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / n
    std = variance ** 0.5
    daily_vol = std * math.sqrt(periods_per_day)
    last_price = closes[-1]
    return {
        "returns": returns,
        "p5": round(_percentile(sorted_r, 5), 4),
        "p25": round(_percentile(sorted_r, 25), 4),
        "p50": round(_percentile(sorted_r, 50), 4),
        "p75": round(_percentile(sorted_r, 75), 4),
        "p95": round(_percentile(sorted_r, 95), 4),
        "std": round(std, 4),
        "daily_vol_pct": round(daily_vol, 4),
        "range_1std_low": round(last_price * (1 - std / 100), 6),
        "range_1std_high": round(last_price * (1 + std / 100), 6),
        "range_2std_low": round(last_price * (1 - 2 * std / 100), 6),
        "range_2std_high": round(last_price * (1 + 2 * std / 100), 6),
        "count": n,
    }


# ── Footprint helpers ────────────────────────────────────────────────────────

def _estimate_buy_sell_volume(candle: dict):
    """Estimate buy/sell taker volume from OHLCV: buy=(C-L)/(H-L)*V, sell=(H-C)/(H-L)*V."""
    h = float(candle.get("high", 0))
    l = float(candle.get("low", 0))
    c = float(candle.get("close", 0))
    v = float(candle.get("volume", 0))
    hl = h - l
    if hl == 0:
        return v / 2, v / 2
    return v * (c - l) / hl, v * (h - c) / hl


def _build_footprint(candles: list, n_buckets: int = 25):
    """Aggregate buy/sell volume by price bucket. Returns (prices, buy_vols, sell_vols)."""
    if not candles:
        return [], [], []
    price_min = min(float(c.get("low", 0)) for c in candles)
    price_max = max(float(c.get("high", 0)) for c in candles)
    price_range = price_max - price_min
    if price_range == 0:
        return [], [], []
    bucket_size = price_range / n_buckets
    buy_by = {}
    sell_by = {}
    for candle in candles:
        mid = (float(candle.get("high", 0)) + float(candle.get("low", 0))) / 2
        idx = min(int((mid - price_min) / bucket_size), n_buckets - 1)
        bv, sv = _estimate_buy_sell_volume(candle)
        buy_by[idx] = buy_by.get(idx, 0) + bv
        sell_by[idx] = sell_by.get(idx, 0) + sv
    prices, buys, sells = [], [], []
    for i in range(n_buckets):
        prices.append(round(price_min + (i + 0.5) * bucket_size, 6))
        buys.append(buy_by.get(i, 0))
        sells.append(sell_by.get(i, 0))
    return prices, buys, sells


def _ts_to_dt(ts) -> datetime | None:
    """Convert raw timestamp (int ms or s) to UTC datetime. Returns None on failure."""
    if ts is None:
        return None
    try:
        ts_f = float(ts)
        if ts_f > 1_000_000_000_000:   # milliseconds
            return datetime.fromtimestamp(ts_f / 1000, tz=timezone.utc)
        elif ts_f > 1_000_000_000:     # seconds
            return datetime.fromtimestamp(ts_f, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass
    return None


def _make_ts_list(candles: list) -> list:
    """Return list of datetime objects for candle timestamps (falls back to index)."""
    result = []
    for i, c in enumerate(candles):
        ts = c.get("timestamp", c.get("time", c.get("open_time")))
        dt = _ts_to_dt(ts)
        result.append(dt if dt is not None else i)
    return result


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    pair = config.trading_pair
    connector = config.connector_name

    try:
        import asyncio
        minute_candles, candles_15m, short_candles, long_candles = await asyncio.gather(
            client.market_data.get_candles(connector, pair, interval="1m", max_records=1440),
            client.market_data.get_candles(connector, pair, interval="15m", max_records=100),
            client.market_data.get_candles(connector, pair, interval="5m", max_records=100),
            client.market_data.get_candles(connector, pair, interval="4h", max_records=100),
        )
    except Exception as e:
        return f"Error fetching candles: {e}"

    def _norm(c):
        return c if isinstance(c, list) else c.get("data", c.get("candles", []))

    minute_candles = _norm(minute_candles)
    candles_15m = _norm(candles_15m)
    short_candles = _norm(short_candles)
    long_candles = _norm(long_candles)

    short = _analyze_timeframe(short_candles, "short_5m")
    medium = _analyze_timeframe(candles_15m, "medium_15m")
    long_tf = _analyze_timeframe(long_candles, "long_4h")

    # Return distribution from 1m candles (1440 periods/day)
    ret_dist = {}
    if minute_candles and len(minute_candles) >= 3:
        min_closes = [float(c.get("close", 0)) for c in minute_candles]
        ret_dist = _analyze_returns(min_closes, periods_per_day=1440)

    # Funding rate
    funding_info = ""
    if "perpetual" in connector:
        try:
            funding = await client.market_data.get_funding_info(connector, pair)
            if funding:
                rate = funding.get("rate", funding.get("funding_rate", "N/A"))
                funding_info = f"funding_rate: {rate}"
        except Exception:
            funding_info = "funding_rate: unavailable"

    # Full order book analysis
    ob_data = {}
    try:
        ob = await client.market_data.get_order_book(connector, pair, depth=50)
        if ob:
            ob_data = _analyze_orderbook(ob)
    except Exception:
        ob_data = {"error": "order_book unavailable"}

    regimes = [short.get("regime", "unknown"), medium.get("regime", "unknown"), long_tf.get("regime", "unknown")]
    regime_priority = {"volatile": 4, "trending_up": 3, "trending_down": 3, "ranging": 2, "quiet": 1}
    overall_regime = max(regimes, key=lambda r: regime_priority.get(r, 0))

    table_data = []
    for tf in [short, medium, long_tf]:
        if "error" in tf:
            continue
        table_data.append({
            "Timeframe": tf["timeframe"],
            "Regime": tf["regime"],
            "Price": tf["price"],
            "Change%": tf["pct_change"],
            "RSI": tf["rsi"],
            "ADX": tf["adx"],
            "ATR%": tf["atr_pct"],
            "BBW": tf["bbw"],
            "Vol Ratio": tf["vol_ratio"],
            "vs SMA20": f"{tf['price_vs_sma20']}%",
        })

    summary_parts = [
        f"**Market Analysis: {pair} on {connector}**",
        f"Overall Regime: **{overall_regime}**",
        f"Spread Recommendation: {_spread_recommendation(overall_regime)}",
    ]
    if funding_info:
        summary_parts.append(funding_info)
    if ob_data and "error" not in ob_data:
        summary_parts.append(
            f"book_spread_bps: {ob_data['spread_bps']:.1f} | imbalance: {ob_data['imbalance_pct']:+.1f}% ({ob_data['imb_label']}) | best_bid: {ob_data['best_bid']} | best_ask: {ob_data['best_ask']}"
        )
    if ret_dist and "error" not in ret_dist:
        summary_parts.append(
            f"daily_vol: {ret_dist['daily_vol_pct']:.2f}% | 1-std range: [{ret_dist['range_1std_low']}, {ret_dist['range_1std_high']}]"
        )
    summary = "\n".join(summary_parts)

    # Generate report
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        from condor.reports import ReportBuilder
        builder = ReportBuilder(f"Market Analysis: {pair}")
        builder.source("routine", "market_analyzer").tags(["market-making", "regime", pair.lower()])

        builder.kpi("Overall Regime", overall_regime)
        builder.kpi("Spread Rec.", _spread_recommendation(overall_regime))
        builder.kpi("Price", f"${short['price']}" if "error" not in short else "N/A")
        if funding_info:
            builder.kpi("Funding Rate", funding_info.split(": ", 1)[-1])
        if ob_data and "error" not in ob_data:
            builder.kpi("Book Spread", f"{ob_data['spread_bps']:.1f} bps")
            builder.kpi("OB Imbalance", f"{ob_data['imbalance_pct']:+.1f}% ({ob_data['imb_label']})")
        if ret_dist and "error" not in ret_dist:
            builder.kpi("Daily Vol", f"{ret_dist['daily_vol_pct']:.2f}%")

        # Timeframe analysis table
        builder.table(table_data, ["Timeframe", "Regime", "Price", "Change%", "RSI", "ADX", "ATR%", "BBW", "Vol Ratio", "vs SMA20"])

        # OB liquidity depth table
        if ob_data and "error" not in ob_data:
            liq_table = [
                {
                    "Range": f"±{pct:.0f}%",
                    "Bid Vol": f"{data['bid']:,.4f}",
                    "Ask Vol": f"{data['ask']:,.4f}",
                    "Total Vol": f"{data['total']:,.4f}",
                    "USD (approx)": f"${data['total'] * ob_data['mid']:,.2f}",
                }
                for pct, data in ob_data["liq"].items()
            ]
            builder.table(liq_table, ["Range", "Bid Vol", "Ask Vol", "Total Vol", "USD (approx)"])

            # Walls table
            wall_rows = []
            for w in ob_data["bid_walls"]:
                wall_rows.append({"Side": "BID", "Price": f"${w['price']:,.4f}", "Size": f"{w['size']:,.4f}", "Dist%": f"{w['dist_pct']:.3f}%", "Strength": f"{w['mult']:.1f}x avg"})
            for w in ob_data["ask_walls"]:
                wall_rows.append({"Side": "ASK", "Price": f"${w['price']:,.4f}", "Size": f"{w['size']:,.4f}", "Dist%": f"{w['dist_pct']:.3f}%", "Strength": f"{w['mult']:.1f}x avg"})
            if wall_rows:
                builder.table(wall_rows, ["Side", "Price", "Size", "Dist%", "Strength"])

        # Return distribution histogram (1m candles)
        if ret_dist and "error" not in ret_dist and ret_dist.get("returns"):
            fig = go.Figure()
            fig.add_trace(go.Histogram(
                x=ret_dist["returns"],
                nbinsx=40,
                marker_color="#7c3aed",
                opacity=0.8,
                name="Returns",
            ))
            fig.add_vline(x=ret_dist["p5"], line_dash="dot", line_color="#f59e0b", annotation_text="p5")
            fig.add_vline(x=ret_dist["p95"], line_dash="dot", line_color="#f59e0b", annotation_text="p95")
            fig.add_vline(x=0, line_dash="solid", line_color="#6b7280", line_width=1)
            fig.update_layout(
                title=f"Return Distribution (1m, 24h) — {pair}",
                xaxis_title="Return per candle (%)",
                yaxis_title="Frequency",
                template="plotly_dark",
                height=350,
            )
            builder.plotly(fig)

        # 15m trend chart: candlestick + EMA9 + SMA20
        if candles_15m and len(candles_15m) >= 5:
            ts_15m = _make_ts_list(candles_15m)
            closes_15m = [float(c.get("close", 0)) for c in candles_15m]
            ema9_series = _calc_ema_series(closes_15m, 9)
            sma20_series = _calc_sma_series(closes_15m, 20)
            fig_15m = go.Figure()
            fig_15m.add_trace(go.Candlestick(
                x=ts_15m,
                open=[float(c.get("open", 0)) for c in candles_15m],
                high=[float(c.get("high", 0)) for c in candles_15m],
                low=[float(c.get("low", 0)) for c in candles_15m],
                close=closes_15m,
                name="15m",
                increasing_line_color="#22c55e",
                decreasing_line_color="#ef4444",
            ))
            fig_15m.add_trace(go.Scatter(
                x=ts_15m, y=ema9_series, mode="lines", name="EMA 9",
                line=dict(color="#f59e0b", width=1.5),
            ))
            fig_15m.add_trace(go.Scatter(
                x=ts_15m, y=sma20_series, mode="lines", name="SMA 20",
                line=dict(color="#60a5fa", width=1.5, dash="dot"),
            ))
            fig_15m.update_layout(
                title=f"15m Trend — {pair}",
                xaxis_title="Time", yaxis_title="Price",
                template="plotly_dark", height=450,
                xaxis_rangeslider_visible=False,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            builder.plotly(fig_15m)

        # Footprint chart: 1m candlestick (left) + volume profile (right).
        # Right panel uses Scatter polygons (not go.Bar) to keep the shared Y axis numeric
        # and avoid Plotly silently switching it to categorical (which breaks price alignment).
        #
        # Two overlaid layers per price bucket:
        #   1. Total volume  (buy + sell) — gray translucent, wide — shows Points of Interest
        #   2. Net delta     (buy - sell) — green (buyers dominated) / red (sellers dominated)
        #      positive → extends right; negative → extends left
        if minute_candles and len(minute_candles) >= 5:
            ts_1m = _make_ts_list(minute_candles)
            fp_prices, fp_buys, fp_sells = _build_footprint(minute_candles, n_buckets=25)
            if fp_prices:
                price_lo = min(float(c.get("low", 0)) for c in minute_candles)
                price_hi = max(float(c.get("high", 0)) for c in minute_candles)
                bucket_h = (price_hi - price_lo) / 25
                half = bucket_h / 2

                fp_net = [b - s for b, s in zip(fp_buys, fp_sells)]
                fp_total = [b + s for b, s in zip(fp_buys, fp_sells)]

                # Build Scatter polygon lists.
                # Each bucket is a rectangle: (0, p-half)→(val, p-half)→(val, p+half)→(0, p+half)
                # A None separator closes each polygon via fill="toself".
                total_x: list = []
                total_y: list = []
                net_pos_x: list = []   # net delta > 0 → buyers dominated (green, right)
                net_pos_y: list = []
                net_neg_x: list = []   # net delta < 0 → sellers dominated (red, left/negative)
                net_neg_y: list = []

                for p, t, n in zip(fp_prices, fp_total, fp_net):
                    # Total vol: always extends right from 0
                    total_x.extend([0, t, t, 0, None])
                    total_y.extend([p - half, p - half, p + half, p + half, None])
                    # Net delta: route to pos or neg trace
                    if n >= 0:
                        net_pos_x.extend([0, n, n, 0, None])
                        net_pos_y.extend([p - half, p - half, p + half, p + half, None])
                    else:
                        net_neg_x.extend([0, n, n, 0, None])   # n is negative → extends left
                        net_neg_y.extend([p - half, p - half, p + half, p + half, None])

                fig_fp = make_subplots(
                    rows=1, cols=2,
                    column_widths=[0.65, 0.35],
                    shared_yaxes=True,
                    subplot_titles=["1m Candlestick (24h)", "Volume Profile (24h)"],
                    horizontal_spacing=0.01,
                )
                fig_fp.add_trace(go.Candlestick(
                    x=ts_1m,
                    open=[float(c.get("open", 0)) for c in minute_candles],
                    high=[float(c.get("high", 0)) for c in minute_candles],
                    low=[float(c.get("low", 0)) for c in minute_candles],
                    close=[float(c.get("close", 0)) for c in minute_candles],
                    name="1m",
                    increasing_line_color="#22c55e",
                    decreasing_line_color="#ef4444",
                ), row=1, col=1)

                # Layer 1: Total volume (gray translucent background — shows POIs)
                fig_fp.add_trace(go.Scatter(
                    x=total_x, y=total_y,
                    fill="toself", fillcolor="rgba(156, 163, 175, 0.25)",
                    line=dict(width=0), mode="lines",
                    name="Total Vol",
                ), row=1, col=2)

                # Layer 2a: Net delta positive — buyers dominated (green)
                if any(v is not None for v in net_pos_x):
                    fig_fp.add_trace(go.Scatter(
                        x=net_pos_x, y=net_pos_y,
                        fill="toself", fillcolor="rgba(34, 197, 94, 0.75)",
                        line=dict(width=0), mode="lines",
                        name="Net Δ (Buy)",
                    ), row=1, col=2)

                # Layer 2b: Net delta negative — sellers dominated (red, x values are negative)
                if any(v is not None for v in net_neg_x):
                    fig_fp.add_trace(go.Scatter(
                        x=net_neg_x, y=net_neg_y,
                        fill="toself", fillcolor="rgba(239, 68, 68, 0.75)",
                        line=dict(width=0), mode="lines",
                        name="Net Δ (Sell)",
                    ), row=1, col=2)

                fig_fp.update_layout(
                    title=f"Footprint Chart (1m, 24h) — {pair}",
                    template="plotly_dark", height=600,
                    xaxis_rangeslider_visible=False,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                fig_fp.update_xaxes(title_text="Time", type="date", row=1, col=1)
                fig_fp.update_xaxes(title_text="Volume (net delta | total)", row=1, col=2)
                # Force shared Y axis to linear and pin range to the actual candle price range
                fig_fp.update_yaxes(
                    type="linear",
                    range=[price_lo * 0.999, price_hi * 1.001],
                    title_text="Price",
                )
                # Hide Y-axis tick labels on the right panel (volume profile)
                fig_fp.update_yaxes(showticklabels=False, row=1, col=2)
                builder.plotly(fig_fp)

        builder.markdown(summary)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary
