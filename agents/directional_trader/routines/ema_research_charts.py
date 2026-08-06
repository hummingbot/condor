"""EMA trend-following research: distribution analysis, EMA sweep, and Telegram charts for BTC/ETH/SOL."""

CATEGORY = "Research"

import io
import logging
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """EMA trend research — fetch 30d candles for BTC/ETH/SOL, sweep EMA combos, send charts to Telegram."""

    connector: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    days: int = Field(default=30, description="Days of history to fetch")
    hold_bars: int = Field(
        default=5, description="Bars ahead to measure post-signal move"
    )


PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
TIMEFRAMES = ["1m", "3m", "15m"]
FAST_VALS = [5, 8, 13, 21]
SLOW_VALS = [21, 34, 55, 89]


# ── Data fetching ─────────────────────────────────────────────────────────────


async def _fetch_df(client, connector: str, pair: str, interval: str, days: int):
    result = None
    try:
        result = await client.market_data.get_candles_last_days(
            connector_name=connector,
            trading_pair=pair,
            days=days,
            interval=interval,
        )
    except Exception as e:
        logger.warning(f"Historical fetch failed for {pair}/{interval}: {e}")

    records = _extract_records(result)

    if not records:
        try:
            mins = {"1m": 1, "3m": 3, "15m": 15}.get(interval, 15)
            max_records = (days * 24 * 60) // mins
            result = await client.market_data.get_candles(
                connector_name=connector,
                trading_pair=pair,
                interval=interval,
                max_records=max_records,
            )
            records = _extract_records(result)
        except Exception as e:
            logger.error(f"Failed to fetch {pair}/{interval}: {e}")
            return None

    if not records:
        return None

    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Timestamp: >1e10 → milliseconds, else seconds
    if "timestamp" in df.columns:
        ts0 = float(df["timestamp"].iloc[0])
        unit = "ms" if ts0 > 1e10 else "s"
        df["datetime"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
    elif "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], utc=True)

    df = df.sort_values("datetime").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    return df


def _extract_records(result):
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("data", result.get("candles", []))
    return []


# ── Indicators ────────────────────────────────────────────────────────────────


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    up = high.diff()
    dn = -low.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    smooth = lambda s: s.ewm(alpha=1 / period, adjust=False).mean()
    atr_s = smooth(tr)
    dx = (
        100
        * (smooth(plus_dm) / atr_s - smooth(minus_dm) / atr_s).abs()
        / (smooth(plus_dm) / atr_s + smooth(minus_dm) / atr_s)
    ).replace([np.inf, -np.inf], np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


# ── EMA signal stats ──────────────────────────────────────────────────────────


def _ema_stats(df: pd.DataFrame, fast: int, slow: int, hold_bars: int) -> dict:
    ema_f = _ema(df["close"], fast).values
    ema_s = _ema(df["close"], slow).values
    signal = np.where(ema_f > ema_s, 1, -1)

    # Crossovers
    diff = np.diff(signal)
    n_cross = int(np.sum(diff != 0))

    # Time range
    if "datetime" in df.columns and len(df) > 1:
        delta_days = (
            df["datetime"].iloc[-1] - df["datetime"].iloc[0]
        ).total_seconds() / 86400
    else:
        delta_days = max(len(df) / 1440, 1)
    signals_per_day = n_cross / max(delta_days, 1)

    # Persistence (avg run length)
    runs, count = [], 1
    for i in range(1, len(signal)):
        if signal[i] == signal[i - 1]:
            count += 1
        else:
            runs.append(count)
            count = 1
    runs.append(count)
    avg_persistence = float(np.mean(runs)) if runs else 0.0

    # Post-signal move (directional return over hold_bars bars after crossover)
    close = df["close"].values
    moves = []
    for i in range(1, len(signal) - hold_bars):
        if diff[i - 1] != 0:
            direction = signal[i]
            future_ret = (close[i + hold_bars] - close[i]) / close[i]
            moves.append(direction * future_ret)
    avg_move_pct = float(np.mean(moves) * 100) if moves else 0.0

    return {
        "fast": fast,
        "slow": slow,
        "signals_per_day": round(signals_per_day, 2),
        "avg_persistence": round(avg_persistence, 1),
        "avg_move_pct": round(avg_move_pct, 4),
        "score": round(avg_persistence * avg_move_pct, 4),
    }


# ── Chart helpers ─────────────────────────────────────────────────────────────


def _to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(
        buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    buf.seek(0)
    return buf.read()


# ── Main ──────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = getattr(context, "_chat_id", None)
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available — configure a Hummingbot server first."

    await context.bot.send_message(
        chat_id=chat_id,
        text="📊 *EMA Research* — fetching 30d candles for BTC/ETH/SOL on 1m/3m/15m...",
        parse_mode="Markdown",
    )

    # ── Fetch all 9 datasets ──────────────────────────────────────────────────
    data = {}
    for pair in PAIRS:
        for tf in TIMEFRAMES:
            df = await _fetch_df(client, config.connector, pair, tf, config.days)
            if df is not None and len(df) >= 100:
                data[(pair, tf)] = df
                logger.info(f"  {pair}/{tf}: {len(df)} candles")

    if not data:
        return "❌ Failed to fetch candle data. Is the Hummingbot server running?"

    fetched = ", ".join(f"{p}/{t}({len(df)})" for (p, t), df in sorted(data.items()))
    logger.info(f"Fetched: {fetched}")

    # ── Step 2: Distribution & regime analysis (15m) ──────────────────────────
    dist_stats = {}
    for pair in PAIRS:
        key = (pair, "15m")
        if key not in data:
            continue
        df = data[key]
        ret = df["ret"].dropna()
        adx_s = _adx(df).dropna()
        rvol_ann = ret.rolling(96).std() * math.sqrt(252 * 96)
        autocorr = float(ret.autocorr(lag=1))

        dist_stats[pair] = {
            "n": len(df),
            "ret_mean": float(ret.mean() * 100),
            "ret_std": float(ret.std() * 100),
            "ret_skew": float(ret.skew()),
            "ret_kurt": float(ret.kurt()),
            "rvol_pct": float(rvol_ann.dropna().mean() * 100),
            "adx_mean": float(adx_s.mean()),
            "adx_pct_trending": float((adx_s > 25).mean() * 100),
            "autocorr_lag1": round(autocorr, 4),
            "regime": (
                "trend"
                if autocorr > 0.05
                else ("mean-rev" if autocorr < -0.05 else "random")
            ),
        }

    # ── Step 3: EMA sweep (15m) ───────────────────────────────────────────────
    sweep_results = {}
    for pair in PAIRS:
        key = (pair, "15m")
        if key not in data:
            continue
        df = data[key]
        results = []
        for fast in FAST_VALS:
            for slow in SLOW_VALS:
                if fast >= slow:
                    continue
                results.append(_ema_stats(df, fast, slow, config.hold_bars))
        sweep_results[pair] = sorted(results, key=lambda x: x["score"], reverse=True)

    # Top 3 across all pairs
    all_scored = []
    for pair, results in sweep_results.items():
        for r in results[:5]:
            all_scored.append({"pair": pair, **r})
    all_scored.sort(key=lambda x: x["score"], reverse=True)
    top3 = all_scored[:3]

    # ── Chart A: Returns distribution + rolling vol ───────────────────────────
    COLORS = {"BTC-USDT": "#f7931a", "ETH-USDT": "#627eea", "SOL-USDT": "#9945ff"}

    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        "Returns Distribution & Realized Volatility — 30d, 15m candles",
        fontsize=13,
        fontweight="bold",
        color="white",
    )

    for col_idx, pair in enumerate(PAIRS):
        key = (pair, "15m")
        if key not in data:
            continue
        df = data[key]
        ret = df["ret"].dropna() * 100
        color = COLORS[pair]

        # Row 0: histogram
        ax_h = axes[0, col_idx]
        ax_h.set_facecolor("#1e293b")
        ax_h.hist(ret, bins=100, color=color, alpha=0.75, density=True)
        ax_h.axvline(
            ret.mean(),
            color="white",
            linestyle="--",
            linewidth=1,
            label=f"μ={ret.mean():.4f}%",
        )
        s = dist_stats.get(pair, {})
        ax_h.set_title(
            f"{pair}\nskew={s.get('ret_skew', 0):.2f}  kurt={s.get('ret_kurt', 0):.2f}",
            fontsize=9,
            color="white",
        )
        ax_h.set_xlabel("Return %", color="white", fontsize=7)
        ax_h.tick_params(colors="white", labelsize=6)
        ax_h.legend(fontsize=7, facecolor="#1e293b", labelcolor="white")

        # Row 1: rolling vol
        ax_v = axes[1, col_idx]
        ax_v.set_facecolor("#1e293b")
        rvol = (ret.rolling(96).std() * math.sqrt(252 * 96)).dropna()
        dt_slice = df["datetime"].iloc[-len(rvol) :]
        ax_v.plot(dt_slice.values, rvol.values, color=color, linewidth=0.8)
        ax_v.set_title(
            f"{pair} Ann. RVol (96-bar)\nμ={rvol.mean():.1f}%",
            fontsize=9,
            color="white",
        )
        ax_v.set_ylabel("RVol %", color="white", fontsize=7)
        ax_v.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax_v.tick_params(colors="white", labelsize=6)
        ax_v.tick_params(axis="x", rotation=45)

    plt.tight_layout()
    chart_a = _to_bytes(fig)
    plt.close(fig)

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(chart_a),
        caption="📈 (A) Returns distribution + realized volatility — BTC / ETH / SOL 15m",
    )

    # ── Chart B: ADX distribution ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        "ADX Distribution — Trend Regime (30d, 15m)",
        fontsize=13,
        fontweight="bold",
        color="white",
    )

    for col_idx, pair in enumerate(PAIRS):
        key = (pair, "15m")
        if key not in data:
            continue
        df = data[key]
        adx_s = _adx(df).dropna()
        color = COLORS[pair]
        s = dist_stats.get(pair, {})

        ax = axes[col_idx]
        ax.set_facecolor("#1e293b")
        ax.hist(adx_s, bins=60, color=color, alpha=0.75)
        ax.axvline(25, color="#ef4444", linestyle="--", linewidth=1.2, label="ADX=25")
        ax.axvline(
            float(adx_s.median()),
            color="white",
            linestyle="-.",
            linewidth=1,
            label=f"median={adx_s.median():.1f}",
        )
        ax.set_title(
            f"{pair}\n{s.get('adx_pct_trending', 0):.0f}% time ADX>25",
            fontsize=9,
            color="white",
        )
        ax.legend(fontsize=7, facecolor="#1e293b", labelcolor="white")
        ax.set_xlabel("ADX", color="white", fontsize=7)
        ax.tick_params(colors="white", labelsize=6)

    plt.tight_layout()
    chart_b = _to_bytes(fig)
    plt.close(fig)

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(chart_b),
        caption="📊 (B) ADX distribution — % time in trending regime per pair",
    )

    # ── Chart C: Best EMA combo price chart (BTC-15m, last 500 bars) ─────────
    btc_key = ("BTC-USDT", "15m")
    if btc_key in data and sweep_results.get("BTC-USDT"):
        best_btc = sweep_results["BTC-USDT"][0]
        fp, sp = best_btc["fast"], best_btc["slow"]
        df_c = data[btc_key].tail(500).copy().reset_index(drop=True)
        df_c["ema_f"] = _ema(df_c["close"], fp)
        df_c["ema_s"] = _ema(df_c["close"], sp)
        df_c["sig"] = np.where(df_c["ema_f"] > df_c["ema_s"], 1, -1)
        df_c["cross"] = df_c["sig"].diff().ne(0)

        fig, ax = plt.subplots(figsize=(14, 5))
        fig.patch.set_facecolor("#0f172a")
        ax.set_facecolor("#1e293b")
        ax.plot(
            df_c["datetime"],
            df_c["close"],
            color="white",
            linewidth=0.6,
            label="Close",
            zorder=2,
        )
        ax.plot(
            df_c["datetime"],
            df_c["ema_f"],
            color="#3b82f6",
            linewidth=1.3,
            label=f"EMA{fp}",
            zorder=3,
        )
        ax.plot(
            df_c["datetime"],
            df_c["ema_s"],
            color="#f59e0b",
            linewidth=1.3,
            label=f"EMA{sp}",
            zorder=3,
        )

        longs = df_c[df_c["cross"] & (df_c["sig"] == 1)]
        shorts = df_c[df_c["cross"] & (df_c["sig"] == -1)]
        ax.scatter(
            longs["datetime"],
            longs["close"],
            marker="^",
            color="#22c55e",
            s=60,
            zorder=5,
            label="Long",
        )
        ax.scatter(
            shorts["datetime"],
            shorts["close"],
            marker="v",
            color="#ef4444",
            s=60,
            zorder=5,
            label="Short",
        )

        ax.set_title(
            f"BTC-USDT 15m | Best EMA {fp}/{sp} | score={best_btc['score']:.2f} | "
            f"persist={best_btc['avg_persistence']:.0f}bars | {best_btc['signals_per_day']:.1f} signals/day",
            fontsize=10,
            color="white",
        )
        ax.legend(fontsize=8, facecolor="#1e293b", labelcolor="white")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        ax.tick_params(colors="white", labelsize=6)
        ax.tick_params(axis="x", rotation=45)
        ax.set_ylabel("Price (USDT)", color="white", fontsize=8)
        ax.yaxis.label.set_color("white")

        plt.tight_layout()
        chart_c = _to_bytes(fig)
        plt.close(fig)

        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(chart_c),
            caption=f"📉 (C) BTC-USDT 15m — Best EMA {fp}/{sp} | ▲=Long ▼=Short | Last 500 bars",
        )

    # ── Chart D: Normalized distance distribution ─────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        "Normalized Distance: (close − EMA_slow) / ATR(14) — 15m",
        fontsize=11,
        fontweight="bold",
        color="white",
    )

    for col_idx, pair in enumerate(PAIRS):
        key = (pair, "15m")
        if key not in data or pair not in sweep_results or not sweep_results[pair]:
            continue
        df = data[key]
        best = sweep_results[pair][0]
        sp = best["slow"]
        color = COLORS[pair]

        ema_s = _ema(df["close"], sp)
        atr_s = _atr(df, 14)
        norm_dist = ((df["close"] - ema_s) / atr_s).dropna()

        ax = axes[col_idx]
        ax.set_facecolor("#1e293b")
        ax.hist(norm_dist, bins=80, color=color, alpha=0.75)
        ax.axvline(1.0, color="#22c55e", linestyle="--", linewidth=1.3, label="+1 ATR")
        ax.axvline(-1.0, color="#ef4444", linestyle="--", linewidth=1.3, label="-1 ATR")
        ax.axvline(0, color="white", linestyle="-", linewidth=0.5)
        ax.set_title(
            f"{pair} | EMA{sp}\nμ={norm_dist.mean():.2f}  σ={norm_dist.std():.2f}",
            fontsize=9,
            color="white",
        )
        ax.legend(fontsize=7, facecolor="#1e293b", labelcolor="white")
        ax.set_xlabel("(close − EMA) / ATR", color="white", fontsize=7)
        ax.tick_params(colors="white", labelsize=6)

    plt.tight_layout()
    chart_d = _to_bytes(fig)
    plt.close(fig)

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(chart_d),
        caption="📐 (D) Normalized distance distribution — EMA entry threshold visualized",
    )

    # ── Text summary ──────────────────────────────────────────────────────────
    lines = ["*📊 EMA Research Summary — BTC/ETH/SOL (30d, 15m)*\n"]

    lines.append("*Distribution Stats (15m):*")
    for pair, s in dist_stats.items():
        lines.append(
            f"  `{pair}` ret μ={s['ret_mean']:.4f}% σ={s['ret_std']:.4f}% | "
            f"skew={s['ret_skew']:.2f} kurt={s['ret_kurt']:.2f} | "
            f"RVol={s['rvol_pct']:.0f}% ann | ADX μ={s['adx_mean']:.1f} "
            f"({s['adx_pct_trending']:.0f}%>25) | autocorr={s['autocorr_lag1']:.3f} [{s['regime']}]"
        )

    lines.append("\n*Top 3 EMA Combos (persistence × avg move):*")
    for i, c in enumerate(top3, 1):
        lines.append(
            f"  #{i} `{c['pair']}` EMA{c['fast']}/{c['slow']} — "
            f"score={c['score']:.3f} | persist={c['avg_persistence']:.0f}bars | "
            f"{c['signals_per_day']:.1f} signals/day | avg move={c['avg_move_pct']:.4f}%"
        )

    lines.append("\n*Recommendation:*")
    if top3:
        best = top3[0]
        lines.append(
            f"  Best signal: `{best['pair']}` 15m with EMA{best['fast']}/{best['slow']}. "
            f"High persistence ({best['avg_persistence']:.0f} bars avg) "
            f"with clean directional moves after crossover. "
            f"→ Ready for controller creation."
        )

    summary = "\n".join(lines)

    # Send summary as Telegram message
    await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")

    # Report
    from condor.reports import ReportBuilder

    rb = ReportBuilder("EMA Research — BTC/ETH/SOL 15m")
    rb.source("routine", "ema_research_charts")
    rb.manual_order()
    rb.markdown("## Distribution Stats\n")
    dist_rows = []
    for pair, s in dist_stats.items():
        dist_rows.append(
            {
                "pair": pair,
                "ret_mean%": round(s["ret_mean"], 4),
                "ret_std%": round(s["ret_std"], 4),
                "skew": round(s["ret_skew"], 2),
                "kurt": round(s["ret_kurt"], 2),
                "rvol%ann": round(s["rvol_pct"], 1),
                "adx_mean": round(s["adx_mean"], 1),
                "adx%>25": round(s["adx_pct_trending"], 1),
                "autocorr": s["autocorr_lag1"],
                "regime": s["regime"],
            }
        )
    rb.table(dist_rows, list(dist_rows[0].keys()) if dist_rows else None)
    rb.markdown("## Top EMA Combos\n")
    rb.table(
        top3,
        [
            "pair",
            "fast",
            "slow",
            "score",
            "avg_persistence",
            "signals_per_day",
            "avg_move_pct",
        ],
    )
    rb.markdown(f"## Recommendation\n\n{lines[-1]}")
    rb.tags(["ema", "research", "trend", "btc", "eth", "sol"])
    await rb.save()

    return summary
