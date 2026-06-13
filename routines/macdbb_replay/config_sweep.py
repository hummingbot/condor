"""Batch strategy-replay PnL sweep over session journals (CLI helper)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import gc
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from routines.macdbb_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_replay.journal import parse_journal_ticks
from routines.macdbb_replay.models import StrategyReplayConfig, parse_session_selector
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_replay.presets import PRESET_OVERRIDES, resolve_config_with_preset
from routines.macdbb_replay.reports import (
    ReportMeta,
    build_reports_by_pair,
    load_reports_index,
)
from routines.macdbb_replay.simulator import simulate_strategy_session

SESSIONS_36_50 = "36,37,38,39,40,41,42,43,44,45,46,47,48,49,50"

# hl_sweep_best baseline extended to sessions 36-50 (preset=custom avoids preset overwrite).
HL_SWEEP_BEST: dict[str, Any] = {
    "preset": "custom",
    "strategy_slug": "macdbb_scanner_aggressive_hl",
    "session_nums": SESSIONS_36_50,
    "data_source": "journal_recompute",
    "write_csv": False,
    "price_source": "auto",
    "hl_use_cache": True,
    "require_price_data": True,
    "activation_ticks": 1,
    "sl_pct": 2.4,
    "tp_pct": 10.0,
    "thesis_decay_exit_ticks": 32,
    "thesis_bb_drift_pts": 25.0,
    "adaptive_long_bb_pos_max": 65.0,
    "adaptive_short_bb_pos_min": 72.0,
    "adaptive_strong_long_bb_pos_max": 30.0,
    "adaptive_strong_short_bb_pos_min": 90.0,
    "adaptive_min_macd_gap_ratio": 0.06,
    "adaptive_min_hist_ratio": 0.09,
    "adaptive_score_open_min": 1.50,
    "adaptive_score_open_min_extreme": 1.00,
    "adaptive_hist_sign_bonus": 0.30,
    "adaptive_hist_sign_penalty": 0.30,
    "adaptive_momentum_bonus": 0.25,
    "adaptive_momentum_penalty": 0.05,
    "bb_proximity_epsilon_pct": 0.10,
    "ignore_adaptive_4h_filter": True,
    "adaptive_requires_flat": False,
    "max_open_executors": 3,
    "min_tradeable_count": 1,
    "sl_cooldown_ticks": 2,
    "flip_cooldown_ticks": 8,
    "formal_notional_quote": 500.0,
}


@dataclass
class SweepResult:
    name: str
    pnl: float
    trades: int
    formal: int
    adaptive: int
    win_rate: float
    exits: dict[str, int] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)


def _merge(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    merged = dict(base)
    merged.update(overrides)
    return merged


def build_sweep_configs() -> list[tuple[str, dict[str, Any]]]:
    """Named config variants covering adaptive, SL/TP, BB epsilon, 4h filter, thesis decay."""
    base = HL_SWEEP_BEST
    configs: list[tuple[str, dict[str, Any]]] = [
        ("baseline_hl_sweep_best", dict(base)),
    ]

    for preset_name in ("safe", "balanced", "opportunistic", "replay_probe"):
        preset_adaptive = PRESET_OVERRIDES[preset_name]
        configs.append(
            (
                f"preset_{preset_name}",
                _merge(
                    base,
                    **preset_adaptive,
                    sl_pct=2.4,
                    tp_pct=10.0,
                    thesis_decay_exit_ticks=32,
                    thesis_bb_drift_pts=25.0,
                    bb_proximity_epsilon_pct=0.10,
                    ignore_adaptive_4h_filter=True,
                ),
            )
        )

    sl_tp_grid = [
        ("sl1.5_tp6", {"sl_pct": 1.5, "tp_pct": 6.0}),
        ("sl2.0_tp8", {"sl_pct": 2.0, "tp_pct": 8.0}),
        ("sl3.0_tp12", {"sl_pct": 3.0, "tp_pct": 12.0}),
        ("sl2.4_tp6", {"sl_pct": 2.4, "tp_pct": 6.0}),
        ("sl2.4_tp15", {"sl_pct": 2.4, "tp_pct": 15.0}),
        ("sl1.8_tp12", {"sl_pct": 1.8, "tp_pct": 12.0}),
    ]
    for name, overrides in sl_tp_grid:
        configs.append((f"risk_{name}", _merge(base, **overrides)))

    thesis_grid = [
        ("decay16", {"thesis_decay_exit_ticks": 16}),
        ("decay24", {"thesis_decay_exit_ticks": 24}),
        ("decay48", {"thesis_decay_exit_ticks": 48}),
        ("drift15", {"thesis_bb_drift_pts": 15.0}),
        ("drift35", {"thesis_bb_drift_pts": 35.0}),
        ("decay16_drift15", {"thesis_decay_exit_ticks": 16, "thesis_bb_drift_pts": 15.0}),
    ]
    for name, overrides in thesis_grid:
        configs.append((f"thesis_{name}", _merge(base, **overrides)))

    bb_eps_grid = [
        ("eps0.05", {"bb_proximity_epsilon_pct": 0.05}),
        ("eps0.15", {"bb_proximity_epsilon_pct": 0.15}),
        ("eps0.20", {"bb_proximity_epsilon_pct": 0.20}),
        ("eps0.25", {"bb_proximity_epsilon_pct": 0.25}),
    ]
    for name, overrides in bb_eps_grid:
        configs.append((f"bb_eps_{name}", _merge(base, **overrides)))

    configs.append(
        ("filter_4h_on", _merge(base, ignore_adaptive_4h_filter=False))
    )

    adaptive_bb_grid = [
        (
            "bb_tight",
            {
                "adaptive_long_bb_pos_max": 55.0,
                "adaptive_short_bb_pos_min": 78.0,
                "adaptive_strong_long_bb_pos_max": 28.0,
                "adaptive_strong_short_bb_pos_min": 92.0,
            },
        ),
        (
            "bb_loose",
            {
                "adaptive_long_bb_pos_max": 75.0,
                "adaptive_short_bb_pos_min": 65.0,
                "adaptive_strong_long_bb_pos_max": 35.0,
                "adaptive_strong_short_bb_pos_min": 85.0,
            },
        ),
        (
            "bb_extreme",
            {
                "adaptive_long_bb_pos_max": 85.0,
                "adaptive_short_bb_pos_min": 60.0,
                "adaptive_strong_long_bb_pos_max": 25.0,
                "adaptive_strong_short_bb_pos_min": 95.0,
            },
        ),
    ]
    for name, overrides in adaptive_bb_grid:
        configs.append((f"adaptive_{name}", _merge(base, **overrides)))

    activation_grid = [
        ("act1", {"activation_ticks": 1}),
        ("act4", {"activation_ticks": 4}),
        ("act6", {"activation_ticks": 6}),
        ("act8", {"activation_ticks": 8}),
    ]
    for name, overrides in activation_grid:
        configs.append((f"activation_{name}", _merge(base, **overrides)))

    score_grid = [
        (
            "score_strict",
            {
                "adaptive_score_open_min": 2.20,
                "adaptive_score_open_min_extreme": 1.80,
                "adaptive_min_macd_gap_ratio": 0.10,
                "adaptive_min_hist_ratio": 0.14,
            },
        ),
        (
            "score_loose",
            {
                "adaptive_score_open_min": 1.00,
                "adaptive_score_open_min_extreme": 0.75,
                "adaptive_min_macd_gap_ratio": 0.04,
                "adaptive_min_hist_ratio": 0.06,
            },
        ),
        (
            "score_mid",
            {
                "adaptive_score_open_min": 1.80,
                "adaptive_score_open_min_extreme": 1.30,
                "adaptive_min_macd_gap_ratio": 0.08,
                "adaptive_min_hist_ratio": 0.11,
            },
        ),
    ]
    for name, overrides in score_grid:
        configs.append((f"adaptive_{name}", _merge(base, **overrides)))

    bonus_grid = [
        (
            "bonus_conservative",
            {
                "adaptive_hist_sign_bonus": 0.20,
                "adaptive_hist_sign_penalty": 0.45,
                "adaptive_momentum_bonus": 0.10,
                "adaptive_momentum_penalty": 0.20,
            },
        ),
        (
            "bonus_aggressive",
            {
                "adaptive_hist_sign_bonus": 0.40,
                "adaptive_hist_sign_penalty": 0.20,
                "adaptive_momentum_bonus": 0.35,
                "adaptive_momentum_penalty": 0.02,
            },
        ),
    ]
    for name, overrides in bonus_grid:
        configs.append((f"adaptive_{name}", _merge(base, **overrides)))

    combo_grid = [
        (
            "combo_loose_bb_wide_tp",
            _merge(
                base,
                adaptive_long_bb_pos_max=75.0,
                adaptive_short_bb_pos_min=65.0,
                adaptive_score_open_min=1.00,
                adaptive_score_open_min_extreme=0.75,
                tp_pct=15.0,
                sl_pct=2.4,
            ),
        ),
        (
            "combo_tight_fast_decay",
            _merge(
                base,
                adaptive_long_bb_pos_max=55.0,
                adaptive_short_bb_pos_min=78.0,
                adaptive_score_open_min=2.0,
                thesis_decay_exit_ticks=16,
                thesis_bb_drift_pts=15.0,
            ),
        ),
        (
            "combo_4h_strict_scores",
            _merge(
                base,
                ignore_adaptive_4h_filter=False,
                adaptive_score_open_min=2.0,
                adaptive_score_open_min_extreme=1.5,
            ),
        ),
        (
            "combo_eps_loose_sl_tight",
            _merge(
                base,
                bb_proximity_epsilon_pct=0.20,
                sl_pct=1.8,
                tp_pct=12.0,
            ),
        ),
        (
            "combo_opportunistic_risk",
            _merge(
                base,
                **PRESET_OVERRIDES["opportunistic"],
                sl_pct=2.0,
                tp_pct=12.0,
                thesis_decay_exit_ticks=24,
            ),
        ),
    ]
    configs.extend(combo_grid)

    return configs


BB_LOOSE_ANCHOR: dict[str, Any] = {
    "adaptive_long_bb_pos_max": 75.0,
    "adaptive_short_bb_pos_min": 65.0,
    "adaptive_strong_long_bb_pos_max": 35.0,
    "adaptive_strong_short_bb_pos_min": 85.0,
}


def build_refine_sweep_configs() -> list[tuple[str, dict[str, Any]]]:
    """Grid around adaptive_bb_loose + SL/TP from sessions 36-50 coarse sweep."""
    base = _merge(HL_SWEEP_BEST, **BB_LOOSE_ANCHOR)
    configs: list[tuple[str, dict[str, Any]]] = [
        ("refine_anchor_bb_loose", dict(base)),
        ("refine_anchor_sl1.8_tp12", _merge(base, sl_pct=1.8, tp_pct=12.0)),
    ]

    long_max_values = (70.0, 75.0, 80.0)
    short_min_values = (60.0, 65.0, 70.0)
    sl_values = (1.8, 2.0, 2.4)
    tp_values = (10.0, 12.0)

    for long_max in long_max_values:
        for short_min in short_min_values:
            for sl_pct in sl_values:
                for tp_pct in tp_values:
                    name = (
                        f"refine_L{int(long_max)}_S{int(short_min)}"
                        f"_sl{sl_pct}_tp{int(tp_pct)}"
                    )
                    configs.append(
                        (
                            name,
                            _merge(
                                base,
                                adaptive_long_bb_pos_max=long_max,
                                adaptive_short_bb_pos_min=short_min,
                                sl_pct=sl_pct,
                                tp_pct=tp_pct,
                            ),
                        )
                    )

    return configs


MEGA_SWEEP_GRID: dict[str, tuple[Any, ...]] = {
    "adaptive_long_bb_pos_max": (55.0, 65.0, 75.0, 80.0),
    "adaptive_short_bb_pos_min": (60.0, 65.0, 72.0, 78.0),
    "adaptive_strong_long_bb_pos_max": (28.0, 30.0, 35.0),
    "adaptive_strong_short_bb_pos_min": (85.0, 90.0, 92.0),
    "adaptive_min_macd_gap_ratio": (0.04, 0.06, 0.08, 0.10),
    "adaptive_min_hist_ratio": (0.06, 0.09, 0.12, 0.16),
    "adaptive_score_open_min": (1.0, 1.5, 2.0, 2.4),
    "adaptive_score_open_min_extreme": (0.75, 1.0, 1.5, 2.0),
    "adaptive_hist_sign_bonus": (0.25, 0.30, 0.35),
    "adaptive_hist_sign_penalty": (0.25, 0.30, 0.40),
    "adaptive_momentum_bonus": (0.10, 0.20, 0.25, 0.35),
    "adaptive_momentum_penalty": (0.05, 0.10, 0.15, 0.20),
    "activation_ticks": (1, 4, 6, 8),
    "sl_pct": (1.5, 1.8, 2.0, 2.4, 3.0),
    "tp_pct": (6.0, 8.0, 10.0, 12.0, 15.0),
    "thesis_decay_exit_ticks": (12, 16, 24, 32, 48),
    "thesis_bb_drift_pts": (15.0, 25.0, 35.0, 50.0),
    "bb_proximity_epsilon_pct": (0.05, 0.10, 0.15, 0.20),
    "ignore_adaptive_4h_filter": (True, False),
}

MEGA_SWEEP_ANCHORS: list[tuple[str, dict[str, Any]]] = [
    ("mega_baseline_hl_sweep_best", dict(HL_SWEEP_BEST)),
    (
        "mega_anchor_bb_loose",
        _merge(HL_SWEEP_BEST, **BB_LOOSE_ANCHOR),
    ),
    (
        "mega_preset_hl_bb_loose_best",
        _merge(HL_SWEEP_BEST, **PRESET_OVERRIDES["hl_bb_loose_best"]),
    ),
]


def _mega_config_name(overrides: dict[str, Any]) -> str:
    parts = [
        f"L{int(overrides['adaptive_long_bb_pos_max'])}",
        f"S{int(overrides['adaptive_short_bb_pos_min'])}",
        f"sl{overrides['sl_pct']}",
        f"tp{int(overrides['tp_pct'])}",
        f"td{overrides['thesis_decay_exit_ticks']}",
        f"dr{int(overrides['thesis_bb_drift_pts'])}",
        f"eps{overrides['bb_proximity_epsilon_pct']}",
        f"4h{int(not overrides['ignore_adaptive_4h_filter'])}",
        f"act{overrides['activation_ticks']}",
    ]
    return "mega_" + "_".join(parts)


def _mega_space_size() -> int:
    return math.prod(len(values) for values in MEGA_SWEEP_GRID.values())


def _random_mega_combo(rng: random.Random) -> dict[str, Any]:
    """Uniform random draw over the factorial grid — O(1) memory per sample."""
    return {key: rng.choice(values) for key, values in MEGA_SWEEP_GRID.items()}


def iter_mega_sweep_configs(
    *,
    min_configs: int = 560,
    seed: int = 42,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield mega configs one at a time; never materialize the full factorial space."""
    for name, overrides in MEGA_SWEEP_ANCHORS:
        yield name, overrides

    rng = random.Random(seed)
    seen_names: set[str] = {name for name, _ in MEGA_SWEEP_ANCHORS}
    target = max(min_configs, 560)
    emitted = 0
    attempts = 0
    max_attempts = target * 20

    while emitted < target and attempts < max_attempts:
        attempts += 1
        combo = _random_mega_combo(rng)
        name = _mega_config_name(combo)
        if name in seen_names:
            name = f"{name}_n{emitted}"
        if name in seen_names:
            continue
        seen_names.add(name)
        yield name, _merge(HL_SWEEP_BEST, **combo)
        emitted += 1


def build_mega_sweep_configs(
    *,
    min_configs: int = 560,
    seed: int = 42,
) -> list[tuple[str, dict[str, Any]]]:
    """List form for tests only — prefer ``iter_mega_sweep_configs`` for mega runs."""
    return list(iter_mega_sweep_configs(min_configs=min_configs, seed=seed))


async def _load_sessions(
    config: StrategyReplayConfig,
) -> tuple[
    dict[int, dict[int, Any]],
    dict[int, dict[tuple[str, int], float]],
    dict[str, list[dict[str, float]]],
    list[int],
]:
    strategy_dir = TRADING_AGENTS_DIR / config.strategy_slug
    sessions_dir = strategy_dir / "sessions"
    selected_sessions = parse_session_selector(config.session_nums, sessions_dir)

    parsed_sessions: dict[int, dict[int, Any]] = {}
    for session_num in selected_sessions:
        journal_path = sessions_dir / f"session_{session_num}" / "journal.md"
        if not journal_path.is_file():
            continue
        tick_meta_map = parse_journal_ticks(
            journal_path.read_text(encoding="utf-8"),
            session_dir=sessions_dir / f"session_{session_num}",
        )
        if tick_meta_map:
            parsed_sessions[session_num] = tick_meta_map

    hl_caches_by_session: dict[int, dict[tuple[str, int], float]] = {}
    hl_candle_cache: dict[str, list[dict[str, float]]] = {}
    if parsed_sessions:
        hl_caches_by_session, hl_candle_cache = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )

    return parsed_sessions, hl_caches_by_session, hl_candle_cache, selected_sessions


def _run_config(
    name: str,
    overrides: dict[str, Any],
    parsed_sessions: dict[int, dict[int, Any]],
    hl_caches_by_session: dict[int, dict[tuple[str, int], float]],
    hl_candle_cache: dict[str, list[dict[str, float]]],
    reports_by_pair: dict[str, list[ReportMeta]],
) -> SweepResult:
    config = resolve_config_with_preset(StrategyReplayConfig(**overrides))
    total_trades = 0
    wins = 0
    pnl = 0.0
    formal = 0
    adaptive = 0
    exit_counts: Counter[str] = Counter()

    for session_num, tick_meta_map in parsed_sessions.items():
        hl_price_cache = hl_caches_by_session.get(session_num)
        _, _, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=config,
            hl_price_cache=hl_price_cache,
            hl_candle_cache=hl_candle_cache,
        )
        if summary.get("status") == "skipped_no_price_data":
            continue
        for trade in trades:
            total_trades += 1
            pnl += trade.pnl_quote
            if trade.pnl_quote > 0:
                wins += 1
            if trade.entry_class == "formal":
                formal += 1
            elif trade.entry_class == "regime_adaptive_half_size":
                adaptive += 1
            exit_counts[trade.exit_reason] += 1

    win_rate = (wins / total_trades) if total_trades else 0.0
    diff_keys = {
        key: value
        for key, value in overrides.items()
        if key not in HL_SWEEP_BEST or HL_SWEEP_BEST[key] != value
    }

    return SweepResult(
        name=name,
        pnl=pnl,
        trades=total_trades,
        formal=formal,
        adaptive=adaptive,
        win_rate=win_rate,
        exits=dict(exit_counts),
        overrides=diff_keys,
    )


def _exit_bucket(exits: dict[str, int], *reasons: str) -> int:
    return sum(exits.get(reason, 0) for reason in reasons)


SWEEP_CSV_FIELDS = [
    "rank",
    "name",
    "pnl",
    "trades",
    "formal",
    "adaptive",
    "win_rate_pct",
    "exit_tp",
    "exit_sl",
    "exit_thesis_decay",
    "exit_session_end",
    "exit_flip",
    "exit_other",
    "overrides_json",
]


def _result_to_csv_row(rank: int, row: SweepResult) -> dict[str, Any]:
    return {
        "rank": rank,
        "name": row.name,
        "pnl": round(row.pnl, 2),
        "trades": row.trades,
        "formal": row.formal,
        "adaptive": row.adaptive,
        "win_rate_pct": round(row.win_rate * 100, 1),
        "exit_tp": _exit_bucket(row.exits, "take_profit_close_proxy"),
        "exit_sl": _exit_bucket(row.exits, "stop_loss_close_proxy"),
        "exit_thesis_decay": _exit_bucket(row.exits, "thesis_decay_exit"),
        "exit_session_end": _exit_bucket(row.exits, "session_end_proxy"),
        "exit_flip": _exit_bucket(row.exits, "flip_confirmed"),
        "exit_other": sum(
            count
            for reason, count in row.exits.items()
            if reason
            not in (
                "take_profit_close_proxy",
                "stop_loss_close_proxy",
                "thesis_decay_exit",
                "session_end_proxy",
                "flip_confirmed",
            )
        ),
        "overrides_json": json.dumps(row.overrides, sort_keys=True),
    }


def _write_sweep_csv(path: Path, results: list[SweepResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=SWEEP_CSV_FIELDS)
        writer.writeheader()
        for rank, row in enumerate(results, start=1):
            writer.writerow(_result_to_csv_row(rank, row))


def _write_sweep_json(path: Path, results: list[SweepResult]) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "name": row.name,
                    "pnl": round(row.pnl, 2),
                    "trades": row.trades,
                    "formal": row.formal,
                    "adaptive": row.adaptive,
                    "win_rate_pct": round(row.win_rate * 100, 1),
                    "exits": row.exits,
                    "overrides": row.overrides,
                }
                for row in results
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


async def run_sweep(
    output_dir: Path | None = None,
    *,
    config_builder: Callable[[], Iterator[tuple[str, dict[str, Any]]] | list[tuple[str, dict[str, Any]]]] = build_sweep_configs,
    output_stem: str = "strategy_replay_sweep_36_50",
    baseline_name: str = "baseline_hl_sweep_best",
    gc_every: int = 0,
    write_json: bool = True,
) -> tuple[list[SweepResult], str]:
    load_config = StrategyReplayConfig(**HL_SWEEP_BEST)
    parsed_sessions, hl_caches, hl_candle_cache, selected = await _load_sessions(
        load_config
    )
    reports = load_reports_index()
    reports_by_pair = build_reports_by_pair(reports)

    results: list[SweepResult] = []
    for index, (name, overrides) in enumerate(config_builder()):
        results.append(
            _run_config(
                name,
                overrides,
                parsed_sessions,
                hl_caches,
                hl_candle_cache,
                reports_by_pair,
            )
        )
        if gc_every and (index + 1) % gc_every == 0:
            gc.collect()

    results.sort(key=lambda row: row.pnl, reverse=True)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_sweep_csv(output_dir / f"{output_stem}.csv", results)
        if write_json:
            _write_sweep_json(output_dir / f"{output_stem}.json", results)

    return results, baseline_name


def _print_table(
    results: list[SweepResult],
    baseline_pnl: float,
    *,
    top_n: int = 40,
) -> None:
    print(f"Sweep: sessions 36-50 | configs={len(results)}")
    print(
        f"{'Rank':<5} {'Name':<32} {'PnL':>10} {'Δ base':>9} "
        f"{'Trades':>7} {'Win%':>6} {'TP':>4} {'SL':>4} {'Decay':>5} {'End':>4}"
    )
    print("-" * 95)
    display = results[:top_n]
    if len(results) > top_n:
        print(f"(showing top {top_n} of {len(results)})")
    for rank, row in enumerate(display, start=1):
        delta = row.pnl - baseline_pnl
        print(
            f"{rank:<5} {row.name:<32} ${row.pnl:+9.2f} ${delta:+8.2f} "
            f"{row.trades:>7} {row.win_rate * 100:5.1f}% "
            f"{_exit_bucket(row.exits, 'take_profit_close_proxy'):>4} "
            f"{_exit_bucket(row.exits, 'stop_loss_close_proxy'):>4} "
            f"{_exit_bucket(row.exits, 'thesis_decay_exit'):>5} "
            f"{_exit_bucket(row.exits, 'session_end_proxy'):>4}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="MACD+BB strategy replay config sweep")
    parser.add_argument(
        "--refine",
        action="store_true",
        help="Run refine grid around adaptive_bb_loose + SL/TP (sessions 36-50)",
    )
    parser.add_argument(
        "--mega",
        action="store_true",
        help="Run large factorial sample sweep (560+ combos, sessions 36-50)",
    )
    parser.add_argument(
        "--min-configs",
        type=int,
        default=560,
        help="Minimum configs for --mega (default 560, ~10x coarse sweep)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for --mega sampling",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        help="Number of top rows to print",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
        help="Directory for CSV/JSON sweep output",
    )
    args = parser.parse_args()

    if args.mega:
        mega_iter = lambda: iter_mega_sweep_configs(
            min_configs=args.min_configs,
            seed=args.seed,
        )
        results, baseline_name = asyncio.run(
            run_sweep(
                output_dir=args.output_dir,
                config_builder=mega_iter,
                output_stem="strategy_replay_mega_36_50",
                baseline_name="mega_baseline_hl_sweep_best",
                gc_every=25,
                write_json=False,
            )
        )
        output_file = args.output_dir / "strategy_replay_mega_36_50.csv"
        print(
            f"Mega sweep space size: {_mega_space_size():,} | "
            f"sampled: {len(results)} | JSON skipped (CSV only)"
        )
    elif args.refine:
        results, baseline_name = asyncio.run(
            run_sweep(
                output_dir=args.output_dir,
                config_builder=build_refine_sweep_configs,
                output_stem="strategy_replay_refine_36_50",
                baseline_name="refine_anchor_bb_loose",
            )
        )
        output_file = args.output_dir / "strategy_replay_refine_36_50.csv"
    else:
        results, baseline_name = asyncio.run(
            run_sweep(output_dir=args.output_dir)
        )
        output_file = args.output_dir / "strategy_replay_sweep_36_50.csv"

    baseline = next(
        (row for row in results if row.name == baseline_name),
        results[-1],
    )
    _print_table(results, baseline.pnl, top_n=args.top)
    print(f"\nWrote {output_file}")
    if results:
        winner = results[0]
        print(f"\nTop config: {winner.name}  PnL=${winner.pnl:+.2f}  overrides={json.dumps(winner.overrides, sort_keys=True)}")


if __name__ == "__main__":
    main()
