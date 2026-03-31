"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class RiskLimitsConfig(BaseModel):
    max_position_size_quote: float = Field(default=500.0, description="Max total position size in quote currency")
    max_open_executors: int = Field(default=5, description="Max simultaneous executors")
    max_drawdown_pct: float = Field(default=-1.0, description="Max drawdown percentage; -1 = disabled")


class AgentConfig(BaseModel):
    server_name: str = Field(default="local", description="Hummingbot API server name")
    total_amount_quote: float = Field(default=100.0, description="Total capital budget for this session in quote currency")
    frequency_sec: int = Field(default=60, description="Tick frequency in seconds")
    trading_context: str = Field(default="", description="Natural language session context that guides the agent's trading decisions")
    execution_mode: Literal["dry_run", "run_once", "loop"] = Field(default="loop", description="Execution mode: dry_run (simulate), run_once (single live tick), loop (continuous)")
    risk_limits: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)

    def to_engine_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by TickEngine."""
        d = self.model_dump()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        """Create from a raw dict (e.g. strategy.default_config)."""
        cleaned = {k: v for k, v in d.items() if k in cls.model_fields}
        # Translate dry_run shorthand → execution_mode
        if d.get("dry_run") and "execution_mode" not in d:
            cleaned["execution_mode"] = "dry_run"
        return cls(**cleaned)


def load_agent_config(agent_dir: Path, defaults: dict[str, Any] | None = None) -> AgentConfig:
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
    config_path.write_text(yaml.dump(config.model_dump(), default_flow_style=False, sort_keys=False))
