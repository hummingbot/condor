from __future__ import annotations

from routines.macdbb_replay.models import JournalSignal1h, ParsedReport, ReplayConfigBase


def _price_at_or_below_mid(parsed: ParsedReport, epsilon_pct: float) -> bool:
    if parsed.bb_mid <= 0:
        return parsed.price_le_mid
    threshold = parsed.bb_mid * (1.0 + epsilon_pct / 100.0)
    return parsed.price <= threshold


def _price_at_or_above_upper(parsed: ParsedReport, epsilon_pct: float) -> bool:
    if parsed.bb_upper <= 0:
        return parsed.price_ge_upper
    threshold = parsed.bb_upper * (1.0 - epsilon_pct / 100.0)
    return parsed.price >= threshold


def parsed_report_from_journal(
    journal_signal: JournalSignal1h,
    price: float,
    signal: str = "NEUTRAL",
    bb_mid: float = 0.0,
    bb_upper: float = 0.0,
) -> ParsedReport:
    return ParsedReport(
        pair=journal_signal.pair,
        interval="1h",
        signal=signal,
        price=price,
        bb_pos_pct=journal_signal.bb_pos_pct,
        bb_mid=bb_mid,
        bb_upper=bb_upper,
        macd=journal_signal.macd,
        signal_line=journal_signal.signal_line,
        histogram=journal_signal.histogram,
        trend=journal_signal.trend,
        momentum=journal_signal.momentum,
        bullish_cross=journal_signal.formal_long,
        price_le_mid=False,
        bearish_cross=journal_signal.formal_short,
        price_ge_upper=False,
        macd_lt_zero=journal_signal.macd < 0,
    )


def compute_metrics(
    parsed: ParsedReport, config: ReplayConfigBase
) -> dict[str, float | bool]:
    epsilon = config.bb_proximity_epsilon_pct
    macd_gap_ratio = abs(parsed.macd - parsed.signal_line) / max(
        abs(parsed.signal_line), 1e-6
    )
    hist_ratio = abs(parsed.histogram) / max(abs(parsed.macd), 1e-6)

    price_le_mid = _price_at_or_below_mid(parsed, epsilon)
    price_ge_upper = _price_at_or_above_upper(parsed, epsilon)

    formal_long = parsed.bullish_cross or (
        price_le_mid
        and parsed.trend == "bullish"
        and parsed.momentum == "increasing"
        and parsed.histogram > 0
    )
    formal_short = (parsed.bearish_cross and parsed.macd < 0) or (
        price_ge_upper
        and parsed.trend == "bearish"
        and parsed.momentum == "decreasing"
        and parsed.histogram < 0
    )
    has_formal = formal_long or formal_short

    adaptive_long_eligible = (
        parsed.trend == "bullish"
        and parsed.bb_pos_pct <= config.adaptive_long_bb_pos_max
    )
    adaptive_short_eligible = (
        parsed.trend == "bearish"
        and parsed.bb_pos_pct >= config.adaptive_short_bb_pos_min
    )
    extreme_long_candidate = (
        parsed.trend == "bullish"
        and parsed.bb_pos_pct <= config.adaptive_strong_long_bb_pos_max
    )
    extreme_short_candidate = (
        parsed.trend == "bearish"
        and parsed.bb_pos_pct >= config.adaptive_strong_short_bb_pos_min
    )
    strength_gate = (
        macd_gap_ratio >= config.adaptive_min_macd_gap_ratio
        or hist_ratio >= config.adaptive_min_hist_ratio
    )

    hist_sign_long = (
        config.adaptive_hist_sign_bonus
        if parsed.histogram >= 0
        else -config.adaptive_hist_sign_penalty
    )
    hist_sign_short = (
        config.adaptive_hist_sign_bonus
        if parsed.histogram <= 0
        else -config.adaptive_hist_sign_penalty
    )
    momentum_bonus_long = (
        config.adaptive_momentum_bonus
        if parsed.momentum == "increasing"
        else -config.adaptive_momentum_penalty
    )
    momentum_bonus_short = (
        config.adaptive_momentum_bonus
        if parsed.momentum == "decreasing"
        else -config.adaptive_momentum_penalty
    )

    adaptive_strength_long = (
        min(1.4, max(0.0, (50.0 - parsed.bb_pos_pct) / 12.0))
        + min(1.0, macd_gap_ratio)
        + min(0.6, hist_ratio)
        + hist_sign_long
        + momentum_bonus_long
    )
    adaptive_strength_short = (
        min(1.4, max(0.0, (parsed.bb_pos_pct - 70.0) / 12.0))
        + min(1.0, macd_gap_ratio)
        + min(0.6, hist_ratio)
        + hist_sign_short
        + momentum_bonus_short
    )

    long_open_threshold = (
        config.adaptive_score_open_min_extreme
        if extreme_long_candidate
        else config.adaptive_score_open_min
    )
    short_open_threshold = (
        config.adaptive_score_open_min_extreme
        if extreme_short_candidate
        else config.adaptive_score_open_min
    )

    adaptive_long_open = (
        adaptive_long_eligible
        and strength_gate
        and adaptive_strength_long >= long_open_threshold
        and not has_formal
    )
    adaptive_short_open = (
        adaptive_short_eligible
        and strength_gate
        and adaptive_strength_short >= short_open_threshold
        and not has_formal
    )

    return {
        "macd_gap_ratio": macd_gap_ratio,
        "hist_ratio": hist_ratio,
        "formal_long": formal_long,
        "formal_short": formal_short,
        "has_formal": has_formal,
        "adaptive_long_eligible": adaptive_long_eligible,
        "adaptive_short_eligible": adaptive_short_eligible,
        "extreme_long_candidate": extreme_long_candidate,
        "extreme_short_candidate": extreme_short_candidate,
        "strength_gate": strength_gate,
        "hist_sign_long": hist_sign_long,
        "hist_sign_short": hist_sign_short,
        "momentum_bonus_long": momentum_bonus_long,
        "momentum_bonus_short": momentum_bonus_short,
        "adaptive_strength_long": adaptive_strength_long,
        "adaptive_strength_short": adaptive_strength_short,
        "long_open_threshold": long_open_threshold,
        "short_open_threshold": short_open_threshold,
        "adaptive_long_open": adaptive_long_open,
        "adaptive_short_open": adaptive_short_open,
    }


def infer_signal_label(metrics: dict[str, float | bool]) -> str:
    if bool(metrics["formal_long"]):
        return "LONG"
    if bool(metrics["formal_short"]):
        return "SHORT"
    return "NEUTRAL"
