"""
Anti-Folla V1 analysis utilities.

Pure-Python implementation of crowd-contrarian indicators
(no pandas_ta dependency — usable directly from Condor/UI layer):

- Rolling VWAP
- Donchian Channel (with shift to exclude current candle)
- OBV + divergence detection
- Volume spike detection
- Trade flow analysis (buy/sell pressure from OHLCV)
- Composite score calculation
- Parameter suggestion helpers
"""

import math
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# LOW-LEVEL CALCULATIONS
# ---------------------------------------------------------------------------

def calculate_rolling_vwap(
    candles: List[Dict[str, Any]],
    period: int = 20,
) -> List[float]:
    """
    Rolling VWAP = sum(close * volume, N) / sum(volume, N).
    Returns a list aligned with candles (NaN-padded as None for first period-1 items).
    """
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    volumes = [float(c.get("volume") or c.get("v") or 0) for c in candles]
    result: List[Optional[float]] = [None] * len(closes)

    for i in range(period - 1, len(closes)):
        pv = sum(closes[j] * volumes[j] for j in range(i - period + 1, i + 1))
        vol = sum(volumes[j] for j in range(i - period + 1, i + 1))
        result[i] = pv / vol if vol > 0 else closes[i]

    return [v for v in result if v is not None]


def calculate_donchian(
    candles: List[Dict[str, Any]],
    period: int = 20,
) -> Tuple[List[float], List[float]]:
    """
    Donchian Channel with shift(1) — excludes the current candle.
    Returns (upper_series, lower_series) aligned with candles from index `period`.
    """
    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]

    uppers: List[float] = []
    lowers: List[float] = []

    # shift(1): window ends at i-1, so range from i-period to i-1
    for i in range(period, len(candles)):
        window_h = highs[i - period: i]  # shifted: excludes current
        window_l = lows[i - period: i]
        uppers.append(max(window_h))
        lowers.append(min(window_l))

    return uppers, lowers


def calculate_obv(candles: List[Dict[str, Any]]) -> List[float]:
    """Calculate On-Balance Volume."""
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    volumes = [float(c.get("volume") or c.get("v") or 0) for c in candles]

    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


def detect_obv_divergence(
    candles: List[Dict[str, Any]],
    obv_series: List[float],
    lookback: int = 10,
) -> str:
    """
    Detect divergence between OBV and price.

    Returns:
        'bullish' – price falls, OBV rises (accumulation)
        'bearish' – price rises, OBV falls (distribution)
        'none'    – no divergence
    """
    if len(candles) < lookback or len(obv_series) < lookback:
        return "none"

    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    price_trend = closes[-1] - closes[-lookback]
    obv_trend = obv_series[-1] - obv_series[-lookback]

    if price_trend < 0 and obv_trend > 0:
        return "bullish"
    if price_trend > 0 and obv_trend < 0:
        return "bearish"
    return "none"


def detect_volume_spike(
    candles: List[Dict[str, Any]],
    threshold: float = 2.5,
) -> Tuple[bool, float]:
    """Return (is_spike, multiplier). Uses last 20 candles as baseline."""
    volumes = [float(c.get("volume") or c.get("v") or 0) for c in candles]
    if len(volumes) < 22:
        return False, 1.0

    avg_vol = sum(volumes[-21:-1]) / 20
    if avg_vol == 0:
        return False, 1.0

    multiplier = volumes[-1] / avg_vol
    return multiplier >= threshold, round(multiplier, 2)


def analyze_trade_flow(
    candles: List[Dict[str, Any]],
    lookback: int = 10,
) -> Dict[str, Any]:
    """
    Estimate buy/sell pressure and whale activity from OHLCV.
    Bullish candles (close > open) = buy pressure, weighted by volume.
    Whale proxy: last candle with volume > 3× avg AND body > avg body.
    """
    if len(candles) < lookback + 1:
        return {"whale_buying": False, "whale_selling": False, "retail_fomo": False, "buy_pressure": 0.5}

    recent = candles[-lookback:]
    closes = [float(c.get("close") or 0) for c in recent]
    opens_ = [float(c.get("open") or 0) for c in recent]
    volumes = [float(c.get("volume") or 0) for c in recent]

    bull_vol = sum(volumes[i] for i in range(len(recent)) if closes[i] > opens_[i])
    bear_vol = sum(volumes[i] for i in range(len(recent)) if closes[i] <= opens_[i])
    total_vol = bull_vol + bear_vol
    buy_pressure = bull_vol / total_vol if total_vol > 0 else 0.5

    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    bodies = [abs(closes[i] - opens_[i]) for i in range(len(recent))]
    avg_body = sum(bodies) / len(bodies) if bodies else 0

    last = candles[-1]
    last_close = float(last.get("close") or 0)
    last_open = float(last.get("open") or 0)
    last_vol = float(last.get("volume") or 0)
    last_body = abs(last_close - last_open)

    whale_buying = last_vol > avg_vol * 3.0 and last_close > last_open and last_body > avg_body
    whale_selling = last_vol > avg_vol * 3.0 and last_close < last_open and last_body > avg_body

    # Retail FOMO proxy
    all_closes = [float(c.get("close") or 0) for c in candles]
    price_change_pct = (all_closes[-1] - all_closes[-lookback]) / all_closes[-lookback] if all_closes[-lookback] > 0 else 0
    retail_fomo = bool(price_change_pct > 0.03 and buy_pressure > 0.7 and not whale_buying)

    return {
        "whale_buying": bool(whale_buying),
        "whale_selling": bool(whale_selling),
        "retail_fomo": retail_fomo,
        "buy_pressure": round(buy_pressure, 3),
    }


def calculate_composite_score(
    signals: Dict[str, Any],
    weight_vwap: float = 15,
    weight_donchian: float = 10,
    weight_obv: float = 15,
    weight_obi: float = 20,
    weight_volume_spike: float = 10,
    weight_trade_flow: float = 15,
    weight_funding: float = 15,
    obi_buy_threshold: float = 1.5,
    obi_sell_threshold: float = 0.67,
) -> float:
    """
    Compute weighted composite score from -100 (strong sell) to +100 (strong buy).
    Only activated components contribute to total_weight, then score is normalised.
    """
    score = 0.0
    total_weight = 0.0

    # VWAP
    if signals.get("vwap_above"):
        score += weight_vwap
        total_weight += weight_vwap
    elif signals.get("vwap_below"):
        score -= weight_vwap
        total_weight += weight_vwap

    # Donchian breakout
    if signals.get("donchian_breakout_up"):
        score += weight_donchian
        total_weight += weight_donchian
    elif signals.get("donchian_breakout_down"):
        score -= weight_donchian
        total_weight += weight_donchian

    # OBV divergence
    obv_div = signals.get("obv_divergence", "none")
    if obv_div == "bullish":
        score += weight_obv
        total_weight += weight_obv
    elif obv_div == "bearish":
        score -= weight_obv
        total_weight += weight_obv

    # OBI
    obi = signals.get("obi")
    if obi is not None:
        if obi >= obi_buy_threshold:
            score += weight_obi
            total_weight += weight_obi
        elif obi <= obi_sell_threshold:
            score -= weight_obi
            total_weight += weight_obi

    # Volume spike (directed by price trend)
    if signals.get("volume_spike"):
        price_trend = signals.get("price_trend", 0)
        if price_trend > 0:
            score += weight_volume_spike
        elif price_trend < 0:
            score -= weight_volume_spike
        total_weight += weight_volume_spike

    # Whale activity
    if signals.get("whale_buying"):
        score += weight_trade_flow
        total_weight += weight_trade_flow
    elif signals.get("whale_selling"):
        score -= weight_trade_flow
        total_weight += weight_trade_flow

    # Funding rate contrarian (futures only)
    funding_rate = signals.get("funding_rate")
    if funding_rate is not None:
        if funding_rate > 0.05:     # too many longs → contrarian short
            score -= weight_funding
            total_weight += weight_funding
        elif funding_rate < -0.05:  # too many shorts → contrarian long
            score += weight_funding
            total_weight += weight_funding

    if total_weight > 0:
        score = (score / total_weight) * 100

    return round(score, 2)


# ---------------------------------------------------------------------------
# FULL ANALYSIS (for Condor wizard / analysis endpoint)
# ---------------------------------------------------------------------------

def analyze_candles_for_anti_folla(
    candles: List[Dict[str, Any]],
    vwap_period: int = 20,
    donchian_period: int = 20,
    atr_period: int = 14,
    obv_divergence_lookback: int = 10,
    volume_spike_threshold: float = 2.5,
    obi_buy_threshold: float = 1.5,
    obi_sell_threshold: float = 0.67,
    score_buy_threshold: float = 50.0,
    score_sell_threshold: float = -50.0,
    weight_vwap: float = 15,
    weight_donchian: float = 10,
    weight_obv: float = 15,
    weight_obi: float = 20,
    weight_volume_spike: float = 10,
    weight_trade_flow: float = 15,
    weight_funding: float = 15,
) -> Dict[str, Any]:
    """
    Full Anti-Folla analysis from candle data.

    Returns a dict with:
    - current_signal: 1 (BUY), -1 (SELL), 0 (NEUTRAL)
    - composite_score: float -100..+100
    - vwap_current, donchian_upper_current, donchian_lower_current
    - obv_divergence: 'bullish' | 'bearish' | 'none'
    - volume_spike, volume_spike_multiplier
    - whale_buying, whale_selling, retail_fomo, buy_pressure
    - price_trend: pct change over last 20 candles
    - suggested_score_buy_threshold, suggested_score_sell_threshold
    - signal_count_long, signal_count_short (historical)
    - analysis_candles
    """
    result = {
        "current_signal": 0,
        "composite_score": 0.0,
        "vwap_current": None,
        "donchian_upper_current": None,
        "donchian_lower_current": None,
        "obv_divergence": "none",
        "volume_spike": False,
        "volume_spike_multiplier": 1.0,
        "whale_buying": False,
        "whale_selling": False,
        "retail_fomo": False,
        "buy_pressure": 0.5,
        "price_trend": 0.0,
        "suggested_score_buy_threshold": score_buy_threshold,
        "suggested_score_sell_threshold": score_sell_threshold,
        "signal_count_long": 0,
        "signal_count_short": 0,
        "analysis_candles": len(candles),
    }

    min_required = max(vwap_period, donchian_period, atr_period, obv_divergence_lookback * 2, 50)
    if not candles or len(candles) < min_required:
        return result

    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]

    # VWAP
    vwap_series = calculate_rolling_vwap(candles, vwap_period)
    current_price = closes[-1]
    current_vwap = vwap_series[-1] if vwap_series else current_price

    # Donchian
    donchian_upper, donchian_lower = calculate_donchian(candles, donchian_period)
    current_upper = donchian_upper[-1] if donchian_upper else current_price
    current_lower = donchian_lower[-1] if donchian_lower else current_price

    # OBV
    obv_series = calculate_obv(candles)
    obv_divergence = detect_obv_divergence(candles, obv_series, obv_divergence_lookback)

    # Volume spike
    is_spike, spike_mult = detect_volume_spike(candles, volume_spike_threshold)

    # Trade flow
    trade_flow = analyze_trade_flow(candles)

    # Price trend
    lookback_pt = min(20, len(closes) - 1)
    price_trend = (closes[-1] - closes[-lookback_pt - 1]) / closes[-lookback_pt - 1] if closes[-lookback_pt - 1] > 0 else 0.0

    signals: Dict[str, Any] = {
        "vwap_above": current_price > current_vwap,
        "vwap_below": current_price < current_vwap,
        "donchian_breakout_up": current_price > current_upper,
        "donchian_breakout_down": current_price < current_lower,
        "obv_divergence": obv_divergence,
        "obi": None,  # OBI requires live order book, not available from candles
        "volume_spike": is_spike,
        "price_trend": price_trend,
        "funding_rate": None,  # Requires live connector
        **trade_flow,
    }

    score = calculate_composite_score(
        signals,
        weight_vwap=weight_vwap,
        weight_donchian=weight_donchian,
        weight_obv=weight_obv,
        weight_obi=weight_obi,
        weight_volume_spike=weight_volume_spike,
        weight_trade_flow=weight_trade_flow,
        weight_funding=weight_funding,
        obi_buy_threshold=obi_buy_threshold,
        obi_sell_threshold=obi_sell_threshold,
    )

    if score >= score_buy_threshold:
        current_signal = 1
    elif score <= score_sell_threshold:
        current_signal = -1
    else:
        current_signal = 0

    # Historical signal count (rolling, no OBI/funding since those need live data)
    long_count = 0
    short_count = 0
    for i in range(min_required, len(candles)):
        sub = candles[:i + 1]
        sub_closes = [float(c.get("close") or 0) for c in sub]
        sub_vwap = calculate_rolling_vwap(sub, vwap_period)
        sub_dup, sub_dlo = calculate_donchian(sub, donchian_period)
        sub_obv = calculate_obv(sub)
        sub_div = detect_obv_divergence(sub, sub_obv, obv_divergence_lookback)
        sub_spike, _ = detect_volume_spike(sub, volume_spike_threshold)
        sub_flow = analyze_trade_flow(sub)
        sub_pt = (sub_closes[-1] - sub_closes[-min(20, len(sub_closes)-1)-1]) / sub_closes[-min(20, len(sub_closes)-1)-1] if len(sub_closes) > 1 else 0
        sub_price = sub_closes[-1]
        sub_signals = {
            "vwap_above": sub_price > (sub_vwap[-1] if sub_vwap else sub_price),
            "vwap_below": sub_price < (sub_vwap[-1] if sub_vwap else sub_price),
            "donchian_breakout_up": sub_price > (sub_dup[-1] if sub_dup else sub_price),
            "donchian_breakout_down": sub_price < (sub_dlo[-1] if sub_dlo else sub_price),
            "obv_divergence": sub_div,
            "obi": None,
            "volume_spike": sub_spike,
            "price_trend": sub_pt,
            "funding_rate": None,
            **sub_flow,
        }
        s = calculate_composite_score(sub_signals, weight_vwap, weight_donchian, weight_obv,
                                      weight_obi, weight_volume_spike, weight_trade_flow, weight_funding,
                                      obi_buy_threshold, obi_sell_threshold)
        if s >= score_buy_threshold:
            long_count += 1
        elif s <= score_sell_threshold:
            short_count += 1

    result.update({
        "current_signal": current_signal,
        "composite_score": score,
        "vwap_current": round(current_vwap, 6),
        "donchian_upper_current": round(current_upper, 6),
        "donchian_lower_current": round(current_lower, 6),
        "obv_divergence": obv_divergence,
        "volume_spike": is_spike,
        "volume_spike_multiplier": spike_mult,
        "price_trend": round(price_trend * 100, 3),
        "signal_count_long": long_count,
        "signal_count_short": short_count,
        **trade_flow,
    })

    return result


def format_anti_folla_analysis(analysis: Dict[str, Any]) -> str:
    """Format analysis results for display in wizard final step."""
    lines = []
    n = analysis.get("analysis_candles", 0)
    score = analysis.get("composite_score", 0.0)
    signal = analysis.get("current_signal", 0)
    signal_str = "🟢 BUY" if signal == 1 else ("🔴 SELL" if signal == -1 else "⚪ NEUTRAL")

    lines.append(f"Anti-Folla analysis ({n} candles):")
    lines.append(f"  Signal now: {signal_str}  |  Score: {score:.1f}")
    vwap = analysis.get("vwap_current")
    dup = analysis.get("donchian_upper_current")
    dlo = analysis.get("donchian_lower_current")
    if vwap:
        lines.append(f"  VWAP: {vwap:.6g}")
    if dup and dlo:
        lines.append(f"  Donchian: Upper={dup:.6g}  Lower={dlo:.6g}")
    lines.append(f"  OBV divergence: {analysis.get('obv_divergence', 'none')}")
    spike = analysis.get("volume_spike", False)
    mult = analysis.get("volume_spike_multiplier", 1.0)
    lines.append(f"  Volume spike: {'YES' if spike else 'no'} ({mult:.1f}×)")
    lines.append(f"  Whale buying: {analysis.get('whale_buying', False)}  |  Whale selling: {analysis.get('whale_selling', False)}")
    lines.append(f"  Retail FOMO: {analysis.get('retail_fomo', False)}  |  Buy pressure: {analysis.get('buy_pressure', 0.5):.1%}")
    lines.append(f"  Price trend (20c): {analysis.get('price_trend', 0.0):+.2f}%")
    lines.append(f"  Signals (history): LONG={analysis.get('signal_count_long', 0)} SHORT={analysis.get('signal_count_short', 0)}")

    return "\n".join(lines)

def get_af_strategy_suggestions(analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Ritorna suggerimenti per Anti-Folla V1 basati sull'analisi storica.
    Modifica soglie di score e pesi per adattare la sensibilità del segnale.
    """
    
    # Suggerisci soglie basate sull'analisi storica
    suggested_buy = analysis.get("suggested_score_buy_threshold", 50.0)
    suggested_sell = analysis.get("suggested_score_sell_threshold", -50.0)
    
    # Valori base TP/SL (usati dal controller base)
    base_tp = 0.03
    base_sl = 0.05
    base_ts_activation = 0.015
    base_ts_delta = 0.005

    return {
        "aggressive": {
            "label": "Target: Aggressivo (Entrate anticipate)",
            "score_buy_threshold": 30.0,           # Soglia più bassa → più BUY
            "score_sell_threshold": -30.0,         # Soglia più alta → più SELL
            # Pesi per lo score (più bilanciati, meno peso a funding)
            "weight_vwap": 15,
            "weight_donchian": 15,                 # Aumentato per più segnali breakout
            "weight_obv": 15,
            "weight_obi": 20,
            "weight_volume_spike": 10,
            "weight_trade_flow": 15,
            "weight_funding": 10,                  # Ridotto (meno impatto funding)
            "take_profit": round(base_tp * 0.7, 4),   # TP più stretto
            "stop_loss": round(base_sl * 0.8, 4),     # SL più stretto
            "trailing_stop_activation": round(base_ts_activation * 0.8, 4),
            "trailing_stop_delta": round(base_ts_delta * 0.8, 4),
        },
        "balanced": {
            "label": "Target: Bilanciato (Standard)",
            "score_buy_threshold": suggested_buy,
            "score_sell_threshold": suggested_sell,
            "weight_vwap": 15,
            "weight_donchian": 10,
            "weight_obv": 15,
            "weight_obi": 20,
            "weight_volume_spike": 10,
            "weight_trade_flow": 15,
            "weight_funding": 15,
            "take_profit": base_tp,
            "stop_loss": base_sl,
            "trailing_stop_activation": base_ts_activation,
            "trailing_stop_delta": base_ts_delta,
        },
        "conservative": {
            "label": "Target: Conservativo (Filtro stretto)",
            "score_buy_threshold": 70.0,           # Soglia più alta → meno BUY (solo segnali forti)
            "score_sell_threshold": -70.0,         # Soglia più bassa → meno SELL
            # Pesi per lo score (più peso a segnali confermati)
            "weight_vwap": 20,                     # Più peso al trend VWAP
            "weight_donchian": 5,                  # Meno peso breakout (più falsi)
            "weight_obv": 20,                      # Più peso divergenze OBV
            "weight_obi": 25,                     # Più peso OBI
            "weight_volume_spike": 5,              # Meno peso spike
            "weight_trade_flow": 20,               # Più peso whale
            "weight_funding": 5,                   # Poco peso funding
            "take_profit": round(base_tp * 1.3, 4),   # TP più largo
            "stop_loss": round(base_sl * 1.2, 4),     # SL più largo
            "trailing_stop_activation": round(base_ts_activation * 1.2, 4),
            "trailing_stop_delta": round(base_ts_delta * 1.2, 4),
        }
    }
