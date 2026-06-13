from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

PresetValue = float | int | bool

PRESET_OVERRIDES: dict[str, dict[str, PresetValue]] = {
    "safe": {
        "activation_ticks": 8,
        "adaptive_long_bb_pos_max": 46.0,
        "adaptive_short_bb_pos_min": 74.0,
        "adaptive_strong_long_bb_pos_max": 33.0,
        "adaptive_strong_short_bb_pos_min": 87.0,
        "adaptive_min_macd_gap_ratio": 0.10,
        "adaptive_min_hist_ratio": 0.16,
        "adaptive_score_open_min": 2.55,
        "adaptive_score_open_min_extreme": 2.30,
        "adaptive_hist_sign_bonus": 0.35,
        "adaptive_hist_sign_penalty": 0.40,
        "adaptive_momentum_bonus": 0.15,
        "adaptive_momentum_penalty": 0.15,
    },
    "balanced": {
        "activation_ticks": 6,
        "adaptive_long_bb_pos_max": 48.0,
        "adaptive_short_bb_pos_min": 72.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 85.0,
        "adaptive_min_macd_gap_ratio": 0.08,
        "adaptive_min_hist_ratio": 0.12,
        "adaptive_score_open_min": 2.40,
        "adaptive_score_open_min_extreme": 2.15,
        "adaptive_hist_sign_bonus": 0.35,
        "adaptive_hist_sign_penalty": 0.35,
        "adaptive_momentum_bonus": 0.20,
        "adaptive_momentum_penalty": 0.10,
    },
    "opportunistic": {
        "activation_ticks": 4,
        "adaptive_long_bb_pos_max": 55.0,
        "adaptive_short_bb_pos_min": 65.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 2.10,
        "adaptive_score_open_min_extreme": 1.85,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
    },
    "replay_probe": {
        "activation_ticks": 4,
        "time_window_min": 90,
        "adaptive_long_bb_pos_max": 90.0,
        "adaptive_short_bb_pos_min": 55.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 1.00,
        "adaptive_score_open_min_extreme": 0.75,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
    },
    # Sessions 36-48 sweep winner (+$148.39): sl2.4/tp10/ne32 + tighter adaptive gates.
    "hl_sweep_best": {
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
    },
    # Sessions 36-50 refine sweep winner (+$186.29): hl_sweep_best + loose adaptive BB gates.
    "hl_bb_loose_best": {
        "activation_ticks": 1,
        "sl_pct": 2.4,
        "tp_pct": 10.0,
        "thesis_decay_exit_ticks": 32,
        "thesis_bb_drift_pts": 25.0,
        "adaptive_long_bb_pos_max": 75.0,
        "adaptive_short_bb_pos_min": 65.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 85.0,
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
    },
    # Sessions 36-50 mega sweep winner (+$199.33): sl1.8/tp10/td16 + wide BB gates.
    "hl_mega_sweep_best": {
        "activation_ticks": 1,
        "sl_pct": 1.8,
        "tp_pct": 10.0,
        "thesis_decay_exit_ticks": 16,
        "thesis_bb_drift_pts": 35.0,
        "adaptive_long_bb_pos_max": 80.0,
        "adaptive_short_bb_pos_min": 78.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 92.0,
        "adaptive_min_macd_gap_ratio": 0.08,
        "adaptive_min_hist_ratio": 0.16,
        "adaptive_score_open_min": 1.00,
        "adaptive_score_open_min_extreme": 0.75,
        "adaptive_hist_sign_bonus": 0.25,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.20,
        "adaptive_momentum_penalty": 0.05,
        "bb_proximity_epsilon_pct": 0.15,
        "ignore_adaptive_4h_filter": True,
        "adaptive_requires_flat": False,
        "max_open_executors": 3,
        "min_tradeable_count": 1,
        "sl_cooldown_ticks": 2,
        "flip_cooldown_ticks": 8,
    },
}

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def resolve_config_with_preset(config: ConfigT) -> ConfigT:
    """Apply a named preset profile on top of the submitted config.

    When preset is not ``custom``, keys defined in PRESET_OVERRIDES always win
    over form/default values (the UI sends every field, so exclude_unset would
    not help). Fields outside the preset dict — e.g. session_nums, sl_pct —
    still come from the form.
    """
    preset = getattr(config, "preset", "custom")
    if preset == "custom":
        return config
    overrides = PRESET_OVERRIDES.get(preset)
    if not overrides:
        return config
    config_type = type(config)
    return config_type(**{**config.model_dump(), **overrides, "preset": preset})
