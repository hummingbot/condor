import logging
import math
import time
from statistics import mean, median, stdev

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

CATEGORY = "Analysis"

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Analyze 1s BTC candles and recommend optimal bid/ask quote placement."""

    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    connector_name: str = Field(default="binance", description="Exchange connector")
    lookback_hours: float = Field(
        default=4.0, description="Hours of 1s candles to analyze"
    )
    total_capital_quote: float = Field(
        default=10000, description="Total capital in quote currency"
    )
    alloc_per_side_pct: float = Field(
        default=10.0, description="% of capital per side per quote"
    )
    maker_fee: float = Field(default=0.0, description="Maker fee rate (0 = zero fee)")
    taker_fee: float = Field(
        default=0.001, description="Taker fee rate (fallback exit)"
    )
    inventory_stop_pct: float = Field(
        default=0.05, description="Stop-loss % if one side fills and price keeps moving"
    )


def _binance_symbol(trading_pair: str) -> str:
    return trading_pair.replace("-", "")


async def _fetch_1s_candles(symbol: str, hours: float) -> list[dict]:
    """Fetch 1s candles with pagination (Binance max 1000 per request)."""
    total_needed = int(hours * 3600)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(hours * 3600 * 1000)

    all_candles = []
    cursor = start_ms

    async with aiohttp.ClientSession() as session:
        while cursor < end_ms and len(all_candles) < total_needed:
            params = {
                "symbol": symbol,
                "interval": "1s",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
            async with session.get(
                "https://api.binance.com/api/v3/klines", params=params
            ) as resp:
                resp.raise_for_status()
                raw = await resp.json()

            if not raw:
                break

            for k in raw:
                all_candles.append(
                    {
                        "ts": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
                )

            cursor = int(raw[-1][0]) + 1000  # next batch after last candle

    return all_candles


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(len(sorted_vals) * pct)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def _rolling_ranges(closes: list[float], window: int) -> list[float]:
    ranges = []
    for i in range(window, len(closes)):
        segment = closes[i - window : i + 1]
        hi, lo = max(segment), min(segment)
        if lo > 0:
            ranges.append((hi - lo) / lo * 100)
    return ranges


def _analyze(candles: list[dict]) -> dict:
    if len(candles) < 120:
        return {}

    closes = [c["close"] for c in candles]
    mid = closes[-1]

    ranges_1s = [
        (c["high"] - c["low"]) / c["low"] * 100 if c["low"] > 0 else 0 for c in candles
    ]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]

    # Autocorrelation
    n = len(returns)
    mean_r = mean(returns) if returns else 0
    var_r = sum((r - mean_r) ** 2 for r in returns) / n if n > 0 else 0
    autocorr = 0.0
    if var_r > 0 and n > 1:
        autocorr = sum(
            (returns[i] - mean_r) * (returns[i - 1] - mean_r) for i in range(1, n)
        ) / (n * var_r)

    vol_1s = stdev(returns) if len(returns) > 1 else 0

    up_moves = sum(1 for r in returns if r > 0)
    total_moves = sum(1 for r in returns if r != 0)
    up_ratio = up_moves / total_moves if total_moves else 0.5

    # Rolling ranges at key windows
    windows = {
        "1s": ranges_1s,
        "5s": [],
        "10s": [],
        "30s": [],
        "60s": [],
        "5m": [],
        "15m": [],
    }
    for w, secs in [
        ("5s", 5),
        ("10s", 10),
        ("30s", 30),
        ("60s", 60),
        ("5m", 300),
        ("15m", 900),
    ]:
        if len(closes) > secs:
            windows[w] = _rolling_ranges(closes, secs)

    def pcts(vals):
        if not vals:
            return {
                "p25": 0,
                "p50": 0,
                "p75": 0,
                "p90": 0,
                "p95": 0,
                "mean": 0,
                "max": 0,
            }
        s = sorted(vals)
        return {
            "p25": _percentile(s, 0.25),
            "p50": _percentile(s, 0.50),
            "p75": _percentile(s, 0.75),
            "p90": _percentile(s, 0.90),
            "p95": _percentile(s, 0.95),
            "mean": mean(vals),
            "max": max(vals),
        }

    stats = {
        "mid_price": mid,
        "candle_count": len(candles),
        "price_high": max(closes),
        "price_low": min(closes),
        "vol_1s_pct": vol_1s,
        "vol_1m_pct": vol_1s * math.sqrt(60),
        "autocorrelation": autocorr,
        "up_ratio": up_ratio,
    }
    for key, vals in windows.items():
        stats[f"r_{key}"] = pcts(vals)

    # How often does price cross back through a level within N seconds?
    # (mean reversion frequency — key for quoting)
    for window_secs, label in [(30, "30s"), (60, "60s"), (300, "5m")]:
        if len(closes) <= window_secs * 2:
            stats[f"cross_{label}"] = 0.0
            continue
        crosses = 0
        samples = 0
        step = max(1, window_secs // 2)
        for i in range(window_secs, len(closes) - window_secs, step):
            ref = closes[i]
            future = closes[i : i + window_secs]
            went_up = any(p > ref for p in future)
            went_down = any(p < ref for p in future)
            if went_up and went_down:
                crosses += 1
            samples += 1
        stats[f"cross_{label}"] = crosses / samples if samples else 0.0

    return stats


def _simulate_quotes(
    candles: list[dict],
    dist_pct: float,
    side_capital: float,
    maker_fee: float,
    stop_pct: float,
    track_curve: bool = False,
) -> dict:
    """Simulate quoting strategy on historical 1s candles.

    Places a buy at mid-dist and sell at mid+dist every 60s.
    Tracks fills, round trips, one-sided exposure, and stop-outs.
    """
    closes = [c["close"] for c in candles]
    n = len(closes)
    if n < 60:
        return {}

    round_trips = 0
    stop_outs = 0
    total_profit = 0.0
    total_loss = 0.0
    one_sided_fills = 0
    quote_cycles = 0
    cumulative = 0.0
    pnl_curve = []  # (cycle_index, cumulative_pnl)
    price_curve = []  # (cycle_index, price)

    i = 0
    while i < n - 1:
        mid = closes[i]
        buy_target = mid * (1 - dist_pct / 100)
        sell_target = mid * (1 + dist_pct / 100)
        buy_filled = False
        sell_filled = False
        quote_cycles += 1

        window_end = min(i + 60, n)
        for j in range(i + 1, window_end):
            low = candles[j]["low"]
            high = candles[j]["high"]
            if not buy_filled and low <= buy_target:
                buy_filled = True
            if not sell_filled and high >= sell_target:
                sell_filled = True
            if buy_filled and sell_filled:
                break

        cycle_pnl = 0.0
        if buy_filled and sell_filled:
            spread = sell_target - buy_target
            units = side_capital / buy_target
            fee = (buy_target * maker_fee + sell_target * maker_fee) * units
            cycle_pnl = spread * units - fee
            total_profit += cycle_pnl
            round_trips += 1
        elif buy_filled or sell_filled:
            one_sided_fills += 1
            if buy_filled:
                exit_price = closes[window_end - 1]
                pnl_pct = (exit_price - buy_target) / buy_target * 100
                if pnl_pct < -stop_pct * 100:
                    loss = min(
                        abs(pnl_pct / 100) * side_capital, side_capital * stop_pct
                    )
                    total_loss += loss
                    cycle_pnl = -loss
                    stop_outs += 1
                elif pnl_pct > 0:
                    gain = pnl_pct / 100 * side_capital * 0.5
                    total_profit += gain
                    cycle_pnl = gain
            else:
                exit_price = closes[window_end - 1]
                pnl_pct = (sell_target - exit_price) / sell_target * 100
                if pnl_pct < -stop_pct * 100:
                    loss = min(
                        abs(pnl_pct / 100) * side_capital, side_capital * stop_pct
                    )
                    total_loss += loss
                    cycle_pnl = -loss
                    stop_outs += 1
                elif pnl_pct > 0:
                    gain = pnl_pct / 100 * side_capital * 0.5
                    total_profit += gain
                    cycle_pnl = gain

        cumulative += cycle_pnl
        if track_curve:
            elapsed_min = i / 60
            pnl_curve.append((elapsed_min, round(cumulative, 4)))
            price_curve.append((elapsed_min, mid))

        i = window_end

    hours = n / 3600
    scale_24h = 24 / hours if hours > 0 else 1

    result = {
        "quote_cycles": quote_cycles,
        "round_trips": round_trips,
        "one_sided_fills": one_sided_fills,
        "stop_outs": stop_outs,
        "total_profit": total_profit,
        "total_loss": total_loss,
        "net_pnl": total_profit - total_loss,
        "net_per_day": (total_profit - total_loss) * scale_24h,
        "net_per_month": (total_profit - total_loss) * scale_24h * 30,
        "hours": hours,
        "rt_rate": round_trips / quote_cycles if quote_cycles else 0,
        "fill_rate": (
            (round_trips + one_sided_fills) / quote_cycles if quote_cycles else 0
        ),
    }
    if track_curve:
        result["pnl_curve"] = pnl_curve
        result["price_curve"] = price_curve
    return result


def _compute_scenarios(
    stats: dict,
    candles: list[dict],
    capital: float,
    alloc_pct: float,
    maker_fee: float,
    taker_fee: float,
    stop_pct: float,
) -> list[dict]:
    """Run simulation at multiple spread distances and return ranked scenarios."""
    mid = stats["mid_price"]
    autocorr = stats["autocorrelation"]
    r_30s = stats["r_30s"]
    r_60s = stats["r_60s"]
    cross_60s = stats.get("cross_60s", 0.5)

    side_capital = capital * (alloc_pct / 100)
    max_concurrent = int(100 / alloc_pct)

    # Test distances from tight to wide based on actual range data
    test_distances = sorted(
        set(
            [
                r_30s["p25"],
                r_30s["p50"],
                r_30s["p75"],
                r_30s["p90"],
                r_60s["p25"],
                r_60s["p50"],
                r_60s["p75"],
                r_60s["p90"],
                0.005,
                0.010,
                0.015,
                0.020,
                0.030,
                0.050,
            ]
        )
    )
    # Filter out zeros and very tiny values
    test_distances = [d for d in test_distances if d >= 0.003]

    scenarios = []
    for dist in test_distances:
        sim = _simulate_quotes(candles, dist, side_capital, maker_fee, stop_pct)
        if not sim or sim["quote_cycles"] == 0:
            continue

        buy_price = mid * (1 - dist / 100)
        sell_price = mid * (1 + dist / 100)
        spread_usd = sell_price - buy_price

        scenarios.append(
            {
                "dist_pct": round(dist, 5),
                "dist_usd": round(mid * dist / 100, 2),
                "spread_usd": round(spread_usd, 2),
                "side_capital": round(side_capital, 2),
                "buy_price": round(buy_price, 2),
                "sell_price": round(sell_price, 2),
                "quote_cycles": sim["quote_cycles"],
                "round_trips": sim["round_trips"],
                "one_sided": sim["one_sided_fills"],
                "stop_outs": sim["stop_outs"],
                "rt_rate": sim["rt_rate"],
                "fill_rate": sim["fill_rate"],
                "profit": round(sim["total_profit"], 2),
                "loss": round(sim["total_loss"], 2),
                "net": round(sim["net_pnl"], 2),
                "net_day": round(sim["net_per_day"], 2),
                "net_month": round(sim["net_per_month"], 2),
                "hours": round(sim["hours"], 2),
            }
        )

    # Sort by net daily profit descending
    scenarios.sort(key=lambda s: s["net_day"], reverse=True)
    return scenarios


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    symbol = _binance_symbol(config.trading_pair)

    try:
        candles = await _fetch_1s_candles(symbol, config.lookback_hours)
    except Exception as e:
        return f"Failed to fetch 1s candles: {e}"

    if len(candles) < 120:
        return f"Not enough candles: got {len(candles)}, need at least 120"

    stats = _analyze(candles)
    if not stats:
        return "Analysis failed: insufficient data"

    scenarios = _compute_scenarios(
        stats,
        candles,
        config.total_capital_quote,
        config.alloc_per_side_pct,
        config.maker_fee,
        config.taker_fee,
        config.inventory_stop_pct,
    )
    mid = stats["mid_price"]
    side_capital = config.total_capital_quote * config.alloc_per_side_pct / 100
    max_concurrent = int(100 / config.alloc_per_side_pct)

    lines = [
        f"Quote Optimizer - {config.trading_pair}",
        f"{'=' * 55}",
        "",
        f"Mid: ${mid:,.2f} | Range: ${stats['price_low']:,.2f}-${stats['price_high']:,.2f}",
        f"Candles: {stats['candle_count']:,} (1s) | Lookback: {config.lookback_hours}h",
        "",
        "-- Micro-structure --",
        f"Vol 1s: {stats['vol_1s_pct']:.5f}% | Vol 1m: {stats['vol_1m_pct']:.4f}%",
        f"Autocorr: {stats['autocorrelation']:.3f} ({'mean-reverting' if stats['autocorrelation'] < -0.05 else 'trending' if stats['autocorrelation'] > 0.05 else 'neutral'})",
        f"Direction: {stats['up_ratio']:.1%} up | Cross-back (60s): {stats.get('cross_60s', 0):.0%}",
        "",
        "-- Rolling Ranges --",
        f"{'Window':>8} | {'P25':>10} | {'P50':>10} | {'P75':>10} | {'P90':>10}",
        f"{'-' * 60}",
    ]

    for label, key in [
        ("1s", "r_1s"),
        ("5s", "r_5s"),
        ("10s", "r_10s"),
        ("30s", "r_30s"),
        ("60s", "r_60s"),
        ("5m", "r_5m"),
        ("15m", "r_15m"),
    ]:
        r = stats.get(key, {})
        if not r or (r.get("p50", 0) == 0 and r.get("p90", 0) == 0):
            continue
        lines.append(
            f"{label:>8} | {r['p25']:>9.5f}% | {r['p50']:>9.5f}% | {r['p75']:>9.5f}% | {r['p90']:>9.5f}%"
        )

    lines.append("")
    lines.append("In USD:")
    for label, key in [("30s", "r_30s"), ("60s", "r_60s"), ("5m", "r_5m")]:
        r = stats.get(key, {})
        if not r or r.get("p50", 0) == 0:
            continue
        lines.append(
            f"  {label}: P50=${mid * r['p50'] / 100:.2f} | P75=${mid * r['p75'] / 100:.2f} | P90=${mid * r['p90'] / 100:.2f}"
        )

    lines += [
        "",
        f"-- Config: {config.alloc_per_side_pct:.0f}% per side = ${side_capital:,.0f} | stop: {config.inventory_stop_pct:.1%} --",
        f"Max concurrent: {max_concurrent} per side | Reserve: {100 - config.alloc_per_side_pct * 2:.0f}%",
        f"Max loss per shot: ${side_capital * config.inventory_stop_pct:.2f} | Shots to ruin: {int(config.total_capital_quote / (side_capital * config.inventory_stop_pct)) if side_capital * config.inventory_stop_pct > 0 else 0}",
        "",
        (
            f"-- Backtest Results ({scenarios[0]['hours']:.1f}h data, extrapolated to 24h) --"
            if scenarios
            else "-- No scenarios --"
        ),
        "",
    ]

    if scenarios:
        lines.append(
            f"{'Dist':>7} | {'Spread':>8} | {'RTs':>4} | {'1-side':>6} | {'Stops':>5} | {'RT%':>5} | {'Profit':>8} | {'Loss':>8} | {'Net/day':>10} | {'Net/mo':>10}"
        )
        lines.append(f"{'-' * 95}")

        for s in scenarios[:12]:
            marker = " <-- best" if s is scenarios[0] else ""
            lines.append(
                f"{s['dist_pct']:>6.3f}% | ${s['dist_usd']:>6.1f} | {s['round_trips']:>4} | {s['one_sided']:>6} | {s['stop_outs']:>5} | {s['rt_rate']:>4.0%} | ${s['profit']:>7.2f} | ${s['loss']:>7.2f} | ${s['net_day']:>9.2f} | ${s['net_month']:>9.0f}{marker}"
            )

        best = scenarios[0]
        lines += [
            "",
            f"-- Best Distance: {best['dist_pct']:.4f}% (${best['dist_usd']:.2f}) --",
            f"BUY:  ${best['buy_price']:,.2f}",
            f"SELL: ${best['sell_price']:,.2f}",
            f"Spread: ${best['spread_usd']:.2f}",
            f"Round trips in {best['hours']:.1f}h: {best['round_trips']} ({best['rt_rate']:.0%} of cycles)",
            f"1-sided fills: {best['one_sided']} | Stop-outs: {best['stop_outs']}",
            f"Profit: ${best['profit']:.2f} | Loss: ${best['loss']:.2f}",
            f"NET: ${best['net']:.2f} in {best['hours']:.1f}h",
            f"Extrapolated: ${best['net_day']:.2f}/day | ${best['net_month']:,.2f}/month",
        ]

    result = "\n".join(lines)

    # Re-run best scenario with curve tracking for chart
    best_curve_sim = None
    if scenarios:
        best = scenarios[0]
        best_curve_sim = _simulate_quotes(
            candles,
            best["dist_pct"],
            best["side_capital"],
            config.maker_fee,
            config.inventory_stop_pct,
            track_curve=True,
        )

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Quote Optimizer: {config.trading_pair}")
        builder.source("routine", "quote_optimizer").tags(
            ["market-making", "1s", config.trading_pair]
        )
        builder.markdown(result)

        if scenarios:
            # Chart 1: Net/day by distance (bar chart)
            fig1 = go.Figure()
            dists = [f"{s['dist_pct']:.3f}%" for s in scenarios[:12]]
            nets = [s["net_day"] for s in scenarios[:12]]
            rts = [s["round_trips"] for s in scenarios[:12]]
            colors = ["#22c55e" if n > 0 else "#ef4444" for n in nets]

            fig1.add_trace(
                go.Bar(
                    x=dists,
                    y=nets,
                    name="Net/Day",
                    marker_color=colors,
                    text=[f"${n:.1f}<br>{r} RTs" for n, r in zip(nets, rts)],
                    textposition="outside",
                )
            )
            fig1.update_layout(
                title=f"Net Profit/Day by Quote Distance ({config.alloc_per_side_pct:.0f}% alloc, ${side_capital:,.0f}/side)",
                xaxis_title="Quote Distance",
                yaxis_title="Net $/Day",
                template="plotly_dark",
                height=400,
                yaxis=dict(zeroline=True, zerolinecolor="rgba(255,255,255,0.3)"),
            )
            builder.plotly(fig1)

            # Chart 2: Cumulative PnL + Price for best scenario
            if best_curve_sim and best_curve_sim.get("pnl_curve"):
                pnl_curve = best_curve_sim["pnl_curve"]
                price_curve = best_curve_sim["price_curve"]
                minutes = [p[0] for p in pnl_curve]
                pnls = [p[1] for p in pnl_curve]
                prices = [p[1] for p in price_curve]

                fig2 = make_subplots(
                    rows=2,
                    cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.08,
                    subplot_titles=[
                        f"Cumulative PnL — Best Distance {scenarios[0]['dist_pct']:.3f}%",
                        "BTC Price",
                    ],
                    row_heights=[0.6, 0.4],
                )
                fig2.add_trace(
                    go.Scatter(
                        x=minutes,
                        y=pnls,
                        mode="lines",
                        name="Cumulative PnL",
                        line=dict(color="#22c55e", width=2),
                        fill="tozeroy",
                        fillcolor="rgba(34,197,94,0.1)",
                    ),
                    row=1,
                    col=1,
                )
                fig2.add_trace(
                    go.Scatter(
                        x=minutes,
                        y=prices,
                        mode="lines",
                        name="BTC Price",
                        line=dict(color="#60a5fa", width=1),
                    ),
                    row=2,
                    col=1,
                )
                fig2.update_layout(
                    template="plotly_dark",
                    height=500,
                    showlegend=False,
                    xaxis2_title="Time (minutes)",
                )
                fig2.update_yaxes(title_text="PnL ($)", row=1, col=1)
                fig2.update_yaxes(title_text="Price ($)", row=2, col=1)
                builder.plotly(fig2)

            builder.table(
                [
                    {
                        "Distance": f"{s['dist_pct']:.4f}%",
                        "Spread": f"${s['spread_usd']:.2f}",
                        "Round Trips": s["round_trips"],
                        "1-Sided": s["one_sided"],
                        "Stops": s["stop_outs"],
                        "Net/Day": f"${s['net_day']:.2f}",
                        "Net/Month": f"${s['net_month']:,.0f}",
                    }
                    for s in scenarios[:12]
                ]
            )

        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    # Send oscillation chart to Telegram via matplotlib
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    if chat_id and context.bot:
        try:
            import io

            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.ticker as mticker

            closes = [c["close"] for c in candles]
            minutes = [i / 60 for i in range(len(closes))]

            fig, axes = plt.subplots(
                3, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1.5, 1]}
            )
            fig.patch.set_facecolor("#1a1a2e")
            for ax in axes:
                ax.set_facecolor("#16213e")
                ax.tick_params(colors="white")
                ax.xaxis.label.set_color("white")
                ax.yaxis.label.set_color("white")
                ax.title.set_color("white")
                for spine in ax.spines.values():
                    spine.set_color("#333")

            # Chart 1: Price with rolling min/max bands
            ax1 = axes[0]
            ax1.plot(
                minutes,
                closes,
                color="#60a5fa",
                linewidth=0.6,
                alpha=0.9,
                label="Price",
            )

            # Rolling bands (30s and 60s)
            for window, color, alpha, label in [
                (30, "#22c55e", 0.15, "30s"),
                (60, "#f59e0b", 0.1, "60s"),
            ]:
                if len(closes) > window:
                    roll_hi = [
                        max(closes[max(0, i - window) : i + 1])
                        for i in range(len(closes))
                    ]
                    roll_lo = [
                        min(closes[max(0, i - window) : i + 1])
                        for i in range(len(closes))
                    ]
                    ax1.fill_between(
                        minutes,
                        roll_lo,
                        roll_hi,
                        alpha=alpha,
                        color=color,
                        label=f"{label} range",
                    )

            ax1.set_title(
                f"{config.trading_pair} — Price Oscillation ({config.lookback_hours}h, 1s candles)",
                fontsize=13,
            )
            ax1.set_ylabel("Price")
            ax1.legend(
                loc="upper left",
                fontsize=8,
                facecolor="#1a1a2e",
                edgecolor="#333",
                labelcolor="white",
            )
            ax1.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda x, _: f"{x:,.0f}")
            )

            # Chart 2: Rolling range in quote currency (how much it oscillates)
            ax2 = axes[1]
            for window, color, label in [
                (30, "#22c55e", "30s range"),
                (60, "#f59e0b", "60s range"),
            ]:
                if len(closes) > window:
                    roll_range = [
                        max(closes[max(0, i - window) : i + 1])
                        - min(closes[max(0, i - window) : i + 1])
                        for i in range(len(closes))
                    ]
                    roll_mins = [i / 60 for i in range(len(roll_range))]
                    ax2.plot(
                        roll_mins,
                        roll_range,
                        color=color,
                        linewidth=0.7,
                        alpha=0.8,
                        label=label,
                    )

            # Add P50/P75 reference lines
            r_60s = stats.get("r_60s", {})
            if r_60s.get("p50", 0) > 0:
                p50_usd = mid * r_60s["p50"] / 100
                p75_usd = mid * r_60s["p75"] / 100
                ax2.axhline(
                    p50_usd, color="#f59e0b", linestyle="--", alpha=0.5, linewidth=0.8
                )
                ax2.text(
                    minutes[-1] * 0.98,
                    p50_usd,
                    f" P50={p50_usd:.0f}",
                    color="#f59e0b",
                    fontsize=7,
                    va="bottom",
                    ha="right",
                )
                ax2.axhline(
                    p75_usd, color="#f59e0b", linestyle=":", alpha=0.4, linewidth=0.8
                )
                ax2.text(
                    minutes[-1] * 0.98,
                    p75_usd,
                    f" P75={p75_usd:.0f}",
                    color="#f59e0b",
                    fontsize=7,
                    va="bottom",
                    ha="right",
                )

            ax2.set_title("Rolling Range (oscillation amplitude)", fontsize=11)
            ax2.set_ylabel("Range ($)")
            ax2.legend(
                loc="upper right",
                fontsize=8,
                facecolor="#1a1a2e",
                edgecolor="#333",
                labelcolor="white",
            )

            # Chart 3: Returns distribution (1s)
            ax3 = axes[2]
            returns = [
                (closes[i] - closes[i - 1]) / closes[i - 1] * 100
                for i in range(1, len(closes))
                if closes[i - 1] > 0
            ]
            nonzero_returns = [r for r in returns if r != 0]
            if nonzero_returns:
                ax3.hist(
                    nonzero_returns,
                    bins=100,
                    color="#818cf8",
                    alpha=0.7,
                    edgecolor="none",
                )
                ax3.axvline(0, color="white", linewidth=0.5, alpha=0.5)
            ax3.set_title("1s Return Distribution", fontsize=11)
            ax3.set_xlabel("Time (minutes)")
            ax3.set_ylabel("Count")

            plt.tight_layout()
            buf = io.BytesIO()
            fig.savefig(
                buf,
                format="png",
                dpi=150,
                bbox_inches="tight",
                facecolor=fig.get_facecolor(),
            )
            buf.seek(0)
            plt.close(fig)

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=buf,
                caption=(
                    f"{config.trading_pair} Oscillation Analysis\n"
                    f"Vol 1s: {stats['vol_1s_pct']:.5f}% | Autocorr: {stats['autocorrelation']:.3f}\n"
                    f"60s range P50: ${mid * r_60s.get('p50', 0) / 100:.0f} | P75: ${mid * r_60s.get('p75', 0) / 100:.0f}"
                ),
            )

            # Chart 2: Scenarios comparison
            if scenarios:
                fig2, ax = plt.subplots(figsize=(12, 5))
                fig2.patch.set_facecolor("#1a1a2e")
                ax.set_facecolor("#16213e")
                ax.tick_params(colors="white")
                ax.xaxis.label.set_color("white")
                ax.yaxis.label.set_color("white")
                ax.title.set_color("white")
                for spine in ax.spines.values():
                    spine.set_color("#333")

                dists = [f"{s['dist_pct']:.3f}%" for s in scenarios[:12]]
                nets = [s["net_day"] for s in scenarios[:12]]
                rts = [s["round_trips"] for s in scenarios[:12]]
                colors = ["#22c55e" if n > 0 else "#ef4444" for n in nets]

                bars = ax.bar(dists, nets, color=colors, alpha=0.85, edgecolor="none")
                for bar, n, r in zip(bars, nets, rts):
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + max(nets) * 0.02,
                        f"${n:.0f}\n{r}RT",
                        ha="center",
                        va="bottom",
                        color="white",
                        fontsize=7,
                    )

                ax.set_title(
                    f"Net $/Day by Quote Distance — {config.trading_pair} ({config.alloc_per_side_pct:.0f}% alloc)",
                    fontsize=12,
                    color="white",
                )
                ax.set_xlabel("Quote Distance")
                ax.set_ylabel("Net $/Day")
                ax.axhline(0, color="white", linewidth=0.3, alpha=0.3)
                plt.xticks(rotation=45)
                plt.tight_layout()

                buf2 = io.BytesIO()
                fig2.savefig(
                    buf2,
                    format="png",
                    dpi=150,
                    bbox_inches="tight",
                    facecolor=fig2.get_facecolor(),
                )
                buf2.seek(0)
                plt.close(fig2)

                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=buf2,
                    caption=f"Best: {scenarios[0]['dist_pct']:.3f}% → ${scenarios[0]['net_day']:.0f}/day ({scenarios[0]['round_trips']} RTs in {scenarios[0]['hours']:.0f}h)",
                )

        except Exception as e:
            logger.warning(f"Failed to send Telegram charts: {e}")

    return result
