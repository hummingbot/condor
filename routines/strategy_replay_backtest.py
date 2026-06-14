"""Full MACD+BB strategy replay: formal + adaptive entries and Step 5 lifecycle."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

import json
import logging
from typing import Any

from telegram.ext import ContextTypes

from routines.base import RoutineResult
from routines.macdbb_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_replay.journal import parse_journal_ticks
from routines.macdbb_replay.models import (
    StrategyReplayConfig,
    parse_session_selector,
    write_csv,
)
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_replay import presets
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_replay.simulator import simulate_strategy_session

logger = logging.getLogger(__name__)

Config = StrategyReplayConfig

# Re-exported for routine discovery — UI reads this to sync form fields on preset change.
PRESET_OVERRIDES = presets.PRESET_OVERRIDES


PER_PAIR_COLUMNS = [
    "session",
    "tick",
    "tick_time_utc",
    "pair",
    "report_id",
    "signal_source",
    "price_trusted",
    "entry_class_journal",
    "adaptive_activation_streak",
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

PER_TICK_COLUMNS = [
    "session",
    "tick",
    "tick_time_utc",
    "entry_class_journal",
    "adaptive_activation_streak",
    "sim_streak",
    "open_positions",
    "macd_pairs_count",
    "tradeable_count",
    "sim_actions",
]

TRADE_COLUMNS = [
    "session",
    "pair",
    "side",
    "entry_class",
    "entry_trigger",
    "notional_quote",
    "entry_tick",
    "exit_tick",
    "hold_ticks",
    "exit_reason",
    "entry_price",
    "exit_price",
    "return_pct",
    "pnl_quote",
    "entry_score_long",
    "entry_score_short",
    "entry_adaptive_activation_streak",
]


def _trade_rows(trades: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "session": trade.session_num,
            "pair": trade.pair,
            "side": trade.side.upper(),
            "entry_class": trade.entry_class,
            "entry_trigger": trade.entry_trigger,
            "notional_quote": round(trade.notional_quote, 2),
            "entry_tick": trade.entry_tick,
            "exit_tick": trade.exit_tick,
            "hold_ticks": trade.hold_ticks,
            "exit_reason": trade.exit_reason,
            "entry_price": round(trade.entry_price, 8),
            "exit_price": round(trade.exit_price, 8),
            "return_pct": round(trade.return_pct, 3),
            "pnl_quote": round(trade.pnl_quote, 2),
            "entry_score_long": round(trade.entry_score_long, 4),
            "entry_score_short": round(trade.entry_score_short, 4),
            "entry_adaptive_activation_streak": trade.entry_adaptive_activation_streak,
        }
        for trade in trades
    ]


async def run(
    config: Config, context: ContextTypes.DEFAULT_TYPE
) -> str | RoutineResult:
    config = resolve_config_with_preset(config)
    strategy_dir = TRADING_AGENTS_DIR / config.strategy_slug
    sessions_dir = strategy_dir / "sessions"
    if not sessions_dir.is_dir():
        return f"Sessions directory not found: {sessions_dir}"

    try:
        selected_sessions = parse_session_selector(config.session_nums, sessions_dir)
    except ValueError as error:
        return f"Invalid session_nums: {error}"

    if not selected_sessions:
        return "No sessions matched the requested selector."

    reports = load_reports_index()
    if not reports:
        return "No macd_bb_analysis reports found in reports index."
    reports_by_pair = build_reports_by_pair(reports)

    all_pair_rows: list[dict[str, Any]] = []
    all_tick_rows: list[dict[str, Any]] = []
    all_trades: list[Any] = []
    session_rollup_rows: list[dict[str, Any]] = []
    skipped_sessions: list[int] = []
    compare_columns = [
        "journal_fL",
        "journal_fS",
        "journal_aL",
        "journal_aS",
        "mismatch_fL",
        "mismatch_fS",
        "mismatch_aL",
        "mismatch_aS",
    ]
    per_pair_columns = list(PER_PAIR_COLUMNS)
    if config.compare_journal_flags:
        per_pair_columns.extend(compare_columns)

    parsed_sessions: dict[int, dict[int, Any]] = {}
    for session_num in selected_sessions:
        journal_path = sessions_dir / f"session_{session_num}" / "journal.md"
        if not journal_path.is_file():
            logger.info("Skipping session %s (journal missing)", session_num)
            continue
        tick_meta_map = parse_journal_ticks(
            journal_path.read_text(encoding="utf-8"),
            session_dir=sessions_dir / f"session_{session_num}",
        )
        if not tick_meta_map:
            logger.info("Skipping session %s (no parsed ticks)", session_num)
            continue
        parsed_sessions[session_num] = tick_meta_map

    hl_caches_by_session: dict[int, dict[tuple[str, int], float]] = {}
    hl_candle_cache: dict[str, list[dict[str, float]]] = {}
    if config.price_source in ("auto", "hl_candles") and parsed_sessions:
        hl_caches_by_session, hl_candle_cache = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )

    for session_num, tick_meta_map in parsed_sessions.items():
        hl_price_cache = hl_caches_by_session.get(session_num)

        per_pair_rows, per_tick_rows, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=config,
            hl_price_cache=hl_price_cache,
            hl_candle_cache=hl_candle_cache,
        )
        status = summary.get("status", "ok")
        if status == "skipped_no_price_data":
            skipped_sessions.append(session_num)
            session_rollup_rows.append(
                {
                    "Session": session_num,
                    "Status": "skipped (no price data)",
                    "Ticks Parsed": len(tick_meta_map),
                    "Pair Rows": 0,
                    "Sim Trades": 0,
                    "Formal Trades": 0,
                    "Adaptive Trades": 0,
                    "Win Rate %": "",
                    "Sim PnL $": "",
                }
            )
            continue

        all_pair_rows.extend(per_pair_rows)
        all_tick_rows.extend(per_tick_rows)
        all_trades.extend(trades)

        session_rollup_rows.append(
            {
                "Session": session_num,
                "Status": "ok",
                "Ticks Parsed": len(per_tick_rows),
                "Pair Rows": sum(
                    1 for row in per_pair_rows if row.get("match_ok") == 1
                ),
                "Sim Trades": summary["total_trades"],
                "Formal Trades": summary["formal_trades"],
                "Adaptive Trades": summary["adaptive_trades"],
                "Win Rate %": summary["win_rate_pct"],
                "Sim PnL $": summary["net_pnl_quote"],
            }
        )

        if config.write_csv:
            output_dir = sessions_dir / f"session_{session_num}"
            write_csv(
                output_dir / "strategy_replay_per_pair.csv",
                per_pair_rows,
                per_pair_columns,
            )
            write_csv(
                output_dir / "strategy_replay_per_tick.csv",
                per_tick_rows,
                PER_TICK_COLUMNS,
            )
            write_csv(
                output_dir / "strategy_replay_trades.csv",
                _trade_rows(trades),
                TRADE_COLUMNS,
            )
            (output_dir / "strategy_replay_summary.json").write_text(
                json.dumps(summary, indent=2),
                encoding="utf-8",
            )

    if not session_rollup_rows:
        return "No session data could be replayed."

    simulated_sessions = [
        row["Session"] for row in session_rollup_rows if row.get("Status") == "ok"
    ]
    if not simulated_sessions and skipped_sessions:
        return (
            f"No sessions had trusted prices (price_source={config.price_source}). "
            f"Skipped: {', '.join(str(value) for value in skipped_sessions)}. "
            "Set require_price_data=false to replay signals without PnL."
        )

    total_trades = len(all_trades)
    total_wins = sum(1 for trade in all_trades if trade.pnl_quote > 0)
    total_pnl = sum(trade.pnl_quote for trade in all_trades)
    total_win_rate = (total_wins / total_trades) if total_trades else 0.0
    formal_trades = sum(1 for trade in all_trades if trade.entry_class == "formal")
    adaptive_trades = sum(
        1 for trade in all_trades if trade.entry_class == "regime_adaptive_half_size"
    )

    summary_lines = [
        f"Strategy replay backtest — {config.strategy_slug}",
        f"Preset: {config.preset} | Entry modes: {config.entry_modes}",
        f"Sessions requested: {', '.join(str(value) for value in selected_sessions)}",
        f"Sessions simulated: {', '.join(str(value) for value in simulated_sessions) or 'none'}",
        (
            f"Ticks replayed: {len(all_tick_rows)} | "
            f"Pair snapshots: {sum(1 for row in all_pair_rows if row.get('match_ok') == 1)}"
        ),
        (
            f"Sim trades: {total_trades} (formal={formal_trades}, adaptive={adaptive_trades}) | "
            f"Win rate: {total_win_rate:.1%} | Sim PnL: ${total_pnl:+.2f}"
        ),
    ]
    if skipped_sessions:
        summary_lines.append(
            "Skipped (no price data): "
            + ", ".join(str(value) for value in skipped_sessions)
        )

    table_columns = [
        "Session",
        "Status",
        "Ticks Parsed",
        "Pair Rows",
        "Sim Trades",
        "Formal Trades",
        "Adaptive Trades",
        "Win Rate %",
        "Sim PnL $",
    ]
    trade_table_rows = [
        {
            "Session": row["session"],
            "Pair": row["pair"],
            "Side": row["side"],
            "Entry Class": row["entry_class"],
            "Trigger": row["entry_trigger"],
            "Entry Tick": row["entry_tick"],
            "Exit Tick": row["exit_tick"],
            "Exit Reason": row["exit_reason"],
            "Return %": row["return_pct"],
            "PnL $": row["pnl_quote"],
        }
        for row in _trade_rows(all_trades)
    ]

    pnl_trend = (
        "positive" if total_pnl > 0 else "negative" if total_pnl < 0 else "neutral"
    )

    sections = [
        {"type": "kpi", "label": "Sessions", "value": str(len(session_rollup_rows))},
        {"type": "kpi", "label": "Sim Trades", "value": str(total_trades)},
        {"type": "kpi", "label": "Win Rate", "value": f"{total_win_rate:.1%}"},
        {
            "type": "kpi",
            "label": "Sim PnL",
            "value": f"${total_pnl:+.2f}",
            "trend": pnl_trend,
        },
    ]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Strategy replay backtest: {config.strategy_slug}")
        builder.source("routine", "strategy_replay_backtest").tags(
            ["trading-agent", "backtest", "strategy"]
        )
        builder.manual_order()
        builder.kpi("Sim Trades", str(total_trades))
        builder.kpi("Formal", str(formal_trades))
        builder.kpi("Adaptive", str(adaptive_trades))
        builder.kpi("Sim PnL", f"${total_pnl:+.2f}", trend=pnl_trend)
        builder.params(config.model_dump(), title="Run Parameters")
        builder.markdown("\n".join(summary_lines))
        if session_rollup_rows:
            builder.table(session_rollup_rows, columns=table_columns)
        if trade_table_rows:
            builder.markdown("### Simulated Trades")
            builder.table(
                trade_table_rows,
                columns=[
                    "Session",
                    "Pair",
                    "Side",
                    "Entry Class",
                    "Trigger",
                    "Entry Tick",
                    "Exit Tick",
                    "Exit Reason",
                    "Return %",
                    "PnL $",
                ],
            )
        else:
            builder.markdown("### Simulated Trades\n\nNo simulated trades.")
        await builder.save()
    except Exception as error:
        logger.warning("Report generation failed: %s", error)

    return RoutineResult(
        text="\n".join(summary_lines),
        table_data=session_rollup_rows,
        table_columns=table_columns,
        sections=sections,
    )
