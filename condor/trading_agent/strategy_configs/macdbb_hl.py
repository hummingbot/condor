"""Strategy parameters for macdbb_scanner_aggressive_hl."""

from __future__ import annotations

import types
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, Field

# Maps duration fields to effective tick keys injected at prompt time.
DURATION_EFFECTIVE_TICK_KEYS: dict[str, str] = {
    "neutral_pressure_activation_hours": "neutral_pressure_activation_ticks",
    "neutral_exit_hours": "neutral_exit_streak",
    "sl_symbol_cooldown_hours": "sl_symbol_cooldown_ticks",
    "flip_cooldown_hours": "flip_cooldown_ticks",
}

# Legacy tick-only params migrated using session frequency_sec when available.
LEGACY_TICK_TO_HOURS: dict[str, str] = {
    "neutral_pressure_activation_ticks": "neutral_pressure_activation_hours",
    "neutral_exit_streak": "neutral_exit_hours",
    "sl_symbol_cooldown_ticks": "sl_symbol_cooldown_hours",
    "flip_cooldown_ticks": "flip_cooldown_hours",
}


def _schema_type_name(annotation: Any) -> str:
    """Map a field annotation to a simple UI type name."""
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    origin = get_origin(annotation)
    if origin is Union or isinstance(annotation, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if args:
            return _schema_type_name(args[0])
    name = getattr(annotation, "__name__", None)
    if name and name != "NoneType":
        return name
    return "str"


class MacdbbScannerAggressiveHlParams(BaseModel):
    """Schema for MACD+BB Scanner Aggressive HL strategy parameters.

    Values are persisted in agent.md ``default_config.strategy_params`` via the UI.
    This model defines field types and descriptions only — no code-level defaults.
    """

    # Adaptive mode (duration-primary; effective ticks derived from frequency_sec)
    neutral_pressure_activation_hours: float | None = Field(
        default=None,
        description="Wall-clock hours of NEUTRAL-only agent ticks before adaptive mode activates",
        json_schema_extra={
            "group": "Adaptive mode",
            "duration": True,
            "effective_tick_key": "neutral_pressure_activation_ticks",
        },
    )
    min_tradeable_for_adaptive: int | None = Field(
        default=None,
        description="Minimum scanner tradeable pairs required for adaptive entries",
        json_schema_extra={"group": "Adaptive mode"},
    )
    adaptive_skip_4h_filter: bool | None = Field(
        default=None,
        description="Skip mandatory 4h filter for adaptive entries only (formal still requires 4h)",
        json_schema_extra={"group": "Adaptive mode"},
    )
    sl_symbol_cooldown_hours: float | None = Field(
        default=None,
        description="Wall-clock hours to skip adaptive entries on a symbol after its stop-loss",
        json_schema_extra={
            "group": "Adaptive mode",
            "duration": True,
            "effective_tick_key": "sl_symbol_cooldown_ticks",
        },
    )

    # Adaptive scoring
    adaptive_long_bb_pos_max: float | None = Field(
        default=None,
        description="Max BB% for adaptive long eligibility",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_short_bb_pos_min: float | None = Field(
        default=None,
        description="Min BB% for adaptive short eligibility",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_strong_long_bb_pos_max: float | None = Field(
        default=None,
        description="BB% at or below which long uses the lower score threshold",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_strong_short_bb_pos_min: float | None = Field(
        default=None,
        description="BB% at or above which short uses the lower score threshold",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_min_macd_gap_ratio: float | None = Field(
        default=None,
        description="Minimum MACD/signal gap ratio for adaptive entry",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_min_hist_ratio: float | None = Field(
        default=None,
        description="Minimum histogram ratio for adaptive entry",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_score_open_min: float | None = Field(
        default=None,
        description="Normal adaptive strength score required to open",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_score_open_min_extreme: float | None = Field(
        default=None,
        description="Lower score threshold when BB is at extreme displacement",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_hist_sign_bonus: float | None = Field(
        default=None,
        description="Score bonus when histogram sign aligns with direction",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_hist_sign_penalty: float | None = Field(
        default=None,
        description="Score penalty when histogram sign opposes direction",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_momentum_bonus: float | None = Field(
        default=None,
        description="Score bonus when momentum aligns with direction",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_momentum_penalty: float | None = Field(
        default=None,
        description="Score penalty when momentum opposes direction",
        json_schema_extra={"group": "Adaptive scoring"},
    )
    adaptive_tiebreak_score_delta: float | None = Field(
        default=None,
        description="Score gap within which higher scanner NATR wins tie-break",
        json_schema_extra={"group": "Adaptive scoring"},
    )

    # Formal triggers
    bb_proximity_epsilon_pct: float | None = Field(
        default=None,
        description="BB proximity tolerance % for formal price-at-band triggers",
        json_schema_extra={"group": "Formal triggers"},
    )

    # Scanner / queue
    scanner_top_n: int | None = Field(
        default=None,
        description="Number of pairs the market scanner ranks",
        json_schema_extra={"group": "Scanner & queue"},
    )
    scanner_lookback_hours: int | None = Field(
        default=None,
        description="1m candle lookback hours for the scanner",
        json_schema_extra={"group": "Scanner & queue"},
    )
    scanner_min_volume_usd: int | None = Field(
        default=None,
        description="Minimum 24h volume USD for scanner candidates",
        json_schema_extra={"group": "Scanner & queue"},
    )
    scanner_mature_count: int | None = Field(
        default=None,
        description="Primary mature candidates from scanner",
        json_schema_extra={"group": "Scanner & queue"},
    )
    scanner_degen_count: int | None = Field(
        default=None,
        description="Primary degen candidates from scanner",
        json_schema_extra={"group": "Scanner & queue"},
    )
    scanner_exclude_hip3: bool | None = Field(
        default=None,
        description="Exclude HIP-3 pairs from scanner results",
        json_schema_extra={"group": "Scanner & queue"},
    )
    min_scanner_analyzed: int | None = Field(
        default=None,
        description="Skip tick when scanner analyzes fewer than this many pairs",
        json_schema_extra={"group": "Scanner & queue"},
    )
    natr_floor_mature_pct: float | None = Field(
        default=None,
        description="Min NATR % when tape is mature-first",
        json_schema_extra={"group": "Scanner & queue"},
    )
    natr_floor_degen_pct: float | None = Field(
        default=None,
        description="Min NATR % when tape is degen-first",
        json_schema_extra={"group": "Scanner & queue"},
    )
    macd_queue_primary_size: int | None = Field(
        default=None,
        description="Max symbols in pass-1 MACD queue",
        json_schema_extra={"group": "Scanner & queue"},
    )
    macd_primary_review_count: int | None = Field(
        default=None,
        description="Max 1h MACD reviews per agent tick when queue is large",
        json_schema_extra={"group": "Scanner & queue"},
    )
    macd_queue_pass2_min: int | None = Field(
        default=None,
        description="Min extra symbols to add in pass-2 queue expansion",
        json_schema_extra={"group": "Scanner & queue"},
    )
    macd_queue_pass2_max: int | None = Field(
        default=None,
        description="Max extra symbols to add in pass-2 queue expansion",
        json_schema_extra={"group": "Scanner & queue"},
    )
    macd_queue_total_cap: int | None = Field(
        default=None,
        description="Hard cap on total queued symbols for MACD",
        json_schema_extra={"group": "Scanner & queue"},
    )

    # Entry barriers
    sl_pct: float | None = Field(
        default=None,
        description="Stop-loss % on triple barrier",
        json_schema_extra={"group": "Entry barriers"},
    )
    tp_pct: float | None = Field(
        default=None,
        description="Take-profit % on triple barrier",
        json_schema_extra={"group": "Entry barriers"},
    )
    leverage: int | None = Field(
        default=None,
        description="Position leverage multiplier",
        json_schema_extra={"group": "Entry barriers"},
    )
    create_max_retries: int | None = Field(
        default=None,
        description="Retries after margin errors (initial attempt is extra)",
        json_schema_extra={"group": "Entry barriers"},
    )

    # Position monitor (duration-primary)
    neutral_exit_hours: float | None = Field(
        default=None,
        description="Wall-clock hours of NEUTRAL 1h MACD readings before closing a RUNNING leg",
        json_schema_extra={
            "group": "Position monitor",
            "duration": True,
            "effective_tick_key": "neutral_exit_streak",
        },
    )
    flip_cooldown_hours: float | None = Field(
        default=None,
        description="Wall-clock hours after a flip before allowing another flip on the symbol",
        json_schema_extra={
            "group": "Position monitor",
            "duration": True,
            "effective_tick_key": "flip_cooldown_ticks",
        },
    )

    @classmethod
    def get_fields(cls) -> dict[str, dict[str, Any]]:
        """Field metadata for UI display (types and descriptions only)."""
        fields: dict[str, dict[str, Any]] = {}
        for name, field_info in cls.model_fields.items():
            entry: dict[str, Any] = {
                "type": _schema_type_name(field_info.annotation),
                "description": field_info.description or name,
            }
            extra = field_info.json_schema_extra
            if isinstance(extra, dict):
                if "group" in extra:
                    entry["group"] = extra["group"]
                if "widget" in extra:
                    entry["widget"] = extra["widget"]
                if extra.get("duration"):
                    entry["duration"] = True
                    if "effective_tick_key" in extra:
                        entry["effective_tick_key"] = extra["effective_tick_key"]
            fields[name] = entry
        return fields

    @classmethod
    def get_computed_fields(cls) -> dict[str, str]:
        """Map duration config keys to effective tick keys shown in prompts."""
        return dict(DURATION_EFFECTIVE_TICK_KEYS)

    @classmethod
    def get_groups(cls) -> list[str]:
        """Ordered unique group names for UI sections."""
        seen: list[str] = []
        for field_info in cls.model_fields.values():
            extra = field_info.json_schema_extra
            if isinstance(extra, dict) and "group" in extra:
                group = str(extra["group"])
                if group not in seen:
                    seen.append(group)
        return seen
