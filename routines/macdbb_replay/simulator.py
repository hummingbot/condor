from __future__ import annotations

import datetime as dt
from typing import Any

from routines.macdbb_replay.models import (
    OpenPosition,
    SimTrade,
    StrategyReplayConfig,
    TickMeta,
    compute_return_pct,
)
from routines.macdbb_replay.reports import ReportMeta
from routines.macdbb_replay.signals import (
    build_tick_snapshots,
    filter_4h_allows,
    session_has_trusted_prices,
)


def _adaptive_4h_allows(
    side: str,
    trend: str | None,
    passed: bool | None,
    config: StrategyReplayConfig,
) -> bool:
    if config.ignore_adaptive_4h_filter:
        return True
    return filter_4h_allows(side, trend, passed)


def _adaptive_notional(config: StrategyReplayConfig) -> float:
    return config.formal_notional_quote / 2.0


def _close_trade(
    session_num: int,
    position: OpenPosition,
    exit_tick: int,
    exit_price: float,
    exit_reason: str,
) -> SimTrade:
    return_pct = compute_return_pct(position.side, position.entry_price, exit_price)
    hold_ticks = exit_tick - position.entry_tick
    return SimTrade(
        session_num=session_num,
        entry_tick=position.entry_tick,
        exit_tick=exit_tick,
        pair=position.pair,
        side=position.side,
        entry_price=position.entry_price,
        exit_price=exit_price,
        hold_ticks=hold_ticks,
        exit_reason=exit_reason,
        pnl_quote=position.notional_quote * return_pct,
        return_pct=return_pct * 100.0,
        entry_class=position.entry_class,
        entry_trigger=position.entry_trigger,
        notional_quote=position.notional_quote,
        entry_score_long=position.entry_score_long,
        entry_score_short=position.entry_score_short,
        entry_neutral_streak=position.entry_neutral_streak,
    )


def _snapshot_row(
    session_num: int,
    tick: int,
    meta: TickMeta,
    pair: str,
    snapshot: Any,
    blockers: list[str],
    config: StrategyReplayConfig,
) -> dict[str, Any]:
    metrics = snapshot.metrics
    row: dict[str, Any] = {
        "session": session_num,
        "tick": tick,
        "tick_time_utc": meta.timestamp.isoformat(),
        "pair": pair,
        "report_id": snapshot.report_id,
        "signal_source": snapshot.source,
        "price_trusted": int(snapshot.price_trusted),
        "entry_class_journal": meta.entry_class or "",
        "neutral_pressure_streak": meta.neutral_pressure_streak
        if meta.neutral_pressure_streak is not None
        else "",
        "signal": snapshot.signal,
        "bb_pos_pct": round(snapshot.parsed.bb_pos_pct, 2) if snapshot.parsed else "",
        "price": round(snapshot.price, 8),
        "trend": snapshot.parsed.trend if snapshot.parsed else "",
        "momentum": snapshot.parsed.momentum if snapshot.parsed else "",
        "macd_gap_ratio": round(float(metrics["macd_gap_ratio"]), 4),
        "hist_ratio": round(float(metrics["hist_ratio"]), 4),
        "formal_long": int(bool(metrics["formal_long"])),
        "formal_short": int(bool(metrics["formal_short"])),
        "adaptive_long_eligible": int(bool(metrics["adaptive_long_eligible"])),
        "adaptive_short_eligible": int(bool(metrics["adaptive_short_eligible"])),
        "adaptive_strength_long": round(float(metrics["adaptive_strength_long"]), 4),
        "adaptive_strength_short": round(float(metrics["adaptive_strength_short"]), 4),
        "adaptive_long_open": int(bool(metrics["adaptive_long_open"])),
        "adaptive_short_open": int(bool(metrics["adaptive_short_open"])),
        "filter_4h_pass": ""
        if snapshot.filter_4h_pass is None
        else int(snapshot.filter_4h_pass),
        "filter_4h_trend": snapshot.filter_4h_trend or "",
        "blockers": ",".join(blockers),
        "match_ok": 1,
        "note": "",
    }
    if config.compare_journal_flags:
        row["journal_fL"] = snapshot.journal_fl if snapshot.journal_fl is not None else ""
        row["journal_fS"] = snapshot.journal_fs if snapshot.journal_fs is not None else ""
        row["journal_aL"] = snapshot.journal_al if snapshot.journal_al is not None else ""
        row["journal_aS"] = snapshot.journal_as if snapshot.journal_as is not None else ""
        row["mismatch_fL"] = (
            int(bool(metrics["formal_long"]) != bool(snapshot.journal_fl))
            if snapshot.journal_fl is not None
            else ""
        )
        row["mismatch_fS"] = (
            int(bool(metrics["formal_short"]) != bool(snapshot.journal_fs))
            if snapshot.journal_fs is not None
            else ""
        )
        row["mismatch_aL"] = (
            int(bool(metrics["adaptive_long_open"]) != bool(snapshot.journal_al))
            if snapshot.journal_al is not None
            else ""
        )
        row["mismatch_aS"] = (
            int(bool(metrics["adaptive_short_open"]) != bool(snapshot.journal_as))
            if snapshot.journal_as is not None
            else ""
        )
    return row


def _update_simulated_streak(
    meta: TickMeta,
    snapshots: dict[str, Any],
    current_streak: int,
    open_position_count: int,
) -> int:
    if meta.neutral_pressure_streak is not None:
        return meta.neutral_pressure_streak
    if open_position_count > 0:
        return current_streak
    if not snapshots:
        return current_streak
    all_neutral = all(item.signal == "NEUTRAL" for item in snapshots.values())
    if all_neutral:
        return current_streak + 1
    return current_streak


def _scanner_allows_entries(meta: TickMeta, config: StrategyReplayConfig) -> bool:
    if meta.tradeable_count is not None and meta.tradeable_count < config.min_tradeable_count:
        return False
    if meta.scanner_analyzed is not None and meta.scanner_analyzed < config.min_tradeable_count:
        return False
    return True


def _skipped_summary(reason: str) -> dict[str, Any]:
    return {
        "status": reason,
        "total_trades": 0,
        "wins": 0,
        "win_rate_pct": 0.0,
        "net_pnl_quote": 0.0,
        "formal_trades": 0,
        "adaptive_trades": 0,
        "formal_pnl": 0.0,
        "adaptive_pnl": 0.0,
        "by_trigger": {},
    }


def simulate_strategy_session(
    session_num: int,
    tick_meta_map: dict[int, TickMeta],
    reports_by_pair: dict[str, list[ReportMeta]],
    config: StrategyReplayConfig,
    hl_price_cache: dict[tuple[str, int], float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[SimTrade], dict[str, Any]]:
    if config.require_price_data and not session_has_trusted_prices(
        tick_meta_map,
        reports_by_pair,
        config,
        hl_price_cache=hl_price_cache,
    ):
        return [], [], [], _skipped_summary("skipped_no_price_data")

    per_pair_rows: list[dict[str, Any]] = []
    per_tick_rows: list[dict[str, Any]] = []
    simulated_trades: list[SimTrade] = []

    open_positions: dict[str, OpenPosition] = {}
    sl_cooldown_until: dict[str, int] = {}
    flip_cooldown_until: dict[str, int] = {}
    last_price_by_pair: dict[str, float] = {}
    last_seen_by_pair: dict[str, tuple[int, float]] = {}
    simulated_streak = 0

    sl_threshold = config.sl_pct / 100.0
    tp_threshold = config.tp_pct / 100.0
    adaptive_notional = _adaptive_notional(config)

    for tick in sorted(tick_meta_map):
        meta = tick_meta_map[tick]
        extra_pairs = list(open_positions.keys())
        snapshots = build_tick_snapshots(
            meta,
            reports_by_pair,
            config,
            last_price_by_pair,
            extra_pairs=extra_pairs,
            hl_price_cache=hl_price_cache,
        )
        for pair, snapshot in snapshots.items():
            if snapshot.price_trusted:
                last_seen_by_pair[pair] = (tick, snapshot.price)

        simulated_streak = _update_simulated_streak(
            meta, snapshots, simulated_streak, len(open_positions)
        )
        tick_actions: list[str] = []
        closes_this_tick: list[str] = []
        opens_this_tick: list[str] = []

        # Step 5 + barriers on RUNNING legs
        for pair in list(open_positions.keys()):
            position = open_positions[pair]
            snapshot = snapshots.get(pair)
            if snapshot is None or not snapshot.price_trusted:
                continue
            metrics = snapshot.metrics
            current_return_pct = compute_return_pct(
                position.side, position.entry_price, snapshot.price
            )
            hold_ticks = tick - position.entry_tick
            exit_reason = ""

            if current_return_pct <= -sl_threshold:
                exit_reason = "stop_loss_close_proxy"
            elif current_return_pct >= tp_threshold:
                exit_reason = "take_profit_close_proxy"
            elif hold_ticks >= config.max_holding_ticks:
                exit_reason = "max_holding_ticks"

            if not exit_reason:
                opposite_formal = (
                    position.side == "long" and bool(metrics["formal_short"])
                ) or (position.side == "short" and bool(metrics["formal_long"]))
                if opposite_formal and tick > flip_cooldown_until.get(pair, -1):
                    if position.flip_streak >= 1:
                        exit_reason = "flip_confirmed"
                    else:
                        position.monitor_state = "flip_pending"
                        position.flip_streak = 1
                elif position.flip_streak >= 1:
                    position.flip_streak = 0
                    position.monitor_state = "aligned"

            if not exit_reason:
                if snapshot.signal == "NEUTRAL":
                    position.neutral_streak += 1
                    position.monitor_state = "neutral_counting"
                elif (
                    position.side == "long" and bool(metrics["formal_long"])
                ) or (position.side == "short" and bool(metrics["formal_short"])):
                    position.neutral_streak = 0
                    position.monitor_state = "aligned"
                    position.neutral_extra_pending = False

                if position.neutral_streak >= config.neutral_exit_streak:
                    if current_return_pct < 0 and not position.neutral_extra_pending:
                        position.neutral_extra_pending = True
                    else:
                        exit_reason = "neutral_exit"

            if exit_reason:
                simulated_trades.append(
                    _close_trade(
                        session_num,
                        position,
                        tick,
                        snapshot.price,
                        exit_reason,
                    )
                )
                closes_this_tick.append(f"{pair}:{exit_reason}")
                del open_positions[pair]

                if exit_reason == "stop_loss_close_proxy":
                    sl_cooldown_until[pair] = tick + config.sl_cooldown_ticks
                elif exit_reason == "flip_confirmed":
                    flip_cooldown_until[pair] = tick + config.flip_cooldown_ticks
                    reverse_side = "short" if position.side == "long" else "long"
                    if (
                        len(open_positions) < config.max_open_executors
                        and filter_4h_allows(
                            reverse_side,
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                        )
                        and config.entry_modes in {"all", "formal"}
                    ):
                        reverse_trigger = (
                            f"flip_reverse_{reverse_side}"
                        )
                        open_positions[pair] = OpenPosition(
                            entry_tick=tick,
                            entry_time=meta.timestamp,
                            pair=pair,
                            side=reverse_side,
                            entry_price=snapshot.price,
                            entry_class="formal",
                            entry_trigger=reverse_trigger,
                            notional_quote=config.formal_notional_quote,
                            entry_score_long=float(metrics["adaptive_strength_long"]),
                            entry_score_short=float(metrics["adaptive_strength_short"]),
                            entry_neutral_streak=simulated_streak,
                            entry_price_trusted=True,
                        )
                        opens_this_tick.append(reverse_trigger)
                        simulated_streak = 0

        # Step 4 entries
        entries_allowed = _scanner_allows_entries(meta, config)
        if entries_allowed:
            formal_candidates: list[tuple[str, str, Any]] = []
            adaptive_candidates: list[tuple[str, str, Any]] = []

            for pair, snapshot in snapshots.items():
                if pair in open_positions:
                    continue
                if tick <= sl_cooldown_until.get(pair, -1):
                    blockers = ["sl_cooldown"]
                elif tick <= flip_cooldown_until.get(pair, -1):
                    blockers = ["flip_cooldown"]
                else:
                    blockers = []

                metrics = snapshot.metrics
                if not snapshot.price_trusted:
                    blockers.append("no_price_data")
                if config.entry_modes in {"all", "formal"}:
                    if bool(metrics["formal_long"]):
                        if not filter_4h_allows(
                            "long",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                        ):
                            blockers.append("4h_filter_block_long")
                        elif (
                            snapshot.price_trusted
                            and len(open_positions) < config.max_open_executors
                        ):
                            formal_candidates.append((pair, "long", snapshot))
                    if bool(metrics["formal_short"]):
                        if not filter_4h_allows(
                            "short",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                        ):
                            blockers.append("4h_filter_block_short")
                        elif (
                            snapshot.price_trusted
                            and len(open_positions) < config.max_open_executors
                        ):
                            formal_candidates.append((pair, "short", snapshot))

                adaptive_flat_ok = (
                    len(open_positions) == 0
                    if config.adaptive_requires_flat
                    else len(open_positions) < config.max_open_executors
                )
                tradeable_ok = (
                    meta.tradeable_count is None
                    or meta.tradeable_count >= config.min_tradeable_count
                )
                if (
                    config.entry_modes in {"all", "adaptive"}
                    and adaptive_flat_ok
                    and simulated_streak >= config.activation_ticks
                    and tradeable_ok
                ):
                    if bool(metrics["adaptive_long_open"]):
                        if not _adaptive_4h_allows(
                            "long",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                            config,
                        ):
                            blockers.append("4h_filter_block_long")
                        elif snapshot.price_trusted:
                            adaptive_candidates.append((pair, "long", snapshot))
                    if bool(metrics["adaptive_short_open"]):
                        if not _adaptive_4h_allows(
                            "short",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                            config,
                        ):
                            blockers.append("4h_filter_block_short")
                        elif snapshot.price_trusted:
                            adaptive_candidates.append((pair, "short", snapshot))

                per_pair_rows.append(
                    _snapshot_row(session_num, tick, meta, pair, snapshot, blockers, config)
                )

            for pair, side, snapshot in sorted(
                formal_candidates,
                key=lambda item: (
                    float(item[2].metrics["adaptive_strength_long"])
                    if item[1] == "long"
                    else float(item[2].metrics["adaptive_strength_short"])
                ),
                reverse=True,
            ):
                if pair in open_positions:
                    continue
                if len(open_positions) >= config.max_open_executors:
                    break
                metrics = snapshot.metrics
                trigger = f"formal_{side}"
                open_positions[pair] = OpenPosition(
                    entry_tick=tick,
                    entry_time=meta.timestamp,
                    pair=pair,
                    side=side,
                    entry_price=snapshot.price,
                    entry_class="formal",
                    entry_trigger=trigger,
                    notional_quote=config.formal_notional_quote,
                    entry_score_long=float(metrics["adaptive_strength_long"]),
                    entry_score_short=float(metrics["adaptive_strength_short"]),
                    entry_neutral_streak=simulated_streak,
                    entry_price_trusted=True,
                )
                opens_this_tick.append(trigger)
                simulated_streak = 0

            if not opens_this_tick and adaptive_candidates:
                ranked = sorted(
                    adaptive_candidates,
                    key=lambda item: (
                        float(item[2].metrics["adaptive_strength_long"])
                        if item[1] == "long"
                        else float(item[2].metrics["adaptive_strength_short"])
                    ),
                    reverse=True,
                )
                pair, side, snapshot = ranked[0]
                metrics = snapshot.metrics
                trigger = f"adaptive_{side}"
                open_positions[pair] = OpenPosition(
                    entry_tick=tick,
                    entry_time=meta.timestamp,
                    pair=pair,
                    side=side,
                    entry_price=snapshot.price,
                    entry_class="regime_adaptive_half_size",
                    entry_trigger=trigger,
                    notional_quote=adaptive_notional,
                    entry_score_long=float(metrics["adaptive_strength_long"]),
                    entry_score_short=float(metrics["adaptive_strength_short"]),
                    entry_neutral_streak=simulated_streak,
                    entry_price_trusted=True,
                )
                opens_this_tick.append(trigger)
                simulated_streak = 0
        else:
            for pair, snapshot in snapshots.items():
                per_pair_rows.append(
                    _snapshot_row(
                        session_num,
                        tick,
                        meta,
                        pair,
                        snapshot,
                        ["scanner_gate"],
                        config,
                    )
                )

        for pair in meta.macd_pairs:
            if pair not in snapshots:
                per_pair_rows.append(
                    {
                        "session": session_num,
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "match_ok": 0,
                        "note": "no signal snapshot",
                    }
                )

        if opens_this_tick:
            tick_actions.extend([f"open:{action}" for action in opens_this_tick])
        if closes_this_tick:
            tick_actions.extend([f"close:{action}" for action in closes_this_tick])
        if not tick_actions:
            tick_actions = ["hold"]

        per_tick_rows.append(
            {
                "session": session_num,
                "tick": tick,
                "tick_time_utc": meta.timestamp.isoformat(),
                "entry_class_journal": meta.entry_class or "",
                "neutral_pressure_streak": meta.neutral_pressure_streak
                if meta.neutral_pressure_streak is not None
                else simulated_streak,
                "sim_streak": simulated_streak,
                "open_positions": len(open_positions),
                "macd_pairs_count": len(meta.macd_pairs),
                "tradeable_count": meta.tradeable_count or "",
                "sim_actions": "|".join(tick_actions),
            }
        )

    for pair, position in list(open_positions.items()):
        if not position.entry_price_trusted:
            continue
        last_seen = last_seen_by_pair.get(pair)
        if last_seen is None:
            continue
        exit_tick, exit_price = last_seen
        simulated_trades.append(
            _close_trade(
                session_num,
                position,
                exit_tick,
                exit_price,
                "session_end_proxy",
            )
        )

    summary = _build_summary(simulated_trades)
    summary["status"] = "ok"
    return per_pair_rows, per_tick_rows, simulated_trades, summary


def _build_summary(trades: list[SimTrade]) -> dict[str, Any]:
    by_trigger: dict[str, dict[str, Any]] = {}
    for trade in trades:
        bucket = by_trigger.setdefault(
            trade.entry_trigger,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        bucket["count"] += 1
        if trade.pnl_quote > 0:
            bucket["wins"] += 1
        bucket["pnl"] += trade.pnl_quote

    formal_trades = [trade for trade in trades if trade.entry_class == "formal"]
    adaptive_trades = [
        trade for trade in trades if trade.entry_class == "regime_adaptive_half_size"
    ]
    total_pnl = sum(trade.pnl_quote for trade in trades)
    wins = sum(1 for trade in trades if trade.pnl_quote > 0)
    return {
        "status": "ok",
        "total_trades": len(trades),
        "wins": wins,
        "win_rate_pct": round((wins / len(trades) * 100.0) if trades else 0.0, 1),
        "net_pnl_quote": round(total_pnl, 2),
        "formal_trades": len(formal_trades),
        "adaptive_trades": len(adaptive_trades),
        "formal_pnl": round(sum(trade.pnl_quote for trade in formal_trades), 2),
        "adaptive_pnl": round(sum(trade.pnl_quote for trade in adaptive_trades), 2),
        "by_trigger": {
            trigger: {
                "count": values["count"],
                "wins": values["wins"],
                "pnl": round(values["pnl"], 2),
            }
            for trigger, values in sorted(by_trigger.items())
        },
    }
