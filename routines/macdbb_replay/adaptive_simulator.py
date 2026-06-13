from __future__ import annotations

from typing import Any

from routines.macdbb_replay.models import (
    AdaptiveReplayConfig,
    SimTrade,
    TickMeta,
    compute_return_pct,
)
from routines.macdbb_replay.reports import ReportMeta
from routines.macdbb_replay.signals import build_tick_snapshots, session_has_trusted_prices


def simulate_adaptive_session(
    session_num: int,
    tick_meta_map: dict[int, TickMeta],
    reports_by_pair: dict[str, list[ReportMeta]],
    config: AdaptiveReplayConfig,
    hl_price_cache: dict[tuple[str, int], float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[SimTrade]]:
    if config.require_price_data and not session_has_trusted_prices(
        tick_meta_map,
        reports_by_pair,
        config,
        hl_price_cache=hl_price_cache,
    ):
        return [], [], []

    per_pair_rows: list[dict[str, Any]] = []
    per_tick_rows: list[dict[str, Any]] = []
    simulated_trades: list[SimTrade] = []

    current_position: dict[str, Any] | None = None
    sl_threshold = config.sl_pct / 100.0
    tp_threshold = config.tp_pct / 100.0
    last_price_by_pair: dict[str, float] = {}
    last_seen_by_pair: dict[str, tuple[int, float]] = {}

    for tick in sorted(tick_meta_map):
        meta = tick_meta_map[tick]
        snapshots = build_tick_snapshots(
            meta,
            reports_by_pair,
            config,
            last_price_by_pair,
            hl_price_cache=hl_price_cache,
        )
        candidate_rows: list[dict[str, Any]] = []
        best_pair = ""
        best_long_score = -1.0
        best_short_score = -1.0
        best_score = -1.0

        for pair, snapshot in snapshots.items():
            if snapshot.price_trusted:
                last_seen_by_pair[pair] = (tick, snapshot.price)
            metrics = snapshot.metrics
            long_score = float(metrics["adaptive_strength_long"])
            short_score = float(metrics["adaptive_strength_short"])
            max_score = max(long_score, short_score)
            if max_score > best_score:
                best_pair = pair
                best_score = max_score
                best_long_score = long_score
                best_short_score = short_score

            row = {
                "session": session_num,
                "tick": tick,
                "tick_time_utc": meta.timestamp.isoformat(),
                "pair": pair,
                "report_id": snapshot.report_id,
                "entry_class_journal": meta.entry_class or "",
                "adaptive_activation_streak": meta.adaptive_activation_streak
                if meta.adaptive_activation_streak is not None
                else "",
                "signal": snapshot.signal,
                "bb_pos_pct": round(snapshot.parsed.bb_pos_pct, 2)
                if snapshot.parsed
                else "",
                "macd": round(snapshot.parsed.macd, 6) if snapshot.parsed else "",
                "signal_line": round(snapshot.parsed.signal_line, 6)
                if snapshot.parsed
                else "",
                "histogram": round(snapshot.parsed.histogram, 6)
                if snapshot.parsed
                else "",
                "trend": snapshot.parsed.trend if snapshot.parsed else "",
                "momentum": snapshot.parsed.momentum if snapshot.parsed else "",
                "macd_gap_ratio": round(float(metrics["macd_gap_ratio"]), 4),
                "hist_ratio": round(float(metrics["hist_ratio"]), 4),
                "formal_long": int(bool(metrics["formal_long"])),
                "formal_short": int(bool(metrics["formal_short"])),
                "adaptive_long_eligible": int(bool(metrics["adaptive_long_eligible"])),
                "adaptive_short_eligible": int(bool(metrics["adaptive_short_eligible"])),
                "extreme_long_candidate": int(bool(metrics["extreme_long_candidate"])),
                "extreme_short_candidate": int(bool(metrics["extreme_short_candidate"])),
                "strength_gate": int(bool(metrics["strength_gate"])),
                "hist_sign_long": round(float(metrics["hist_sign_long"]), 4),
                "hist_sign_short": round(float(metrics["hist_sign_short"]), 4),
                "momentum_bonus_long": round(float(metrics["momentum_bonus_long"]), 4),
                "momentum_bonus_short": round(float(metrics["momentum_bonus_short"]), 4),
                "adaptive_strength_long": round(long_score, 4),
                "adaptive_strength_short": round(short_score, 4),
                "long_open_threshold": round(float(metrics["long_open_threshold"]), 4),
                "short_open_threshold": round(float(metrics["short_open_threshold"]), 4),
                "adaptive_long_open": int(bool(metrics["adaptive_long_open"])),
                "adaptive_short_open": int(bool(metrics["adaptive_short_open"])),
                "match_ok": 1,
                "note": "",
            }
            per_pair_rows.append(row)
            candidate_rows.append(row)

        for pair in meta.macd_pairs:
            if pair not in snapshots:
                per_pair_rows.append(
                    {
                        "session": session_num,
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "match_ok": 0,
                        "note": "no 1h report in window",
                    }
                )

        action_taken = "hold"
        action_reason = ""

        if current_position is None and candidate_rows:
            adaptive_mode_active = (
                (meta.adaptive_activation_streak or 0) >= config.activation_ticks
            )
            if adaptive_mode_active:
                open_candidates: list[tuple[str, str, float, float, float]] = []
                for pair, snapshot in snapshots.items():
                    metrics = snapshot.metrics
                    if bool(metrics["adaptive_long_open"]) and snapshot.price_trusted:
                        open_candidates.append(
                            (
                                pair,
                                "long",
                                snapshot.price,
                                float(metrics["adaptive_strength_long"]),
                                float(metrics["adaptive_strength_short"]),
                            )
                        )
                    if bool(metrics["adaptive_short_open"]) and snapshot.price_trusted:
                        open_candidates.append(
                            (
                                pair,
                                "short",
                                snapshot.price,
                                float(metrics["adaptive_strength_long"]),
                                float(metrics["adaptive_strength_short"]),
                            )
                        )
                if open_candidates:
                    selected = max(
                        open_candidates,
                        key=lambda item: (
                            item[3] if item[1] == "long" else item[4]
                        ),
                    )
                    current_position = {
                        "entry_tick": tick,
                        "pair": selected[0],
                        "side": selected[1],
                        "entry_price": selected[2],
                        "entry_score_long": selected[3],
                        "entry_score_short": selected[4],
                        "entry_adaptive_activation_streak": meta.adaptive_activation_streak or 0,
                    }
                    action_taken = "open"
                    action_reason = f"adaptive_{selected[1]}"

        if current_position is not None:
            open_pair = current_position["pair"]
            snapshot = snapshots.get(open_pair)
            if snapshot is not None and snapshot.price_trusted:
                metrics = snapshot.metrics
                current_return_pct = compute_return_pct(
                    current_position["side"],
                    current_position["entry_price"],
                    snapshot.price,
                )
                hold_ticks = tick - current_position["entry_tick"]
                exit_reason = ""
                if current_return_pct <= -sl_threshold:
                    exit_reason = "stop_loss_close_proxy"
                elif current_return_pct >= tp_threshold:
                    exit_reason = "take_profit_close_proxy"
                elif (
                    config.exit_on_opposite_formal
                    and current_position["side"] == "long"
                    and bool(metrics["formal_short"])
                ):
                    exit_reason = "opposite_formal"
                elif (
                    config.exit_on_opposite_formal
                    and current_position["side"] == "short"
                    and bool(metrics["formal_long"])
                ):
                    exit_reason = "opposite_formal"

                if exit_reason:
                    simulated_trades.append(
                        SimTrade(
                            session_num=session_num,
                            entry_tick=current_position["entry_tick"],
                            exit_tick=tick,
                            pair=open_pair,
                            side=current_position["side"],
                            entry_price=current_position["entry_price"],
                            exit_price=snapshot.price,
                            hold_ticks=hold_ticks,
                            exit_reason=exit_reason,
                            pnl_quote=config.notional_quote * current_return_pct,
                            return_pct=current_return_pct * 100.0,
                            entry_class="regime_adaptive_half_size",
                            entry_trigger=f"adaptive_{current_position['side']}",
                            notional_quote=config.notional_quote,
                            entry_score_long=current_position["entry_score_long"],
                            entry_score_short=current_position["entry_score_short"],
                            entry_adaptive_activation_streak=current_position["entry_adaptive_activation_streak"],
                        )
                    )
                    if action_taken == "hold":
                        action_taken = "close"
                        action_reason = exit_reason
                    else:
                        action_reason = f"{action_reason}+{exit_reason}"
                    current_position = None

        per_tick_rows.append(
            {
                "session": session_num,
                "tick": tick,
                "tick_time_utc": meta.timestamp.isoformat(),
                "entry_class_journal": meta.entry_class or "",
                "adaptive_activation_streak": meta.adaptive_activation_streak
                if meta.adaptive_activation_streak is not None
                else "",
                "macd_pairs_count": len(meta.macd_pairs),
                "best_candidate_pair": best_pair,
                "best_adaptive_score": round(best_score if best_score > 0 else 0.0, 4),
                "best_long_score": round(
                    best_long_score if best_long_score > 0 else 0.0, 4
                ),
                "best_short_score": round(
                    best_short_score if best_short_score > 0 else 0.0, 4
                ),
                "sim_action": action_taken,
                "sim_reason": action_reason,
            }
        )

    if current_position is not None:
        open_pair = current_position["pair"]
        last_seen = last_seen_by_pair.get(open_pair)
        if last_seen is not None:
            exit_tick, exit_price = last_seen
            return_pct = compute_return_pct(
                current_position["side"],
                current_position["entry_price"],
                exit_price,
            )
            simulated_trades.append(
                SimTrade(
                    session_num=session_num,
                    entry_tick=current_position["entry_tick"],
                    exit_tick=exit_tick,
                    pair=open_pair,
                    side=current_position["side"],
                    entry_price=current_position["entry_price"],
                    exit_price=exit_price,
                    hold_ticks=max(0, exit_tick - current_position["entry_tick"]),
                    exit_reason="session_end_proxy",
                    pnl_quote=config.notional_quote * return_pct,
                    return_pct=return_pct * 100.0,
                    entry_class="regime_adaptive_half_size",
                    entry_trigger=f"adaptive_{current_position['side']}",
                    notional_quote=config.notional_quote,
                    entry_score_long=current_position["entry_score_long"],
                    entry_score_short=current_position["entry_score_short"],
                    entry_adaptive_activation_streak=current_position["entry_adaptive_activation_streak"],
                )
            )

    return per_pair_rows, per_tick_rows, simulated_trades
