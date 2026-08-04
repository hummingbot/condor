import asyncio
import logging

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Cross-timeframe mean reversion research: return stats, RSI, Bollinger, Z-score, Hurst, autocorrelation."""

    trading_pair: str = Field(default="SOL-USDT", description="Trading pair to analyze")
    connector: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    timeframes: str = Field(
        default="1m,5m,15m,1h,4h,1d", description="Comma-separated timeframes"
    )
    samples: int = Field(default=1500, description="Candles per timeframe (max)")


def _compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - 100 / (1 + rs)
    return rsi.values


def _compute_bb(closes: np.ndarray, period: int = 20, std_dev: float = 2.0):
    s = pd.Series(closes)
    mid = s.rolling(period).mean()
    std = s.rolling(period).std()
    upper = (mid + std_dev * std).values
    lower = (mid - std_dev * std).values
    return upper, mid.values, lower


def _compute_zscore(closes: np.ndarray, period: int = 30) -> np.ndarray:
    s = pd.Series(closes)
    mean = s.rolling(period).mean()
    std = s.rolling(period).std()
    z = (s - mean) / (std + 1e-10)
    return z.fillna(0).values


def _compute_hurst(series: np.ndarray, max_lag: int = 50) -> float:
    """R/S Hurst exponent. H<0.5=MR, H=0.5=random, H>0.5=trending."""
    if len(series) < 100:
        return 0.5
    lags = list(range(10, min(max_lag, len(series) // 4)))
    rs_vals = []
    for lag in lags:
        segments = len(series) // lag
        if segments < 4:
            continue
        rs_list = []
        for i in range(segments):
            seg = series[i * lag : (i + 1) * lag]
            if len(seg) < 2:
                continue
            mean_seg = np.mean(seg)
            dev = np.cumsum(seg - mean_seg)
            r = np.max(dev) - np.min(dev)
            s = np.std(seg, ddof=1)
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            rs_vals.append((lag, float(np.mean(rs_list))))
    if len(rs_vals) < 3:
        return 0.5
    lags_arr = np.log([x[0] for x in rs_vals])
    rs_arr = np.log([x[1] for x in rs_vals])
    try:
        poly = np.polyfit(lags_arr, rs_arr, 1)
        return float(np.clip(poly[0], 0.0, 1.0))
    except Exception:
        return 0.5


def _autocorr_lag1(returns: np.ndarray) -> float:
    if len(returns) < 10:
        return 0.0
    return float(pd.Series(returns).dropna().autocorr(lag=1))


def _analyze_timeframe(tf: str, closes: np.ndarray, returns: np.ndarray) -> dict:
    # Return distribution
    ret_mean = float(np.mean(returns)) * 100
    ret_std = float(np.std(returns)) * 100
    try:
        from scipy import stats as spstats

        ret_skew = float(spstats.skew(returns))
        ret_kurt = float(spstats.kurtosis(returns))  # excess kurtosis
    except ImportError:
        mu, sig = np.mean(returns), np.std(returns)
        if sig > 0:
            norm = (returns - mu) / sig
            ret_skew = float(np.mean(norm**3))
            ret_kurt = float(np.mean(norm**4) - 3)
        else:
            ret_skew, ret_kurt = 0.0, 0.0

    # RSI(14)
    rsi = _compute_rsi(closes, 14)
    valid_rsi = rsi[~np.isnan(rsi)]
    rsi_os_freq = float(np.mean(valid_rsi < 30)) * 100
    rsi_ob_freq = float(np.mean(valid_rsi > 70)) * 100
    rsi_signal_freq = rsi_os_freq + rsi_ob_freq
    rsi_mean = float(np.nanmean(valid_rsi))
    rsi_std = float(np.nanstd(valid_rsi))

    # Bollinger Bands(20,2) — touch + 3-bar reversion
    upper, mid, lower = _compute_bb(closes, 20, 2.0)
    bb_touches = 0
    bb_reversions = 0
    for i in range(20, len(closes) - 3):
        is_lower_touch = bool(closes[i] < lower[i]) if not np.isnan(lower[i]) else False
        is_upper_touch = bool(closes[i] > upper[i]) if not np.isnan(upper[i]) else False
        if is_lower_touch or is_upper_touch:
            bb_touches += 1
            for j in range(i + 1, min(i + 4, len(closes))):
                lj = lower[j] if not np.isnan(lower[j]) else np.inf
                uj = upper[j] if not np.isnan(upper[j]) else -np.inf
                if is_lower_touch and closes[j] >= lj:
                    bb_reversions += 1
                    break
                elif is_upper_touch and closes[j] <= uj:
                    bb_reversions += 1
                    break
    bb_touch_rate = (bb_touches / max(1, len(closes) - 20)) * 100
    bb_reversion_rate = (bb_reversions / bb_touches * 100) if bb_touches > 0 else 0.0

    # Z-score(30)
    zscore = _compute_zscore(closes, 30)
    valid_z = zscore[30:]
    z_mean = float(np.mean(valid_z))
    z_std = float(np.std(valid_z))
    z_tail_freq = float(np.mean(np.abs(valid_z) > 2)) * 100
    z_extreme_freq = float(np.mean(np.abs(valid_z) > 3)) * 100

    # Autocorrelation lag-1 on returns
    autocorr = _autocorr_lag1(returns)

    # Hurst exponent on close prices
    hurst = _compute_hurst(closes)

    # MR composite score (0–100, higher = stronger mean reversion)
    hurst_score = float(np.clip((0.5 - hurst) / 0.2 * 100, 0, 100))
    autocorr_score = (-autocorr + 1) / 2 * 100  # -1→100, 0→50, +1→0
    bb_score = bb_reversion_rate  # 0–100
    z_score = float(np.clip(z_tail_freq * 5, 0, 100))  # 20%+ tails → 100
    kurt_score = float(np.clip(ret_kurt * 10 + 50, 0, 100))  # excess kurt>0 = fat tails

    mr_score = (
        hurst_score * 0.30
        + autocorr_score * 0.25
        + bb_score * 0.25
        + z_score * 0.10
        + kurt_score * 0.10
    )

    return {
        "tf": tf,
        "n_candles": len(closes),
        "ret_mean_pct": ret_mean,
        "ret_std_pct": ret_std,
        "ret_skew": ret_skew,
        "ret_kurt": ret_kurt,
        "rsi_mean": rsi_mean,
        "rsi_std": rsi_std,
        "rsi_os_freq": rsi_os_freq,
        "rsi_ob_freq": rsi_ob_freq,
        "rsi_signal_freq": rsi_signal_freq,
        "bb_touch_rate": bb_touch_rate,
        "bb_reversion_rate": bb_reversion_rate,
        "z_mean": z_mean,
        "z_std": z_std,
        "z_tail_freq": z_tail_freq,
        "z_extreme_freq": z_extreme_freq,
        "autocorr_lag1": autocorr,
        "hurst": hurst,
        "mr_score": mr_score,
    }


# Canonical timeframe ordering for regime-gate selection
_TF_ORDER = [
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
]


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    timeframes = [tf.strip() for tf in config.timeframes.split(",") if tf.strip()]
    sem = asyncio.Semaphore(4)

    async def fetch_tf(tf: str):
        async with sem:
            try:
                result = await client.market_data.get_candles(
                    config.connector,
                    config.trading_pair,
                    tf,
                    max_records=config.samples,
                )
                records = (
                    result
                    if isinstance(result, list)
                    else result.get("data", result.get("candles", []))
                )
                return tf, records
            except Exception as e:
                logger.warning(f"Failed to fetch {tf}: {e}")
                return tf, []

    raw = await asyncio.gather(*[fetch_tf(tf) for tf in timeframes])

    analyses = []
    for tf, records in raw:
        if not records or len(records) < 60:
            logger.warning(f"Skipping {tf}: only {len(records)} candles")
            continue
        try:
            closes = np.array([float(r["close"]) for r in records], dtype=float)
            returns = np.diff(closes) / (closes[:-1] + 1e-10)
            analyses.append(_analyze_timeframe(tf, closes, returns))
        except Exception as e:
            logger.warning(f"Analysis failed for {tf}: {e}")

    if not analyses:
        return f"No data available for {config.trading_pair} on {config.connector}"

    ranked = sorted(analyses, key=lambda x: x["mr_score"], reverse=True)
    best = ranked[0]
    best_tf = best["tf"]

    # Regime gate: first slower TF in canonical order
    regime_gate_tf = None
    regime_gate_a = None
    try:
        best_idx = _TF_ORDER.index(best_tf)
        candidates = [
            a
            for a in analyses
            if a["tf"] in _TF_ORDER and _TF_ORDER.index(a["tf"]) > best_idx
        ]
        if candidates:
            regime_gate_a = max(candidates, key=lambda x: x["z_tail_freq"])
            regime_gate_tf = regime_gate_a["tf"]
    except (ValueError, Exception):
        pass

    try:
        from condor.reports import ReportBuilder
        from routines.base import RoutineResult

        builder = ReportBuilder(f"Mean Reversion Research — {config.trading_pair}")
        builder.source("routine", "mean_reversion_research").tags(
            ["analysis", "mean-reversion", config.trading_pair, config.connector]
        )

        # ── Summary KPIs ───────────────────────────────────────────────────────
        builder.section(
            "SUMMARY",
            f"{config.trading_pair} · {config.connector} · {len(analyses)} timeframes",
        )
        builder.kpi("Pair", config.trading_pair)
        builder.kpi("Connector", config.connector)
        builder.kpi("Best MR Timeframe", best_tf)
        builder.kpi("Best MR Score", f"{best['mr_score']:.1f} / 100")
        builder.kpi("Regime Gate TF", regime_gate_tf or "N/A")
        builder.kpi("Timeframes Analyzed", str(len(analyses)))

        # ── MR Suitability Ranking ─────────────────────────────────────────────
        builder.section(
            "MR SUITABILITY RANKING",
            "Composite score — Hurst 30% · AutoCorr 25% · BB-Rev 25% · Z-Tail 10% · Kurtosis 10%",
        )
        ranking_rows = []
        for i, a in enumerate(ranked):
            if a["hurst"] < 0.4:
                regime = "Strong MR"
            elif a["hurst"] < 0.5:
                regime = "Weak MR"
            elif a["hurst"] < 0.6:
                regime = "Random Walk"
            else:
                regime = "Trending"
            ranking_rows.append(
                {
                    "Rank": i + 1,
                    "TF": a["tf"],
                    "MR Score": f"{a['mr_score']:.1f}",
                    "Hurst": f"{a['hurst']:.3f}",
                    "Regime": regime,
                    "AutoCorr L1": f"{a['autocorr_lag1']:.3f}",
                    "BB Rev%": f"{a['bb_reversion_rate']:.1f}%",
                    "Z-Tail%": f"{a['z_tail_freq']:.1f}%",
                    "RSI Sig%": f"{a['rsi_signal_freq']:.1f}%",
                }
            )
        builder.table(
            ranking_rows,
            [
                "Rank",
                "TF",
                "MR Score",
                "Hurst",
                "Regime",
                "AutoCorr L1",
                "BB Rev%",
                "Z-Tail%",
                "RSI Sig%",
            ],
        )

        # ── Return Distribution ────────────────────────────────────────────────
        builder.section(
            "RETURN DISTRIBUTION",
            "Per-bar return stats — mean/std in %; skew & excess kurtosis",
        )
        ret_rows = [
            {
                "TF": a["tf"],
                "Candles": a["n_candles"],
                "Mean %": f"{a['ret_mean_pct']:.5f}",
                "Std %": f"{a['ret_std_pct']:.4f}",
                "Skew": f"{a['ret_skew']:.3f}",
                "Ex. Kurt": f"{a['ret_kurt']:.2f}",
            }
            for a in analyses
        ]
        builder.table(
            ret_rows, ["TF", "Candles", "Mean %", "Std %", "Skew", "Ex. Kurt"]
        )

        # ── RSI(14) ────────────────────────────────────────────────────────────
        builder.section(
            "RSI(14) DISTRIBUTION",
            "Extreme-zone frequency: oversold <30, overbought >70",
        )
        rsi_rows = [
            {
                "TF": a["tf"],
                "RSI Mean": f"{a['rsi_mean']:.1f}",
                "RSI Std": f"{a['rsi_std']:.1f}",
                "OS % (<30)": f"{a['rsi_os_freq']:.2f}%",
                "OB % (>70)": f"{a['rsi_ob_freq']:.2f}%",
                "Total Signal %": f"{a['rsi_signal_freq']:.2f}%",
            }
            for a in analyses
        ]
        builder.table(
            rsi_rows,
            ["TF", "RSI Mean", "RSI Std", "OS % (<30)", "OB % (>70)", "Total Signal %"],
        )

        # ── Bollinger Bands(20,2) ──────────────────────────────────────────────
        builder.section(
            "BOLLINGER BANDS(20,2)", "Band-touch rate and 3-bar reversion rate"
        )
        bb_rows = [
            {
                "TF": a["tf"],
                "Touch Rate %": f"{a['bb_touch_rate']:.2f}%",
                "3-Bar Reversion %": f"{a['bb_reversion_rate']:.1f}%",
            }
            for a in analyses
        ]
        builder.table(bb_rows, ["TF", "Touch Rate %", "3-Bar Reversion %"])

        # ── Z-Score(30) ────────────────────────────────────────────────────────
        builder.section("Z-SCORE(30) DISTRIBUTION", "Rolling Z-score tail frequency")
        z_rows = [
            {
                "TF": a["tf"],
                "Z Mean": f"{a['z_mean']:.3f}",
                "Z Std": f"{a['z_std']:.3f}",
                "|Z|>2 %": f"{a['z_tail_freq']:.2f}%",
                "|Z|>3 %": f"{a['z_extreme_freq']:.2f}%",
            }
            for a in analyses
        ]
        builder.table(z_rows, ["TF", "Z Mean", "Z Std", "|Z|>2 %", "|Z|>3 %"])

        # ── Recommendation ─────────────────────────────────────────────────────
        builder.section("RECOMMENDATION", "Primary timeframe and regime gate")
        hurst_interp = (
            "strong mean reversion"
            if best["hurst"] < 0.4
            else (
                "moderate mean reversion"
                if best["hurst"] < 0.5
                else "near-random walk — MR is marginal"
            )
        )
        autocorr_interp = (
            "negative (confirms MR tendency)"
            if best["autocorr_lag1"] < -0.02
            else (
                "near-zero (neutral)"
                if best["autocorr_lag1"] < 0.02
                else "positive (slight trending bias)"
            )
        )
        rec = [
            f"## {config.trading_pair} Mean Reversion Summary",
            "",
            f"**Primary Timeframe: {best_tf}**  (MR Score: {best['mr_score']:.1f}/100)",
            f"| Metric | Value | Interpretation |",
            f"|---|---|---|",
            f"| Hurst | {best['hurst']:.3f} | {hurst_interp} |",
            f"| AutoCorr Lag-1 | {best['autocorr_lag1']:.3f} | {autocorr_interp} |",
            f"| BB 3-Bar Reversion | {best['bb_reversion_rate']:.1f}% | {'strong' if best['bb_reversion_rate'] > 70 else 'moderate' if best['bb_reversion_rate'] > 50 else 'weak'} snap-back |",
            f"| Z-Score Tail (|z|>2) | {best['z_tail_freq']:.1f}% | {'fat tails, many signals' if best['z_tail_freq'] > 10 else 'thin tails, few signals'} |",
            f"| RSI Signal Freq | {best['rsi_signal_freq']:.1f}% | extreme-zone bars |",
            "",
        ]
        if regime_gate_tf and regime_gate_a:
            rga = regime_gate_a
            rec += [
                f"**Regime Gate: {regime_gate_tf}**",
                f"- Use {regime_gate_tf} Z-score or Hurst to gate {best_tf} entries.",
                f"- Only trade MR on {best_tf} when {regime_gate_tf} shows non-trending regime.",
                f"  - {regime_gate_tf} Hurst: {rga['hurst']:.3f} | AutoCorr: {rga['autocorr_lag1']:.3f} | Z-Tail: {rga['z_tail_freq']:.1f}%",
                "",
            ]
        else:
            rec.append(f"No suitable regime gate found among analyzed timeframes.\n")

        if best["hurst"] >= 0.5:
            rec.append(
                "⚠️ **Warning**: Top-ranked TF shows random-walk or trending behavior. MR strategies may underperform — consider a trend-following approach instead."
            )
        elif best["mr_score"] < 40:
            rec.append(
                "⚠️ **Warning**: Overall MR score is low. Verify with a longer sample before deploying capital."
            )
        else:
            rec.append(
                f"✅ **Verdict**: {best_tf} shows meaningful mean-reversion characteristics. Suitable for MR strategy deployment with {regime_gate_tf or 'no'} regime filter."
            )

        builder.markdown("\n".join(rec))
        builder.manual_order()
        await builder.save()

        summary_rows = [
            {
                "Rank": i + 1,
                "TF": a["tf"],
                "MR Score": f"{a['mr_score']:.0f}",
                "Hurst": f"{a['hurst']:.3f}",
                "AutoCorr": f"{a['autocorr_lag1']:.3f}",
                "BB Rev%": f"{a['bb_reversion_rate']:.0f}%",
                "RSI Sig%": f"{a['rsi_signal_freq']:.1f}%",
            }
            for i, a in enumerate(ranked)
        ]

        summary = (
            f"MR Research: {config.trading_pair} on {config.connector}\n"
            f"Best TF: {best_tf} (score {best['mr_score']:.0f}/100) · "
            f"Hurst {best['hurst']:.3f} · AutoCorr {best['autocorr_lag1']:.3f} · "
            f"Regime Gate: {regime_gate_tf or 'N/A'}"
        )
        return RoutineResult(
            text=summary,
            table_data=summary_rows,
            table_columns=[
                "Rank",
                "TF",
                "MR Score",
                "Hurst",
                "AutoCorr",
                "BB Rev%",
                "RSI Sig%",
            ],
        )

    except Exception as e:
        logger.error(f"Report failed: {e}", exc_info=True)
        return f"Analysis complete for {config.trading_pair} but report generation failed: {e}"
