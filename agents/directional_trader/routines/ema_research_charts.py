"""EMA trend-following research: configurable pair/timeframe sweep with win rate, Sharpe, ADX filter, score heatmap, and current signal state."""

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


COLORS = {"BTC-USDT": "#f7931a", "ETH-USDT": "#627eea", "SOL-USDT": "#9945ff"}
DEFAULT_COLOR = "#64748b"

# bars per calendar day for each supported timeframe
BARS_PER_DAY = {
    "1m": 1440,
    "3m": 480,
    "5m": 288,
    "15m": 96,
    "30m": 48,
    "1h": 24,
    "4h": 6,
    "1d": 1,
}


class Config(BaseModel):
    """EMA trend research — sweep EMA combos, score by Sharpe × persistence, show current signal state."""

    connector: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    pairs: str = Field(
        default="BTC-USDT,ETH-USDT,SOL-USDT",
        description="Comma-separated trading pairs",
    )
    timeframe: str = Field(
        default="15m", description="Candle interval (1m/3m/15m/1h/4h)"
    )
    days: int = Field(default=30, description="Days of history to fetch")
    hold_bars: int = Field(
        default=5, description="Bars ahead to measure post-signal move"
    )
    fast_min: int = Field(default=5, description="Min fast EMA period")
    fast_max: int = Field(default=21, description="Max fast EMA period")
    slow_min: int = Field(default=21, description="Min slow EMA period")
    slow_max: int = Field(default=89, description="Max slow EMA period")
    adx_filter: int = Field(
        default=0, description="Min ADX for a signal to count (0=disabled)"
    )
    top_n: int = Field(default=5, description="Top N combos to show per pair")


# ── Data fetching ─────────────────────────────────────────────────────────────


def _extract_records(result):
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("data", result.get("candles", []))
    return []


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
            bpd = BARS_PER_DAY.get(interval, 96)
            max_records = days * bpd
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

    if "timestamp" in df.columns:
        ts0 = float(df["timestamp"].iloc[0])
        unit = "ms" if ts0 > 1e10 else "s"
        df["datetime"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
    elif "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], utc=True)

    df = df.sort_values("datetime").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    return df


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

    def smooth(s):
        return s.ewm(alpha=1 / period, adjust=False).mean()

    atr_s = smooth(tr)
    dx = (
        100
        * (smooth(plus_dm) / atr_s - smooth(minus_dm) / atr_s).abs()
        / (smooth(plus_dm) / atr_s + smooth(minus_dm) / atr_s)
    ).replace([np.inf, -np.inf], np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


# ── EMA signal stats ──────────────────────────────────────────────────────────


def _ema_stats(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    hold_bars: int,
    adx_threshold: int = 0,
) -> dict:
    ema_f = _ema(df["close"], fast).values
    ema_s = _ema(df["close"], slow).values
    signal = np.where(ema_f > ema_s, 1, -1)

    # All crossover indices (diff prepend keeps length == len(signal))
    raw_diff = np.diff(signal, prepend=signal[0])
    all_cross_idx = np.where(raw_diff != 0)[0]
    # Remove index 0 (always zero from prepend trick)
    all_cross_idx = all_cross_idx[all_cross_idx > 0]

    # Time range in days
    if "datetime" in df.columns and len(df) > 1:
        delta_days = (
            df["datetime"].iloc[-1] - df["datetime"].iloc[0]
        ).total_seconds() / 86400
    else:
        delta_days = max(len(df) / 1440, 1)

    # ADX mask — filter which crossovers are valid signals
    if adx_threshold > 0:
        adx_vals = _adx(df).fillna(0).values
        adx_mask = adx_vals >= adx_threshold
    else:
        adx_mask = np.ones(len(signal), dtype=bool)

    valid_cross_idx = np.array([i for i in all_cross_idx if adx_mask[i]])

    # signals_per_day counts only valid (filtered) crossovers
    signals_per_day = len(valid_cross_idx) / max(delta_days, 1)

    # Persistence: raw avg run length — how sticky the EMA signal is
    runs, count = [], 1
    for i in range(1, len(signal)):
        if signal[i] == signal[i - 1]:
            count += 1
        else:
            runs.append(count)
            count = 1
    runs.append(count)
    avg_persistence = float(np.mean(runs)) if runs else 0.0

    # Post-signal moves on valid crossovers
    close = df["close"].values
    moves = []
    for i in valid_cross_idx:
        if i + hold_bars < len(close):
            direction = int(signal[i])
            future_ret = (close[i + hold_bars] - close[i]) / close[i]
            moves.append(direction * future_ret)

    n_signals = len(moves)
    if n_signals > 1:
        arr = np.array(moves)
        avg_move_pct = float(arr.mean() * 100)
        win_rate = float((arr > 0).mean() * 100)
        sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0
    elif n_signals == 1:
        avg_move_pct = float(moves[0] * 100)
        win_rate = 100.0 if moves[0] > 0 else 0.0
        sharpe = 0.0
    else:
        avg_move_pct = 0.0
        win_rate = 0.0
        sharpe = 0.0

    # Score: Sharpe × persistence (rewards quality AND duration; penalizes chop)
    score = round(max(sharpe, 0.0) * avg_persistence, 4)
    current_signal = int(signal[-1])

    return {
        "fast": fast,
        "slow": slow,
        "n_signals": n_signals,
        "signals_per_day": round(signals_per_day, 2),
        "avg_persistence": round(avg_persistence, 1),
        "win_rate": round(win_rate, 1),
        "avg_move_pct": round(avg_move_pct, 4),
        "sharpe": round(sharpe, 3),
        "score": score,
        "current_signal": current_signal,
    }


# ── Chart helpers ─────────────────────────────────────────────────────────────


def _to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(
        buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    buf.seek(0)
    return buf.read()


def _style_ax(ax):
    ax.set_facecolor("#1e293b")
    ax.tick_params(colors="white", labelsize=6)
    for spine in ax.spines.values():
        spine.set_edgecolor("#334155")


# ── Main ──────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = getattr(context, "_chat_id", None)
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available — configure a Hummingbot server first."

    pairs = [p.strip() for p in config.pairs.split(",") if p.strip()]
    if not pairs:
        return "No pairs configured."

    # Build EMA grid: ~5 evenly spaced points across each range
    def _grid(lo, hi, n=5):
        if lo >= hi:
            return [lo]
        step = max(1, (hi - lo) // max(n - 1, 1))
        vals = list(range(lo, hi + 1, step))
        if vals[-1] != hi:
            vals.append(hi)
        return sorted(set(vals))

    fast_vals = _grid(config.fast_min, config.fast_max)
    slow_vals = _grid(config.slow_min, config.slow_max)
    bars_per_day = BARS_PER_DAY.get(config.timeframe, 96)
    min_bars = max(config.slow_max + config.hold_bars + 20, 100)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📊 *EMA Research* — fetching {config.days}d of {config.timeframe} candles "
            f"for {', '.join(pairs)}..."
        ),
        parse_mode="Markdown",
    )

    # ── Fetch ─────────────────────────────────────────────────────────────────
    data = {}
    for pair in pairs:
        df = await _fetch_df(
            client, config.connector, pair, config.timeframe, config.days
        )
        if df is not None and len(df) >= min_bars:
            data[pair] = df
            logger.info(f"  {pair}/{config.timeframe}: {len(df)} candles")
        elif df is not None:
            logger.warning(
                f"  {pair}/{config.timeframe}: only {len(df)} candles (need {min_bars}), skipping"
            )

    if not data:
        return "❌ Failed to fetch candle data. Is the Hummingbot server running?"

    active_pairs = [p for p in pairs if p in data]
    n_pairs = len(active_pairs)

    # ── Regime analysis ───────────────────────────────────────────────────────
    dist_stats = {}
    for pair in active_pairs:
        df = data[pair]
        ret = df["ret"].dropna()
        adx_s = _adx(df).dropna()
        rvol_window = bars_per_day * 5  # ~5 days rolling
        rvol_ann = ret.rolling(rvol_window).std() * math.sqrt(252 * bars_per_day)
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

    # ── EMA sweep ─────────────────────────────────────────────────────────────
    sweep_results = {}
    for pair in active_pairs:
        df = data[pair]
        results = []
        for fast in fast_vals:
            for slow in slow_vals:
                if fast >= slow:
                    continue
                results.append(
                    _ema_stats(df, fast, slow, config.hold_bars, config.adx_filter)
                )
        sweep_results[pair] = sorted(results, key=lambda x: x["score"], reverse=True)

    # Global top 3 and best per pair
    all_scored = []
    for pair in active_pairs:
        for r in sweep_results[pair][: config.top_n]:
            all_scored.append({"pair": pair, **r})
    all_scored.sort(key=lambda x: x["score"], reverse=True)
    top3 = all_scored[:3]

    best_per_pair = {
        pair: sweep_results[pair][0] for pair in active_pairs if sweep_results.get(pair)
    }
    current_signals = {
        pair: {
            "combo": f"EMA{b['fast']}/{b['slow']}",
            "signal": "🟢 LONG" if b["current_signal"] == 1 else "🔴 SHORT",
        }
        for pair, b in best_per_pair.items()
    }

    # ── Chart A: Distribution + rolling vol ───────────────────────────────────
    fig, axes = plt.subplots(2, n_pairs, figsize=(5 * n_pairs, 8), squeeze=False)
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        f"Returns Distribution & Realized Volatility — {config.days}d, {config.timeframe}",
        fontsize=13,
        fontweight="bold",
        color="white",
    )
    for ci, pair in enumerate(active_pairs):
        df = data[pair]
        ret = df["ret"].dropna() * 100
        color = COLORS.get(pair, DEFAULT_COLOR)
        s = dist_stats[pair]

        ax_h = axes[0, ci]
        _style_ax(ax_h)
        ax_h.hist(ret, bins=100, color=color, alpha=0.75, density=True)
        ax_h.axvline(
            ret.mean(),
            color="white",
            linestyle="--",
            linewidth=1,
            label=f"μ={ret.mean():.4f}%",
        )
        ax_h.set_title(
            f"{pair}\nskew={s['ret_skew']:.2f}  kurt={s['ret_kurt']:.2f}",
            fontsize=9,
            color="white",
        )
        ax_h.set_xlabel("Return %", color="white", fontsize=7)
        ax_h.legend(fontsize=7, facecolor="#1e293b", labelcolor="white")

        ax_v = axes[1, ci]
        _style_ax(ax_v)
        rvol = (
            ret.rolling(bars_per_day * 5).std() * math.sqrt(252 * bars_per_day)
        ).dropna()
        dt_slice = df["datetime"].iloc[-len(rvol) :]
        ax_v.plot(dt_slice.values, rvol.values, color=color, linewidth=0.8)
        ax_v.set_title(
            f"{pair} Ann. RVol\nμ={rvol.mean():.1f}%", fontsize=9, color="white"
        )
        ax_v.set_ylabel("RVol %", color="white", fontsize=7)
        ax_v.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax_v.tick_params(axis="x", rotation=45)

    plt.tight_layout()
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(_to_bytes(fig)),
        caption=f"📈 (A) Returns distribution + realized volatility — {config.timeframe}",
    )
    plt.close(fig)

    # ── Chart B: ADX distribution ──────────────────────────────────────────────
    fig, axes = plt.subplots(1, n_pairs, figsize=(5 * n_pairs, 4), squeeze=False)
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        f"ADX Distribution — Trend Regime ({config.days}d, {config.timeframe})",
        fontsize=13,
        fontweight="bold",
        color="white",
    )
    for ci, pair in enumerate(active_pairs):
        df = data[pair]
        adx_s = _adx(df).dropna()
        color = COLORS.get(pair, DEFAULT_COLOR)
        s = dist_stats[pair]

        ax = axes[0, ci]
        _style_ax(ax)
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
            f"{pair}\n{s['adx_pct_trending']:.0f}% time ADX>25",
            fontsize=9,
            color="white",
        )
        ax.legend(fontsize=7, facecolor="#1e293b", labelcolor="white")
        ax.set_xlabel("ADX", color="white", fontsize=7)

    plt.tight_layout()
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(_to_bytes(fig)),
        caption="📊 (B) ADX distribution — % time in trending regime per pair",
    )
    plt.close(fig)

    # ── Chart C: Best EMA combo per pair — all pairs in one figure ────────────
    fig, axes = plt.subplots(n_pairs, 1, figsize=(14, 5 * n_pairs), squeeze=False)
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        f"Best EMA Combo per Pair — Last 500 bars ({config.timeframe})",
        fontsize=12,
        fontweight="bold",
        color="white",
    )
    for ri, pair in enumerate(active_pairs):
        if pair not in best_per_pair:
            continue
        best = best_per_pair[pair]
        fp, sp = best["fast"], best["slow"]
        df_c = data[pair].tail(500).copy().reset_index(drop=True)
        df_c["ema_f"] = _ema(df_c["close"], fp)
        df_c["ema_s"] = _ema(df_c["close"], sp)
        df_c["sig"] = np.where(df_c["ema_f"] > df_c["ema_s"], 1, -1)
        df_c["cross"] = df_c["sig"].diff().ne(0)

        ax = axes[ri, 0]
        _style_ax(ax)
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
            s=50,
            zorder=5,
            label="Long",
        )
        ax.scatter(
            shorts["datetime"],
            shorts["close"],
            marker="v",
            color="#ef4444",
            s=50,
            zorder=5,
            label="Short",
        )

        sig_label = "▲ LONG" if best["current_signal"] == 1 else "▼ SHORT"
        ax.set_title(
            f"{pair} {config.timeframe} | EMA{fp}/{sp} | score={best['score']:.3f} | "
            f"win={best['win_rate']:.0f}% | sharpe={best['sharpe']:.2f} | "
            f"persist={best['avg_persistence']:.0f}b | NOW: {sig_label}",
            fontsize=9,
            color="white",
        )
        ax.legend(fontsize=8, facecolor="#1e293b", labelcolor="white")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        ax.tick_params(axis="x", rotation=45)
        ax.set_ylabel("Price", color="white", fontsize=8)

    plt.tight_layout()
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(_to_bytes(fig)),
        caption="📉 (C) Best EMA combo per pair | ▲=Long ▼=Short | Last 500 bars",
    )
    plt.close(fig)

    # ── Chart D: Score heatmap for the first pair ──────────────────────────────
    pair_hm = active_pairs[0]
    results_hm = sweep_results.get(pair_hm, [])
    if len(results_hm) > 1:
        hm_fast = sorted({r["fast"] for r in results_hm})
        hm_slow = sorted({r["slow"] for r in results_hm})
        fi = {v: i for i, v in enumerate(hm_fast)}
        si = {v: i for i, v in enumerate(hm_slow)}
        mat = np.full((len(hm_fast), len(hm_slow)), np.nan)
        for r in results_hm:
            mat[fi[r["fast"]], si[r["slow"]]] = r["score"]

        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor("#0f172a")
        _style_ax(ax)
        im = ax.imshow(mat, aspect="auto", cmap="RdYlGn", interpolation="nearest")
        ax.set_xticks(range(len(hm_slow)))
        ax.set_xticklabels([str(s) for s in hm_slow], fontsize=8, color="white")
        ax.set_yticks(range(len(hm_fast)))
        ax.set_yticklabels([str(f) for f in hm_fast], fontsize=8, color="white")
        ax.set_xlabel("Slow EMA", color="white", fontsize=9)
        ax.set_ylabel("Fast EMA", color="white", fontsize=9)
        ax.set_title(
            f"{pair_hm} — EMA Score Heatmap (Sharpe × Persistence)",
            fontsize=11,
            color="white",
        )
        cbar = plt.colorbar(im, ax=ax)
        cbar.ax.tick_params(colors="white")

        best_hm = best_per_pair.get(pair_hm)
        if best_hm:
            bfi, bsi = fi.get(best_hm["fast"]), si.get(best_hm["slow"])
            if bfi is not None and bsi is not None:
                ax.add_patch(
                    plt.Rectangle(
                        (bsi - 0.5, bfi - 0.5),
                        1,
                        1,
                        fill=False,
                        edgecolor="white",
                        linewidth=2,
                    )
                )
                ax.text(
                    bsi, bfi, "★", ha="center", va="center", color="white", fontsize=14
                )

        plt.tight_layout()
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(_to_bytes(fig)),
            caption=f"🗺️ (D) {pair_hm} — EMA score heatmap | ★ = best combo",
        )
        plt.close(fig)

    # ── Chart E: Normalized distance distribution ──────────────────────────────
    fig, axes = plt.subplots(1, n_pairs, figsize=(5 * n_pairs, 4), squeeze=False)
    fig.patch.set_facecolor("#0f172a")
    fig.suptitle(
        "Normalized Distance: (close − EMA_slow) / ATR(14)",
        fontsize=11,
        fontweight="bold",
        color="white",
    )
    for ci, pair in enumerate(active_pairs):
        if pair not in best_per_pair:
            continue
        df = data[pair]
        sp = best_per_pair[pair]["slow"]
        color = COLORS.get(pair, DEFAULT_COLOR)

        ema_s = _ema(df["close"], sp)
        atr_s = _atr(df, 14)
        norm_dist = ((df["close"] - ema_s) / atr_s).dropna()

        ax = axes[0, ci]
        _style_ax(ax)
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

    plt.tight_layout()
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(_to_bytes(fig)),
        caption="📐 (E) Normalized distance — EMA entry threshold visualized",
    )
    plt.close(fig)

    # ── Text summary ──────────────────────────────────────────────────────────
    adx_note = f" (ADX≥{config.adx_filter} filter)" if config.adx_filter > 0 else ""
    lines = [f"*📊 EMA Research — {config.timeframe} | {config.days}d{adx_note}*\n"]

    lines.append("*Current Signals (best combo per pair):*")
    for pair in active_pairs:
        if pair in current_signals:
            cs = current_signals[pair]
            lines.append(f"  `{pair}` {cs['combo']} → {cs['signal']}")

    lines.append("\n*Regime Stats:*")
    for pair in active_pairs:
        s = dist_stats[pair]
        lines.append(
            f"  `{pair}` ADX μ={s['adx_mean']:.1f} ({s['adx_pct_trending']:.0f}%>25) | "
            f"autocorr={s['autocorr_lag1']:.3f} [{s['regime']}] | RVol={s['rvol_pct']:.0f}%ann"
        )

    lines.append(f"\n*Top {config.top_n} EMA Combos per pair (Sharpe × Persistence):*")
    for pair in active_pairs:
        lines.append(f"\n  *{pair}:*")
        for r in sweep_results[pair][: config.top_n]:
            sig_icon = "▲" if r["current_signal"] == 1 else "▼"
            lines.append(
                f"    EMA{r['fast']}/{r['slow']} | score={r['score']:.3f} | "
                f"win={r['win_rate']:.0f}% | sharpe={r['sharpe']:.2f} | "
                f"persist={r['avg_persistence']:.0f}b | {r['signals_per_day']:.1f}/day | {sig_icon}"
            )

    lines.append("\n*Global Top 3:*")
    for i, c in enumerate(top3, 1):
        sig_icon = "▲" if c["current_signal"] == 1 else "▼"
        lines.append(
            f"  #{i} `{c['pair']}` EMA{c['fast']}/{c['slow']} — "
            f"score={c['score']:.3f} | win={c['win_rate']:.0f}% | sharpe={c['sharpe']:.2f} | "
            f"persist={c['avg_persistence']:.0f}b | {c['signals_per_day']:.1f}/day | {sig_icon}"
        )

    if top3:
        best = top3[0]
        sig_icon = "▲ LONG" if best["current_signal"] == 1 else "▼ SHORT"
        lines.append("\n*Recommendation:*")
        lines.append(
            f"  `{best['pair']}` {config.timeframe} EMA{best['fast']}/{best['slow']} | "
            f"win={best['win_rate']:.0f}% | sharpe={best['sharpe']:.2f} | "
            f"persist={best['avg_persistence']:.0f}b | NOW: {sig_icon}"
        )

    summary = "\n".join(lines)
    await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")

    # ── Report ────────────────────────────────────────────────────────────────
    from condor.reports import ReportBuilder

    rb = ReportBuilder(f"EMA Research — {config.timeframe} | {config.days}d")
    rb.source("routine", "ema_research_charts")
    rb.manual_order()
    rb.markdown("## Regime Stats\n")
    dist_rows = [
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
        for pair, s in dist_stats.items()
    ]
    if dist_rows:
        rb.table(dist_rows, list(dist_rows[0].keys()))
    rb.markdown("## Top EMA Combos\n")
    rb.table(
        top3,
        [
            "pair",
            "fast",
            "slow",
            "score",
            "win_rate",
            "sharpe",
            "avg_persistence",
            "signals_per_day",
            "current_signal",
        ],
    )
    if top3:
        rb.markdown(f"## Recommendation\n\n{lines[-1]}")
    rb.tags(
        ["ema", "research", "trend"] + [p.split("-")[0].lower() for p in active_pairs]
    )
    await rb.save()

    return summary
