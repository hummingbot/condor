"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLimitsConfig(BaseModel):
    # Strict: a typo'd or unknown risk key must fail loudly, not silently
    # widen the effective limits to schema defaults.
    model_config = ConfigDict(extra="forbid")

    # ge=0: zero is a legitimate deny-all baseline (watcher agents); negatives
    # are nonsense.
    max_position_size_quote: float = Field(
        default=500.0, ge=0, description="Max total position size in quote currency"
    )
    max_open_executors: int = Field(
        default=5, ge=0, description="Max simultaneous executors"
    )
    max_drawdown_pct: float = Field(
        default=-1.0,
        description="Max drawdown %% that pauses (soft-blocks) ticks; -1 = disabled",
    )
    shutdown_drawdown_pct: float = Field(
        default=-1.0,
        description="Max drawdown %% that triggers an emergency winddown "
        "(closes positions per shutdown.md); -1 = disabled",
    )

    @field_validator("max_drawdown_pct", "shutdown_drawdown_pct")
    @classmethod
    def _drawdown_range(cls, v: float) -> float:
        if v == -1.0:  # disabled sentinel
            return v
        if not (0 < v <= 100):
            raise ValueError("drawdown pct must be -1 (disabled) or in (0, 100]")
        return v


class AgentConfig(BaseModel):
    server_name: str = Field(default="local", description="Hummingbot API server name")
    agent_key: str = Field(
        default="",
        description="LLM model to use (e.g. 'claude-code', 'ollama:llama3.1'). Empty = use strategy default.",
    )
    model_base_url: str = Field(
        default="",
        description="Custom base URL for OpenAI-compatible endpoints (LM Studio, vLLM). Leave empty for standard providers.",
    )
    total_amount_quote: float = Field(
        default=100.0,
        description="Total capital budget for this session in quote currency",
    )
    frequency_sec: int = Field(default=60, description="Tick frequency in seconds")
    trading_context: str = Field(
        default="",
        description="Natural language session context that guides the agent's trading decisions",
    )
    execution_mode: Literal["experiment", "run_once", "loop"] = Field(
        default="loop",
        description="Execution mode: experiment (simulate — a.k.a. dry run), "
        "run_once (single live tick), loop (continuous)",
    )
    max_ticks: int = Field(
        default=0, description="Max ticks before auto-stop; 0 = unlimited"
    )
    keep_position_on_stop: bool = Field(
        default=True,
        description="On plain stop: True (default) detaches — positions stay "
        "open, unmanaged; False closes them out. Liquidation is otherwise "
        "reserved for the explicit shutdown escalation.",
    )
    bot_name: str = Field(
        default="",
        description="If set, the agent operates this Hummingbot bot's controllers "
        "(deploy/retune) instead of creating standalone executors, and the bot's "
        "PnL is merged into the agent's reported performance. Empty = executor mode.",
    )
    risk_limits: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)

    def to_engine_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by TickEngine."""
        d = self.model_dump()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        """Create from a raw dict (e.g. strategy.default_config)."""
        cleaned = {k: v for k, v in d.items() if k in cls.model_fields}
        # Translate the dry_run shorthand (users still say "dry run") → experiment
        if d.get("dry_run") and "execution_mode" not in d:
            cleaned["execution_mode"] = "experiment"
        return cls(**cleaned)


def load_agent_config(
    agent_dir: Path, defaults: dict[str, Any] | None = None
) -> AgentConfig:
    """Load config from config.yml in the agent directory, falling back to defaults."""
    config_path = agent_dir / "config.yml"
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
            return AgentConfig(**data)
        except Exception:
            pass
    if defaults:
        return AgentConfig.from_dict(defaults)
    return AgentConfig()


def save_agent_config(agent_dir: Path, config: AgentConfig) -> None:
    """Save config to config.yml in the agent directory."""
    config_path = agent_dir / "config.yml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False, sort_keys=False)
    )


def normalize_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize a launch config: validate core fields, fill schema defaults.

    Preserves strategy-specific extra keys alongside the validated AgentConfig
    fields. Strategies are pure templates with no config.yml of their own
    (refactor-01b), so there is nothing to load from disk — the caller passes
    the merged defaults (strategy default_config + risk baseline seeding).
    """
    result = dict(config or {})

    # Validate core fields and fill in any missing AgentConfig defaults
    core = AgentConfig.from_dict(result)
    core_defaults = core.model_dump()
    for k, v in core_defaults.items():
        result.setdefault(k, v)

    return result


def merge_launch_config(
    base: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    """Deep-merge a launch override into normalized defaults, then re-validate.

    A shallow ``update`` let a partial nested override (e.g. ``risk_limits``
    with only ``max_drawdown_pct``) replace the seeded baseline object wholesale
    — the missing keys then silently reverted to schema defaults (500/5),
    broadening risk limits. Nested dicts merge key-by-key instead, and the FINAL
    object is validated (strict risk_limits: unknown keys and out-of-range
    values raise)."""
    result = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            merged = dict(result[k])
            merged.update(v)
            result[k] = merged
        else:
            result[k] = v
    # Validates all core fields, including risk_limits via RiskLimitsConfig
    # (extra="forbid" + range constraints). Raises pydantic.ValidationError.
    AgentConfig.from_dict(result)
    return result


def save_full_config(agent_dir: Path, config: dict[str, Any]) -> None:
    """Save a raw config dict as YAML (no filtering through AgentConfig)."""
    config_path = agent_dir / "config.yml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
