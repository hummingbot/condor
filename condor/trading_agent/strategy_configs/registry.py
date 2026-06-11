"""Registry mapping agent slugs to strategy parameter models."""

from __future__ import annotations

from typing import Any, Type

from pydantic import BaseModel

from .macdbb_hl import (
    DURATION_EFFECTIVE_TICK_KEYS,
    LEGACY_TICK_TO_HOURS,
    MacdbbScannerAggressiveHlParams,
)

STRATEGY_CONFIG_REGISTRY: dict[str, Type[BaseModel]] = {
    "macdbb_scanner_aggressive_hl": MacdbbScannerAggressiveHlParams,
}


def duration_to_ticks(hours: float, frequency_sec: int) -> int:
    """Convert wall-clock hours to agent tick count."""
    freq = max(1, int(frequency_sec))
    return max(1, round(float(hours) * 3600 / freq))


def ticks_to_hours(ticks: int | float, frequency_sec: int) -> float:
    """Convert legacy tick count to hours at the given tick frequency."""
    freq = max(1, int(frequency_sec))
    return round(float(ticks) * freq / 3600, 4)


def migrate_legacy_tick_params(
    params: dict[str, Any] | None,
    frequency_sec: int,
) -> dict[str, Any]:
    """Convert old *_ticks fields to *_hours when hours are missing."""
    if not params:
        return {}
    migrated = dict(params)
    ref_freq = max(1, int(frequency_sec))

    for legacy_tick_key, hours_key in LEGACY_TICK_TO_HOURS.items():
        if legacy_tick_key not in migrated:
            continue
        if hours_key in migrated:
            migrated.pop(legacy_tick_key, None)
            continue
        legacy_value = migrated.pop(legacy_tick_key)
        try:
            migrated[hours_key] = ticks_to_hours(legacy_value, ref_freq)
        except (TypeError, ValueError):
            pass

    return migrated


def get_strategy_config_class(slug: str) -> Type[BaseModel] | None:
    return STRATEGY_CONFIG_REGISTRY.get(slug)


def get_strategy_config_defaults(slug: str) -> dict[str, Any]:
    """Deprecated: defaults live in agent.md default_config.strategy_params (UI)."""
    return {}


def merge_strategy_params(
    slug: str,
    params: dict[str, Any] | None,
    frequency_sec: int,
) -> dict[str, Any]:
    """Return saved strategy_params with legacy tick migration only."""
    _ = slug  # reserved for per-strategy migration hooks
    return migrate_legacy_tick_params(params, frequency_sec)


def resolve_effective_strategy_params(
    slug: str,
    params: dict[str, Any] | None,
    frequency_sec: int,
) -> dict[str, Any]:
    """Merge params and inject duration-derived effective tick thresholds."""
    freq = max(1, int(frequency_sec))
    merged = merge_strategy_params(slug, params, freq)
    if not merged:
        return {}

    resolved = dict(merged)
    if slug == "macdbb_scanner_aggressive_hl":
        for hours_key, tick_key in DURATION_EFFECTIVE_TICK_KEYS.items():
            hours_value = resolved.get(hours_key)
            if hours_value is not None:
                resolved[tick_key] = duration_to_ticks(hours_value, freq)

    resolved["frequency_sec"] = freq
    resolved["tick_interval_hours"] = round(freq / 3600, 4)
    return resolved


def get_strategy_config_schema(
    slug: str,
    saved_defaults: dict[str, Any] | None = None,
    frequency_sec: int | None = None,
) -> dict[str, Any] | None:
    config_class = get_strategy_config_class(slug)
    if config_class is None:
        return None
    get_fields = getattr(config_class, "get_fields", None)
    get_groups = getattr(config_class, "get_groups", None)
    get_computed = getattr(config_class, "get_computed_fields", None)
    if not callable(get_fields):
        return None

    freq = max(1, int(frequency_sec or 60))
    defaults = migrate_legacy_tick_params(saved_defaults or {}, freq)

    return {
        "fields": get_fields(),
        "groups": get_groups() if callable(get_groups) else [],
        "defaults": defaults,
        "computed_fields": get_computed() if callable(get_computed) else {},
    }
