"""Replay adaptive entry logic using persisted MACD+BB routine reports."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

import logging
from typing import Any

from telegram.ext import ContextTypes

from routines.base import RoutineResult
from routines.macdbb_replay.adaptive_simulator import simulate_adaptive_session
from routines.macdbb_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_replay.journal import parse_journal_ticks
from routines.macdbb_replay.signals import session_has_trusted_prices
from routines.macdbb_replay.models import (
    AdaptiveReplayConfig,
    parse_session_selector,
    write_csv,
)
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.reports import build_reports_by_pair, load_reports_index

logger = logging.getLogger(__name__)

Config = AdaptiveReplayConfig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str | RoutineResult:
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
    if config.price_source in ("auto", "hl_candles") and parsed_sessions:
        hl_caches_by_session = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )

    for session_num, tick_meta_map in parsed_sessions.items():
        hl_price_cache = hl_caches_by_session.get(session_num)

        if config.require_price_data and not session_has_trusted_prices(
            tick_meta_map,
            reports_by_pair,
            config,
            hl_price_cache=hl_price_cache,
        ):
            skipped_sessions.append(session_num)
            session_rollup_rows.append(
                {
                    "Session": session_num,
                    "Status": "skipped (no price data)",
                    "Ticks Parsed": len(tick_meta_map),
                    "Pair Rows": 0,
                    "Sim Trades": 0,
                    "Win Rate %": "",
                    "Sim PnL $": "",
                }
            )
            continue

        per_pair_rows, per_tick_rows, trades = simulate_adaptive_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=config,
            hl_price_cache=hl_price_cache,
        )
        all_pair_rows.extend(per_pair_rows)
        all_tick_rows.extend(per_tick_rows)
        all_trades.extend(trades)

        session_pnl = sum(trade.pnl_quote for trade in trades)
        session_wins = sum(1 for trade in trades if trade.pnl_quote > 0)
        session_rollup_rows.append(
            {
                "Session": session_num,
                "Status": "ok",
                "Ticks Parsed": len(per_tick_rows),
                "Pair Rows": sum(1 for row in per_pair_rows if row.get("match_ok") == 1),
                "Sim Trades": len(trades),
                "Win Rate %": round(
                    (session_wins / len(trades) * 100.0) if trades else 0.0, 1
                ),
                "Sim PnL $": round(session_pnl, 2),
            }
        )

        if config.write_csv:
            output_dir = sessions_dir / f"session_{session_num}"
            write_csv(
                output_dir / "adaptive_replay_per_pair.csv",
                per_pair_rows,
                [
                    "session",
                    "tick",
                    "tick_time_utc",
                    "pair",
                    "report_id",
                    "entry_class_journal",
                    "neutral_pressure_streak",
                    "signal",
                    "bb_pos_pct",
                    "macd",
                    "signal_line",
                    "histogram",
                    "trend",
                    "momentum",
                    "macd_gap_ratio",
                    "hist_ratio",
                    "formal_long",
                    "formal_short",
                    "adaptive_long_eligible",
                    "adaptive_short_eligible",
                    "extreme_long_candidate",
                    "extreme_short_candidate",
                    "strength_gate",
                    "hist_sign_long",
                    "hist_sign_short",
                    "momentum_bonus_long",
                    "momentum_bonus_short",
                    "adaptive_strength_long",
                    "adaptive_strength_short",
                    "long_open_threshold",
                    "short_open_threshold",
                    "adaptive_long_open",
                    "adaptive_short_open",
                    "match_ok",
                    "note",
                ],
            )
            write_csv(
                output_dir / "adaptive_replay_per_tick.csv",
                per_tick_rows,
                [
                    "session",
                    "tick",
                    "tick_time_utc",
                    "entry_class_journal",
                    "neutral_pressure_streak",
                    "macd_pairs_count",
                    "best_candidate_pair",
                    "best_adaptive_score",
                    "best_long_score",
                    "best_short_score",
                    "sim_action",
                    "sim_reason",
                ],
            )

    if not session_rollup_rows:
        return "No session data could be replayed."

    total_trades = len(all_trades)
    total_wins = sum(1 for trade in all_trades if trade.pnl_quote > 0)
    total_pnl = sum(trade.pnl_quote for trade in all_trades)
    total_win_rate = (total_wins / total_trades) if total_trades else 0.0

    simulated_trades_rows = [
        {
            "Session": trade.session_num,
            "Pair": trade.pair,
            "Side": trade.side.upper(),
            "Entry Tick": trade.entry_tick,
            "Exit Tick": trade.exit_tick,
            "Hold Ticks": trade.hold_ticks,
            "Exit Reason": trade.exit_reason,
            "Entry Price": round(trade.entry_price, 8),
            "Exit Price": round(trade.exit_price, 8),
            "Return %": round(trade.return_pct, 3),
            "PnL $": round(trade.pnl_quote, 2),
            "Entry Score Long": round(trade.entry_score_long, 4),
            "Entry Score Short": round(trade.entry_score_short, 4),
            "Entry Streak": trade.entry_neutral_streak,
        }
        for trade in all_trades
    ]

    simulated_sessions = [
        row["Session"] for row in session_rollup_rows if row.get("Status") == "ok"
    ]
    summary_lines = [
        f"Adaptive replay backtest — {config.strategy_slug}",
        f"Preset: {config.preset}",
        f"Sessions requested: {', '.join(str(value) for value in selected_sessions)}",
        f"Sessions simulated: {', '.join(str(value) for value in simulated_sessions) or 'none'}",
        (
            f"Ticks replayed: {len(all_tick_rows)} | "
            f"Pair snapshots: {sum(1 for row in all_pair_rows if row.get('match_ok') == 1)}"
        ),
        (
            f"Sim trades: {total_trades} | Win rate: {total_win_rate:.1%} | "
            f"Sim PnL: ${total_pnl:+.2f}"
        ),
        (
            "Config: "
            f"score_min={config.adaptive_score_open_min}, "
            f"score_min_extreme={config.adaptive_score_open_min_extreme}, "
            f"gate(macd_gap={config.adaptive_min_macd_gap_ratio}, hist={config.adaptive_min_hist_ratio}), "
            f"BB(L<={config.adaptive_long_bb_pos_max}, S>={config.adaptive_short_bb_pos_min}, "
            f"Lext<={config.adaptive_strong_long_bb_pos_max}, Sext>={config.adaptive_strong_short_bb_pos_min}), "
            f"hist(+{config.adaptive_hist_sign_bonus}/-{config.adaptive_hist_sign_penalty}), "
            f"mom(+{config.adaptive_momentum_bonus}/-{config.adaptive_momentum_penalty})"
        ),
    ]
    if skipped_sessions:
        summary_lines.append(
            "Skipped (no price data): "
            + ", ".join(str(value) for value in skipped_sessions)
        )

    sections = [
        {"type": "kpi", "label": "Sessions", "value": str(len(session_rollup_rows))},
        {"type": "kpi", "label": "Sim Trades", "value": str(total_trades)},
        {"type": "kpi", "label": "Win Rate", "value": f"{total_win_rate:.1%}"},
        {"type": "kpi", "label": "Sim PnL", "value": f"${total_pnl:+.2f}"},
    ]

    table_columns = [
        "Session",
        "Status",
        "Ticks Parsed",
        "Pair Rows",
        "Sim Trades",
        "Win Rate %",
        "Sim PnL $",
    ]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Adaptive replay backtest: {config.strategy_slug}")
        builder.source("routine", "adaptive_replay_backtest").tags(
            ["trading-agent", "backtest", "adaptive"]
        )
        builder.kpi("Sim Trades", str(total_trades))
        builder.kpi("Win Rate", f"{total_win_rate:.1%}")
        builder.kpi("Sim PnL", f"${total_pnl:+.2f}")
        builder.markdown("\n".join(summary_lines))
        if session_rollup_rows:
            builder.table(session_rollup_rows, columns=table_columns)
        if simulated_trades_rows:
            builder.markdown("### Simulated Trades")
            builder.table(
                simulated_trades_rows,
                columns=[
                    "Session",
                    "Pair",
                    "Side",
                    "Entry Tick",
                    "Exit Tick",
                    "Hold Ticks",
                    "Exit Reason",
                    "Return %",
                    "PnL $",
                    "Entry Score Long",
                    "Entry Score Short",
                    "Entry Streak",
                ],
            )
        await builder.save()
    except Exception as error:
        logger.warning("Report generation failed: %s", error)

    return RoutineResult(
        text="\n".join(summary_lines),
        table_data=session_rollup_rows,
        table_columns=table_columns,
        sections=sections,
    )
