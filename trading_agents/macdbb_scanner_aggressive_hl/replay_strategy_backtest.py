#!/usr/bin/env python3
"""Replay full MACD+BB strategy from session journal + snapshot telemetry."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from routines.macdbb_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_session_hl_prices,
)
from routines.macdbb_replay.journal import parse_journal_ticks
from routines.macdbb_replay.models import StrategyReplayConfig, write_csv
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_replay.simulator import simulate_strategy_session

PER_PAIR_COLUMNS = [
    "session",
    "tick",
    "tick_time_utc",
    "pair",
    "report_id",
    "signal_source",
    "price_trusted",
    "entry_class_journal",
    "neutral_pressure_streak",
    "signal",
    "bb_pos_pct",
    "price",
    "trend",
    "momentum",
    "macd_gap_ratio",
    "hist_ratio",
    "formal_long",
    "formal_short",
    "adaptive_long_eligible",
    "adaptive_short_eligible",
    "adaptive_strength_long",
    "adaptive_strength_short",
    "adaptive_long_open",
    "adaptive_short_open",
    "filter_4h_pass",
    "filter_4h_trend",
    "blockers",
    "match_ok",
    "note",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay full strategy from journal.")
    parser.add_argument(
        "--journal",
        default="trading_agents/macdbb_scanner_aggressive_hl/sessions/session_38/journal.md",
    )
    parser.add_argument("--session-num", type=int, default=38)
    parser.add_argument("--preset", default="balanced")
    parser.add_argument("--entry-modes", default="all", choices=["all", "formal", "adaptive"])
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    journal_path = Path(args.journal)
    session_dir = journal_path.parent
    output_dir = Path(args.output_dir) if args.output_dir else session_dir

    config = resolve_config_with_preset(
        StrategyReplayConfig(
            preset=args.preset,
            entry_modes=args.entry_modes,
            write_csv=True,
        )
    )
    ticks = parse_journal_ticks(journal_path.read_text(encoding="utf-8"), session_dir=session_dir)
    reports = build_reports_by_pair(load_reports_index())
    hl_price_cache = None
    if config.price_source in ("auto", "hl_candles"):
        hl_settings = hl_prefetch_settings_from_config(config)
        hl_price_cache = asyncio.run(
            prefetch_session_hl_prices(
                ticks,
                interval=hl_settings.interval,
                max_concurrent=hl_settings.max_concurrent,
                request_interval_ms=hl_settings.request_interval_ms,
                max_retries=hl_settings.max_retries,
            )
        )
    per_pair, per_tick, trades, summary = simulate_strategy_session(
        args.session_num,
        ticks,
        reports,
        config,
        hl_price_cache=hl_price_cache,
    )

    write_csv(output_dir / "strategy_replay_per_pair.csv", per_pair, PER_PAIR_COLUMNS)
    write_csv(output_dir / "strategy_replay_per_tick.csv", per_tick, list(per_tick[0].keys()) if per_tick else [])
    trade_rows = [
        {
            "entry_trigger": trade.entry_trigger,
            "pair": trade.pair,
            "side": trade.side,
            "entry_tick": trade.entry_tick,
            "exit_tick": trade.exit_tick,
            "exit_reason": trade.exit_reason,
            "pnl_quote": round(trade.pnl_quote, 2),
        }
        for trade in trades
    ]
    write_csv(
        output_dir / "strategy_replay_trades.csv",
        trade_rows,
        list(trade_rows[0].keys()) if trade_rows else [],
    )
    (output_dir / "strategy_replay_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
