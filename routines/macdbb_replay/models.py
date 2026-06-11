from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass
class JournalSignal1h:
    pair: str
    bb_pos_pct: float
    macd: float
    signal_line: float
    histogram: float
    macd_gap_ratio: float
    hist_ratio: float
    trend: str
    momentum: str
    formal_long: bool
    formal_short: bool
    adaptive_long: bool
    adaptive_short: bool
    strength_long: float
    strength_short: float
    # Optional replay bands/crosses/price (signals_1h extension after sS)
    bb_mid: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bullish_cross: bool | None = None
    bearish_cross: bool | None = None
    price: float | None = None

    def has_replay_bands(self) -> bool:
        return (
            self.bb_mid is not None
            and self.bb_upper is not None
            and self.bb_mid > 0
            and self.bb_upper > 0
        )


@dataclass
class Filter4h:
    pair: str
    trend: str
    bb_pos_pct: float | None = None
    macd: float | None = None
    signal_line: float | None = None
    histogram: float | None = None
    passed: bool = False


@dataclass
class TickMeta:
    tick: int
    timestamp: dt.datetime
    macd_pairs: list[str]
    neutral_pressure_streak: int | None
    entry_class: str | None
    tradeable_count: int | None = None
    scanner_analyzed: int | None = None
    queue_total: list[str] = field(default_factory=list)
    signals_1h: dict[str, JournalSignal1h] = field(default_factory=dict)
    filter_4h: dict[str, Filter4h] = field(default_factory=dict)


@dataclass
class ReportMeta:
    report_id: str
    filename: str
    created_at: dt.datetime
    pair: str
    interval: str


@dataclass
class ParsedReport:
    pair: str
    interval: str
    signal: str
    price: float
    bb_pos_pct: float
    bb_mid: float
    bb_upper: float
    macd: float
    signal_line: float
    histogram: float
    trend: str
    momentum: str
    bullish_cross: bool
    price_le_mid: bool
    bearish_cross: bool
    price_ge_upper: bool
    macd_lt_zero: bool


@dataclass
class SignalSnapshot:
    pair: str
    price: float
    signal: str
    parsed: ParsedReport | None
    metrics: dict[str, float | bool]
    filter_4h_pass: bool | None
    filter_4h_trend: str | None
    source: str
    report_id: str = ""
    journal_fl: int | None = None
    journal_fs: int | None = None
    journal_al: int | None = None
    journal_as: int | None = None
    price_trusted: bool = False


@dataclass
class OpenPosition:
    entry_tick: int
    entry_time: dt.datetime
    pair: str
    side: str
    entry_price: float
    entry_class: str
    entry_trigger: str
    notional_quote: float
    entry_score_long: float
    entry_score_short: float
    entry_neutral_streak: int
    entry_price_trusted: bool = False
    monitor_state: str = "aligned"
    neutral_streak: int = 0
    flip_streak: int = 0
    neutral_extra_pending: bool = False


@dataclass
class SimTrade:
    session_num: int
    entry_tick: int
    exit_tick: int
    pair: str
    side: str
    entry_price: float
    exit_price: float
    hold_ticks: int
    exit_reason: str
    pnl_quote: float
    return_pct: float
    entry_class: str
    entry_trigger: str
    notional_quote: float
    entry_score_long: float
    entry_score_short: float
    entry_neutral_streak: int


class ReplayConfigBase(BaseModel):
    """Shared threshold and simulation settings for MACD+BB replay."""

    preset: Literal["custom", "safe", "balanced", "opportunistic", "replay_probe"] = (
        Field(
            default="balanced",
            description=(
                "Adaptive threshold profile. Preset applies its adaptive_* values "
                "at run time (overrides those form fields). Use custom to tune manually."
            ),
        )
    )
    strategy_slug: str = Field(
        default="macdbb_scanner_aggressive_hl",
        description="Strategy folder under trading_agents/",
    )
    session_nums: str = Field(
        default="38",
        description="Session selector: 'all' or comma-separated values like '35,36'",
    )
    time_window_min: int = Field(
        default=25,
        description="Max minutes for matching report file to tick/pair",
    )
    data_source: Literal["journal_first", "journal_recompute", "html_only"] = Field(
        default="journal_first",
        description=(
            "journal_first: replay journal entry flags (fL/fS/aL/aS) as logged. "
            "journal_recompute: recompute entries from journal numerics + config "
            "(full formal when mid/up logged; else fL/fS fallback). "
            "html_only: HTML report payloads only."
        ),
    )
    activation_ticks: int = Field(
        default=6,
        description="neutral_pressure_streak threshold for adaptive mode",
    )
    adaptive_long_bb_pos_max: float = Field(default=48.0)
    adaptive_short_bb_pos_min: float = Field(default=72.0)
    adaptive_strong_long_bb_pos_max: float = Field(default=35.0)
    adaptive_strong_short_bb_pos_min: float = Field(default=85.0)
    adaptive_min_macd_gap_ratio: float = Field(default=0.08)
    adaptive_min_hist_ratio: float = Field(default=0.12)
    adaptive_score_open_min: float = Field(default=2.40)
    adaptive_score_open_min_extreme: float = Field(default=2.15)
    adaptive_hist_sign_bonus: float = Field(default=0.35)
    adaptive_hist_sign_penalty: float = Field(default=0.35)
    adaptive_momentum_bonus: float = Field(default=0.20)
    adaptive_momentum_penalty: float = Field(default=0.10)
    bb_proximity_epsilon_pct: float = Field(
        default=0.10,
        description="BB proximity epsilon for formal price gates",
    )
    sl_pct: float = Field(default=1.5)
    tp_pct: float = Field(default=3.0)
    max_holding_ticks: int = Field(
        default=8,
        description="Force-close after this many ticks if still open",
    )
    write_csv: bool = Field(default=True)
    compare_journal_flags: bool = Field(
        default=False,
        description="Emit journal fL/fS/aL/aS mismatch columns in per-pair CSV",
    )
    price_source: Literal["auto", "reports", "hl_candles"] = Field(
        default="auto",
        description=(
            "Price resolution: auto (HTML reports then HL candles), "
            "reports (HTML only), or hl_candles (Hyperliquid historical)."
        ),
    )
    hl_price_interval: str = Field(
        default="5m",
        description=(
            "HL candle interval for historical tick prices "
            "(5m recommended; 1m retention is short on HL)"
        ),
    )
    hl_max_concurrent: int = Field(
        default=1,
        description="Max parallel HL candleSnapshot pair requests during replay",
    )
    hl_request_interval_ms: int = Field(
        default=400,
        description="Minimum milliseconds between HL REST candle requests",
    )
    hl_max_retries: int = Field(
        default=6,
        description="Retries for HL candleSnapshot on HTTP 429/5xx",
    )
    require_price_data: bool = Field(
        default=True,
        description=(
            "Skip sessions (and entries) without trusted prices from price_source. "
            "Journal-only replay cannot compute PnL."
        ),
    )


class StrategyReplayConfig(ReplayConfigBase):
    """Replay full MACD+BB strategy (formal + adaptive entries, exits, flips) from session journals."""

    entry_modes: Literal["all", "formal", "adaptive"] = Field(
        default="all",
        description="Which entry paths to simulate",
    )
    max_open_executors: int = Field(default=3)
    formal_notional_quote: float = Field(default=200.0)
    neutral_exit_streak: int = Field(default=3)
    sl_cooldown_ticks: int = Field(default=3)
    flip_cooldown_ticks: int = Field(default=2)
    min_tradeable_count: int = Field(
        default=3,
        description="Skip tick entries when journal tradeable_count is below this",
    )
    ignore_risk_blocks: bool = Field(default=True)
    ignore_adaptive_4h_filter: bool = Field(
        default=False,
        description="Skip 4h regime filter for adaptive entries (backtest / strategy tuning)",
    )
    adaptive_requires_flat: bool = Field(
        default=True,
        description="Require 0 open positions before adaptive entries (matches live agent)",
    )


class AdaptiveReplayConfig(ReplayConfigBase):
    """Legacy adaptive-only replay with single position."""

    notional_quote: float = Field(
        default=200.0,
        description="Notional per simulated adaptive trade in quote currency",
    )
    exit_on_opposite_formal: bool = Field(
        default=True,
        description="Exit position if opposite formal signal appears",
    )


def compute_return_pct(side: str, entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    if side == "long":
        return (current_price / entry_price) - 1.0
    return (entry_price / current_price) - 1.0


def write_csv(path: Any, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    import csv
    from pathlib import Path

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_session_selector(session_selector: str, sessions_dir: Any) -> list[int]:
    from pathlib import Path

    sessions_path = Path(sessions_dir)
    if session_selector.strip().lower() == "all":
        session_numbers: list[int] = []
        for session_path in sessions_path.iterdir():
            if session_path.is_dir() and session_path.name.startswith("session_"):
                try:
                    session_numbers.append(int(session_path.name.split("_", 1)[1]))
                except ValueError:
                    continue
        return sorted(session_numbers)
    parsed_numbers: list[int] = []
    for value in session_selector.split(","):
        value = value.strip()
        if not value:
            continue
        parsed_numbers.append(int(value))
    return sorted(set(parsed_numbers))
