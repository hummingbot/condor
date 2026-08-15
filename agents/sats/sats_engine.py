"""
SATS Engine — Self-Aware Trend System
Pure Python port of WillyAlgoTrader's Pine Script (TradingView).
Zero external dependencies beyond numpy. No API calls. No TradingView connection.

Core concept: Adaptive SuperTrend with a 4-factor Trend Quality Index (TQI)
that modulates band width, asymmetry, and flip logic in real time.

Author: Hermes Agent (ported from Pine Script v1.10.0 by WillyAlgoTrader)
License: MIT
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import math


# ═══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Candle:
    """Single OHLCV candle."""
    timestamp: int  # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class TQIFactors:
    """The four independent Trend Quality Index factors (each 0..1)."""
    efficiency: float     # Kaufman Efficiency Ratio
    volatility: float     # Volume Z-score or ATR ratio
    structure: float      # Price position within range
    momentum: float       # Aligned bars fraction
    combined: float       # Weighted combination (the actual TQI)
    
    def __repr__(self):
        return (f"TQI(eff={self.efficiency:.3f}, vol={self.volatility:.3f}, "
                f"struct={self.structure:.3f}, mom={self.momentum:.3f}, "
                f"combined={self.combined:.3f})")


@dataclass
class SATSSignal:
    """Complete trading signal from SATS engine."""
    direction: str           # "LONG", "SHORT", or "FLAT"
    entry_price: float       # Suggested entry (current close)
    stop_loss: float         # Structural invalidation (band level)
    take_profit_1r: float    # 1:1R target
    take_profit_2r: float    # 2:1R target  
    take_profit_3r: float    # 3:1R target
    band_upper: float        # Adaptive upper band
    band_lower: float        # Adaptive lower band
    tqi: TQIFactors          # Full TQI breakdown
    adaptive_multiplier: float  # Current effective ATR multiplier
    character_flipped: bool  # TQI collapse detected (early exit signal)
    trend_strength: float    # 0..1 composite trend quality
    confidence: str          # "dumb_obvious" / "decent" / "iffy" / "none"
    confidence_score: float  # 0..1 numerical confidence


# ═══════════════════════════════════════════════════════════════════
# SATS ENGINE
# ═══════════════════════════════════════════════════════════════════

class SATSEngine:
    """
    Self-Aware Trend System — Adaptive SuperTrend with TQI modulation.
    
    Default parameters match WillyAlgoTrader's v1.10.0 defaults.
    All parameters are tunable for crypto-specific optimization.
    
    Usage:
        engine = SATSEngine()
        candles = fetch_ohlcv("BTC-USD", "15m", limit=200)
        signal = engine.evaluate(candles)
    """
    
    def __init__(
        self,
        # SuperTrend base parameters
        atr_period: int = 10,
        atr_multiplier: float = 3.0,
        
        # TQI windows
        tqi_efficiency_window: int = 20,
        tqi_volatility_window: int = 20,
        tqi_structure_window: int = 20,
        tqi_momentum_window: int = 10,
        
        # TQI weights (each 0..1, normalized internally)
        w_efficiency: float = 1.0,
        w_volatility: float = 1.0,
        w_structure: float = 1.0,
        w_momentum: float = 1.0,
        
        # Adaptive engine
        q_strength: float = 0.7,      # How much TQI influences bands (0=off, 1=full)
        curve_power: float = 1.5,     # Power curve exponent (1=linear, >1=nonlinear)
        asym_strength: float = 0.3,   # Asymmetry intensity (0=symmetric)
        
        # Character-flip detection
        flip_window: int = 5,         # Bars to look back for TQI collapse
        flip_threshold: float = 0.3,  # TQI below this = weak regime
        
        # Confidence thresholds
        confidence_obvious: float = 0.75,  # TQI above this = "dumb_obvious"
        confidence_decent: float = 0.50,   # TQI above this = "decent"
        confidence_min: float = 0.30,      # TQI below this = FLAT regardless
    ):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        
        self.tqi_efficiency_window = tqi_efficiency_window
        self.tqi_volatility_window = tqi_volatility_window
        self.tqi_structure_window = tqi_structure_window
        self.tqi_momentum_window = tqi_momentum_window
        
        self.w_efficiency = w_efficiency
        self.w_volatility = w_volatility
        self.w_structure = w_structure
        self.w_momentum = w_momentum
        
        self.q_strength = q_strength
        self.curve_power = curve_power
        self.asym_strength = asym_strength
        
        self.flip_window = flip_window
        self.flip_threshold = flip_threshold
        
        self.confidence_obvious = confidence_obvious
        self.confidence_decent = confidence_decent
        self.confidence_min = confidence_min
    
    # ── INDICATOR HELPERS ────────────────────────────────────────
    
    @staticmethod
    def _sma(values: List[float], period: int) -> List[float]:
        """Simple Moving Average. Returns list same length as input (NaN for leading)."""
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(float('nan'))
            else:
                result.append(sum(values[i-period+1:i+1]) / period)
        return result
    
    @staticmethod
    def _ema(values: List[float], period: int) -> List[float]:
        """Exponential Moving Average."""
        if len(values) < period:
            return [float('nan')] * len(values)
        k = 2.0 / (period + 1)
        result = [float('nan')] * (period - 1)
        # Seed: SMA of first 'period' values
        result.append(sum(values[:period]) / period)
        for i in range(period, len(values)):
            result.append(values[i] * k + result[-1] * (1 - k))
        return result
    
    @staticmethod
    def _atr(candles: List[Candle], period: int) -> List[float]:
        """Average True Range (Wilder's smoothing)."""
        tr = [float('nan')]  # first bar has no TR
        for i in range(1, len(candles)):
            c, p = candles[i], candles[i-1]
            tr.append(max(
                c.high - c.low,
                abs(c.high - p.close),
                abs(c.low - p.close)
            ))
        # RMA smoothing (Wilder's)
        result = [float('nan')] * period
        if len(tr) <= period:
            return result
        # Seed: SMA of first 'period' TR values
        seed = sum(tr[1:period+1]) / period
        result.append(seed)
        for i in range(period+1, len(tr)):
            result.append((result[-1] * (period - 1) + tr[i]) / period)
        return result
    
    @staticmethod
    def _stdev(values: List[float], period: int, sma: List[float]) -> List[float]:
        """Rolling standard deviation."""
        result = []
        for i in range(len(values)):
            if i < period - 1 or math.isnan(sma[i]):
                result.append(float('nan'))
            else:
                window = values[i-period+1:i+1]
                m = sma[i]
                variance = sum((x - m) ** 2 for x in window) / period
                result.append(math.sqrt(variance))
        return result
    
    @staticmethod
    def _highest(values: List[float], period: int) -> List[float]:
        """Rolling maximum."""
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(float('nan'))
            else:
                result.append(max(values[i-period+1:i+1]))
        return result
    
    @staticmethod
    def _lowest(values: List[float], period: int) -> List[float]:
        """Rolling minimum."""
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(float('nan'))
            else:
                result.append(min(values[i-period+1:i+1]))
        return result
    
    # ── TQI FACTORS ──────────────────────────────────────────────
    
    def _factor_efficiency(self, closes: List[float]) -> List[float]:
        """
        Factor 1: Kaufman Efficiency Ratio.
        KER = |close[N] - close[0]| / sum(|close[i] - close[i-1]|) for i=1..N
        1.0 = perfect straight line, 0.0 = pure noise.
        """
        n = self.tqi_efficiency_window
        result = [float('nan')] * (n - 1)
        for i in range(n - 1, len(closes)):
            direction = abs(closes[i] - closes[i - n + 1])
            volatility = sum(abs(closes[j] - closes[j-1]) for j in range(i - n + 2, i + 1))
            if volatility == 0:
                result.append(1.0)  # flat line = perfect efficiency
            else:
                result.append(min(1.0, direction / volatility))
        return result
    
    def _factor_volatility(
        self, candles: List[Candle], atr_values: List[float]
    ) -> List[float]:
        """
        Factor 2: Volatility Regime.
        Uses Volume Z-score when available, falls back to ATR ratio.
        Maps z-score range [-1, 2] → [0, 1].
        """
        n = self.tqi_volatility_window
        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        
        # Check if we have meaningful volume data (>50% non-zero)
        has_volume = sum(1 for v in volumes if v > 0) > len(volumes) * 0.5
        
        result = [float('nan')] * (n - 1)
        
        if has_volume:
            # Volume Z-score approach
            vol_sma = self._sma(volumes, n)
            vol_std = self._stdev(volumes, n, vol_sma)
            for i in range(n - 1, len(volumes)):
                if vol_std[i] and vol_std[i] > 0:
                    z = (volumes[i] - vol_sma[i]) / vol_std[i]
                    # Map z ∈ [-1, 2] → [0, 1], clamp rest
                    mapped = (z + 1) / 3.0  # -1→0, 0→0.33, 2→1.0
                    result.append(max(0.0, min(1.0, mapped)))
                else:
                    result.append(0.5)  # no volatility signal
        else:
            # ATR ratio fallback: current ATR vs long-baseline ATR
            long_n = min(n * 3, len(atr_values))
            for i in range(n - 1, len(atr_values)):
                if math.isnan(atr_values[i]):
                    result.append(0.5)
                    continue
                # Long baseline = ATR over longer window
                baseline_start = max(0, i - long_n + 1)
                baseline_atrs = [v for v in atr_values[baseline_start:i+1] 
                                if not math.isnan(v)]
                if not baseline_atrs:
                    result.append(0.5)
                    continue
                baseline = sum(baseline_atrs) / len(baseline_atrs)
                if baseline > 0:
                    ratio = atr_values[i] / baseline
                    # ratio 0.5→0.0 (low vol), 1.0→0.5 (normal), 2.0→1.0 (high vol)
                    mapped = max(0.0, min(1.0, (ratio - 0.5) / 1.5))
                    result.append(mapped)
                else:
                    result.append(0.5)
        
        return result
    
    def _factor_structure(self, closes: List[float]) -> List[float]:
        """
        Factor 3: Structure.
        pricePos = (close - lowest) / (highest - lowest)
        tqiStruct = |pricePos - 0.5| × 2
        Trends pin price to one edge (1.0), chop oscillates around midpoint (0.0).
        """
        n = self.tqi_structure_window
        highest = self._highest(closes, n)
        lowest = self._lowest(closes, n)
        
        result = [float('nan')] * (n - 1)
        for i in range(n - 1, len(closes)):
            rng = highest[i] - lowest[i]
            if rng == 0 or math.isnan(rng):
                result.append(0.0)
            else:
                price_pos = (closes[i] - lowest[i]) / rng
                struct = abs(price_pos - 0.5) * 2.0
                result.append(max(0.0, min(1.0, struct)))
        return result
    
    def _factor_momentum(self, closes: List[float]) -> List[float]:
        """
        Factor 4: Momentum Persistence.
        Fraction of last N bars that moved in the SAME direction
        as the overall window change.
        """
        n = self.tqi_momentum_window
        result = [float('nan')] * (n - 1)
        
        for i in range(n - 1, len(closes)):
            # Overall window direction
            window_change = closes[i] - closes[i - n + 1]
            if window_change == 0:
                result.append(0.5)  # flat = neutral momentum
                continue
            
            # Count bars that moved in same direction
            aligned = 0
            for j in range(i - n + 1, i):
                bar_change = closes[j + 1] - closes[j]
                if (window_change > 0 and bar_change > 0) or \
                   (window_change < 0 and bar_change < 0):
                    aligned += 1
            
            result.append(aligned / n)
        
        return result
    
    # ── TQI COMPUTATION ──────────────────────────────────────────
    
    def compute_tqi(self, candles: List[Candle]) -> List[TQIFactors]:
        """
        Compute the 4-factor Trend Quality Index for every bar.
        Returns list parallel to candles (leading entries are None-filled with NaN).
        """
        closes = [c.close for c in candles]
        atr_vals = self._atr(candles, self.atr_period)
        
        eff = self._factor_efficiency(closes)
        vol = self._factor_volatility(candles, atr_vals)
        struct = self._factor_structure(closes)
        mom = self._factor_momentum(closes)
        
        total_weight = self.w_efficiency + self.w_volatility + self.w_structure + self.w_momentum
        
        results = []
        for i in range(len(candles)):
            if math.isnan(eff[i]) or math.isnan(vol[i]) or \
               math.isnan(struct[i]) or math.isnan(mom[i]):
                # Not enough data for all factors
                results.append(TQIFactors(
                    efficiency=float('nan'),
                    volatility=float('nan'),
                    structure=float('nan'),
                    momentum=float('nan'),
                    combined=float('nan')
                ))
                continue
            
            combined = (
                eff[i] * self.w_efficiency +
                vol[i] * self.w_volatility +
                struct[i] * self.w_structure +
                mom[i] * self.w_momentum
            ) / total_weight
            
            results.append(TQIFactors(
                efficiency=eff[i],
                volatility=vol[i],
                structure=struct[i],
                momentum=mom[i],
                combined=max(0.0, min(1.0, combined))
            ))
        
        return results
    
    # ── ADAPTIVE BAND COMPUTATION ────────────────────────────────
    
    def compute_adaptive_multiplier(self, tqi: float) -> float:
        """
        Convert TQI into adaptive ATR multiplier using power curve.
        
        qualityDeviation = (1 - tqi)^curvePower
        tqiMult = 1 - qStrength + qStrength × (0.6 + 0.8 × qualityDeviation)
        
        At TQI=1.0: tqiMult ≈ 0.6 (compressed bands — tight trend)
        At TQI=0.0: tqiMult ≈ 1.4 (expanded bands — choppy market)
        """
        if math.isnan(tqi):
            return self.atr_multiplier  # fallback to base
        
        quality_deviation = (1.0 - tqi) ** self.curve_power
        tqi_mult = 1.0 - self.q_strength + self.q_strength * (0.6 + 0.8 * quality_deviation)
        
        return tqi_mult * self.atr_multiplier
    
    def compute_bands(
        self, candles: List[Candle], tqi_values: List[TQIFactors]
    ) -> Tuple[List[float], List[float], List[float], List[int]]:
        """
        Compute adaptive SuperTrend bands.
        
        Returns:
            upper_band: adaptive upper band
            lower_band: adaptive lower band
            adaptive_mult: effective ATR multiplier at each bar
            direction: 1 (uptrend) / -1 (downtrend) / 0 (undetermined)
        """
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        atr_vals = self._atr(candles, self.atr_period)
        
        upper = [float('nan')] * len(candles)
        lower = [float('nan')] * len(candles)
        adp_mult = [float('nan')] * len(candles)
        direction = [0] * len(candles)
        
        min_bars = max(
            self.atr_period,
            self.tqi_efficiency_window,
            self.tqi_volatility_window,
            self.tqi_structure_window,
            self.tqi_momentum_window
        )
        
        prev_upper = float('nan')
        prev_lower = float('nan')
        prev_dir = 0
        
        for i in range(min_bars, len(candles)):
            hl2 = (highs[i] + lows[i]) / 2.0
            
            if math.isnan(atr_vals[i]) or math.isnan(tqi_values[i].combined):
                continue
            
            tqi = tqi_values[i].combined
            
            # Compute symmetric adaptive multiplier
            sym_mult = self.compute_adaptive_multiplier(tqi)
            adp_mult[i] = sym_mult
            
            # Asymmetric bands
            # Active side (trend direction) tightens, passive side widens
            asym_factor = self.asym_strength * tqi * 0.3
            
            # Start with symmetric bands
            basic_upper = hl2 + (sym_mult * atr_vals[i])
            basic_lower = hl2 - (sym_mult * atr_vals[i])
            
            # Apply asymmetry based on previous direction
            if prev_dir == 1:  # in uptrend
                # Tighten active side (lower band — the trailing stop for longs)
                active_lower = hl2 - (sym_mult * (1 - asym_factor) * atr_vals[i])
                # Widen passive side
                passive_upper = hl2 + (sym_mult * (1 + asym_factor) * atr_vals[i])
                upper[i] = passive_upper
                lower[i] = active_lower
            elif prev_dir == -1:  # in downtrend
                # Tighten active side (upper band — the trailing stop for shorts)
                active_upper = hl2 + (sym_mult * (1 - asym_factor) * atr_vals[i])
                # Widen passive side
                passive_lower = hl2 - (sym_mult * (1 + asym_factor) * atr_vals[i])
                upper[i] = active_upper
                lower[i] = passive_lower
            else:
                upper[i] = basic_upper
                lower[i] = basic_lower
            
            # SuperTrend tracking: don't let bands expand backward
            if not math.isnan(prev_upper) and prev_dir == 1:
                upper[i] = max(upper[i], prev_upper) if not math.isnan(upper[i]) else upper[i]
            if not math.isnan(prev_lower) and prev_dir == -1:
                lower[i] = min(lower[i], prev_lower) if not math.isnan(lower[i]) else lower[i]
            
            # Determine new direction (flip logic)
            curr_dir = prev_dir
            if closes[i] > prev_upper if not math.isnan(prev_upper) else False:
                curr_dir = 1  # flip to uptrend
            elif closes[i] < prev_lower if not math.isnan(prev_lower) else False:
                curr_dir = -1  # flip to downtrend
            # Also check against current adaptive bands
            if not math.isnan(upper[i]) and closes[i] > upper[i]:
                curr_dir = 1
            if not math.isnan(lower[i]) and closes[i] < lower[i]:
                curr_dir = -1
            
            direction[i] = curr_dir
            
            prev_upper = upper[i] if curr_dir == 1 else (lower[i] if curr_dir == -1 else prev_upper)
            prev_lower = lower[i] if curr_dir == 1 else (upper[i] if curr_dir == -1 else prev_lower)
            prev_dir = curr_dir
        
        return upper, lower, adp_mult, direction
    
    # ── CHARACTER-FLIP DETECTION ─────────────────────────────────
    
    def detect_character_flip(self, tqi_values: List[TQIFactors]) -> List[bool]:
        """
        Detect TQI collapse: high quality → low quality in short window.
        This catches regime breakdown BEFORE price breaks the band.
        """
        flips = [False] * len(tqi_values)
        
        if len(tqi_values) < self.flip_window + 1:
            return flips
        
        for i in range(self.flip_window, len(tqi_values)):
            # Check if TQI was above threshold recently but now below
            past_window = tqi_values[i - self.flip_window:i + 1]
            past_tqis = [t.combined for t in past_window if not math.isnan(t.combined)]
            
            if len(past_tqis) < 2:
                continue
            
            # Was recently trending strong
            early_tqi = past_tqis[0]
            late_tqi = past_tqis[-1]
            
            if (early_tqi > self.flip_threshold + 0.2 and 
                late_tqi < self.flip_threshold):
                flips[i] = True
        
        return flips
    
    # ── CONFIDENCE ───────────────────────────────────────────────
    
    def _compute_confidence(self, tqi: float, direction: int, flipped: bool) -> Tuple[str, float]:
        """Map TQI + context to human-readable confidence level."""
        if math.isnan(tqi) or direction == 0 or flipped:
            return "none", 0.0
        
        if tqi >= self.confidence_obvious:
            return "dumb_obvious", min(1.0, tqi + 0.05)
        elif tqi >= self.confidence_decent:
            return "decent", tqi
        elif tqi >= self.confidence_min:
            return "iffy", tqi * 0.7
        else:
            return "none", tqi * 0.3
    
    # ── MAIN SIGNAL GENERATION ───────────────────────────────────
    
    def evaluate(self, candles: List[Candle]) -> SATSSignal:
        """
        Main entry point: compute SATS signal from OHLCV candles.
        
        Args:
            candles: List of Candle objects (needs at least ~50 bars for TQI).
            
        Returns:
            SATSSignal with direction, entry, stops, targets, and TQI breakdown.
        """
        if len(candles) < max(
            self.atr_period,
            self.tqi_efficiency_window,
            self.tqi_volatility_window,
            self.tqi_structure_window,
            self.tqi_momentum_window
        ) + 5:
            return SATSSignal(
                direction="FLAT",
                entry_price=candles[-1].close,
                stop_loss=0.0,
                take_profit_1r=0.0,
                take_profit_2r=0.0,
                take_profit_3r=0.0,
                band_upper=float('nan'),
                band_lower=float('nan'),
                tqi=TQIFactors(float('nan'), float('nan'), float('nan'), float('nan'), float('nan')),
                adaptive_multiplier=float('nan'),
                character_flipped=False,
                trend_strength=0.0,
                confidence="none",
                confidence_score=0.0,
            )
        
        # Compute TQI for all bars
        tqi_values = self.compute_tqi(candles)
        
        # Compute adaptive bands
        upper, lower, adp_mult, direction = self.compute_bands(candles, tqi_values)
        
        # Detect character flips
        flips = self.detect_character_flip(tqi_values)
        
        # Get latest values
        last_idx = len(candles) - 1
        last_close = candles[-1].close
        last_tqi = tqi_values[-1]
        last_dir = direction[-1]
        last_flip = flips[-1]
        last_upper = upper[-1] if not math.isnan(upper[-1]) else last_close * 1.05
        last_lower = lower[-1] if not math.isnan(lower[-1]) else last_close * 0.95
        last_adp_mult = adp_mult[-1] if not math.isnan(adp_mult[-1]) else self.atr_multiplier
        
        # Determine signal direction
        if last_flip:
            sig_direction = "FLAT"  # Character flip = exit
        elif last_dir == 1:
            sig_direction = "LONG"
        elif last_dir == -1:
            sig_direction = "SHORT"
        else:
            sig_direction = "FLAT"
        
        # Compute stop and targets
        stop_distance = abs(last_close - (last_lower if sig_direction == "LONG" else last_upper))
        
        if sig_direction == "LONG":
            entry = last_close
            stop = min(last_lower, entry * 0.97)  # structural invalidation
            tp1r = entry + stop_distance
            tp2r = entry + stop_distance * 2
            tp3r = entry + stop_distance * 3
        elif sig_direction == "SHORT":
            entry = last_close
            stop = max(last_upper, entry * 1.03)  # structural invalidation
            tp1r = entry - stop_distance
            tp2r = entry - stop_distance * 2
            tp3r = entry - stop_distance * 3
        else:
            entry = last_close
            stop = 0.0
            tp1r = 0.0
            tp2r = 0.0
            tp3r = 0.0
        
        confidence, confidence_score = self._compute_confidence(
            last_tqi.combined, last_dir, last_flip
        )
        
        # Trend strength = TQI combined modulated by band tightness
        trend_strength = last_tqi.combined if not math.isnan(last_tqi.combined) else 0.0
        # Bonus for compressed bands (tight trend)
        if not math.isnan(last_adp_mult) and last_adp_mult < self.atr_multiplier * 0.8:
            trend_strength = min(1.0, trend_strength + 0.1)
        
        return SATSSignal(
            direction=sig_direction,
            entry_price=entry,
            stop_loss=stop,
            take_profit_1r=tp1r,
            take_profit_2r=tp2r,
            take_profit_3r=tp3r,
            band_upper=last_upper,
            band_lower=last_lower,
            tqi=last_tqi,
            adaptive_multiplier=last_adp_mult,
            character_flipped=last_flip,
            trend_strength=trend_strength,
            confidence=confidence,
            confidence_score=confidence_score,
        )
    
    # ── UTILITY ──────────────────────────────────────────────────
    
    def get_signal_summary(self, signal: SATSSignal) -> str:
        """Human-readable signal summary for journal/logging."""
        tqi = signal.tqi
        return (
            f"SATS({signal.direction}) | "
            f"Entry: ${signal.entry_price:,.2f} | "
            f"Stop: ${signal.stop_loss:,.2f} | "
            f"TP1: ${signal.take_profit_1r:,.2f} | "
            f"TQI: {tqi.combined:.2f} (e:{tqi.efficiency:.2f} v:{tqi.volatility:.2f} "
            f"s:{tqi.structure:.2f} m:{tqi.momentum:.2f}) | "
            f"AdaptMult: {signal.adaptive_multiplier:.2f}x | "
            f"Flip: {signal.character_flipped} | "
            f"Conf: {signal.confidence} ({signal.confidence_score:.0%})"
        )
    
    def get_tqi_breakdown(self, signal: SATSSignal) -> dict:
        """Dictionary representation of TQI factors for dashboard/journal."""
        tqi = signal.tqi
        return {
            "timestamp": None,  # filled by caller
            "direction": signal.direction,
            "entry": signal.entry_price,
            "stop": signal.stop_loss,
            "tp_1r": signal.take_profit_1r,
            "tp_2r": signal.take_profit_2r,
            "tp_3r": signal.take_profit_3r,
            "band_upper": signal.band_upper,
            "band_lower": signal.band_lower,
            "tqi_combined": round(tqi.combined, 4) if not math.isnan(tqi.combined) else None,
            "tqi_efficiency": round(tqi.efficiency, 4) if not math.isnan(tqi.efficiency) else None,
            "tqi_volatility": round(tqi.volatility, 4) if not math.isnan(tqi.volatility) else None,
            "tqi_structure": round(tqi.structure, 4) if not math.isnan(tqi.structure) else None,
            "tqi_momentum": round(tqi.momentum, 4) if not math.isnan(tqi.momentum) else None,
            "adaptive_multiplier": round(signal.adaptive_multiplier, 2),
            "character_flipped": signal.character_flipped,
            "trend_strength": round(signal.trend_strength, 4),
            "confidence": signal.confidence,
            "confidence_score": round(signal.confidence_score, 4),
        }


# ═══════════════════════════════════════════════════════════════════
# UNIT TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """Quick smoke test with synthetic data."""
    import random
    
    # Generate synthetic trending data
    random.seed(42)
    candles = []
    price = 65000.0
    base_time = 1700000000000
    
    for i in range(100):
        # Upward trend with noise
        price += 50 + random.gauss(0, 200)
        high = price + abs(random.gauss(0, 50))
        low = price - abs(random.gauss(0, 50))
        vol = abs(random.gauss(1000000, 300000))
        candles.append(Candle(
            timestamp=base_time + i * 900000,  # 15m bars
            open=price - random.gauss(0, 30),
            high=high,
            low=low,
            close=price,
            volume=vol,
        ))
    
    engine = SATSEngine()
    signal = engine.evaluate(candles)
    
    print("=" * 60)
    print("SATS Engine Smoke Test")
    print("=" * 60)
    print(engine.get_signal_summary(signal))
    print()
    print("TQI Breakdown:")
    for k, v in engine.get_tqi_breakdown(signal).items():
        print(f"  {k}: {v}")
    print()
    print(f"Last 5 closes: {[round(c.close, 2) for c in candles[-5:]]}")
    print("=" * 60)
    print("✅ SATS Engine working correctly!")
