"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from condor.fsutil import atomic_write_text


class RiskLimitsConfig(BaseModel):
    max_position_size_quote: float = Field(
        default=500.0, description="Max total position size in quote currency"
    )
    max_open_executors: int = Field(default=5, description="Max simultaneous executors")
    max_drawdown_pct: float = Field(
        default=-1.0,
        description="Max drawdown %% that pauses (soft-blocks) ticks; -1 = disabled",
    )
    shutdown_drawdown_pct: float = Field(
        default=-1.0,
        description="Max drawdown %% that triggers an emergency winddown "
        "(closes positions per shutdown.md); -1 = disabled",
    )
    max_drift_quote: float = Field(
        default=-1.0,
        description="Largest book-vs-venue drift (quote) this agent's own "
        "controllers may show before new exposure is refused; brakes always "
        "pass. -1 = disabled",
    )
    max_leverage: float = Field(
        default=-1.0,
        description="Most leverage a create may ask for, and the highest this "
        "session may set on an account. With it enabled, a create that "
        "declares no leverage is refused (the venue's own default applies to "
        "an omitted one). -1 = disabled",
    )


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
    tick_timeout_sec: int = Field(
        default=0,
        description="Wall-clock budget for one tick's agent session, in seconds; "
        "0 = use the runtime default (10 min, or CONDOR_TIMEOUT_TICK_DEFAULT)",
    )
    trading_context: str = Field(
        default="",
        description="Natural language session context that guides the agent's trading decisions",
    )
    execution_mode: Literal["dry_run", "run_once", "loop"] = Field(
        default="loop",
        description="Execution mode: dry_run (simulate), run_once (single live tick), loop (continuous)",
    )
    max_ticks: int = Field(
        default=0, description="Max ticks before auto-stop; 0 = unlimited"
    )
    restart_on_boot: bool = Field(
        default=False,
        description="Resume this loop after Condor restarts. The boot pass "
        "(condor.runtime.loops) marks an interrupted run and, only with this "
        "set, starts a FRESH session from the config as it stands then. Off by "
        "default: a trading loop that resumes unattended after a crash nobody "
        "noticed is a decision its owner has to make per strategy.",
    )
    bot_name: str = Field(
        default="",
        description="If set, the agent operates this Hummingbot bot's controllers "
        "(deploy/retune) instead of creating standalone executors, and the bot's "
        "PnL is merged into the agent's reported performance. Empty = executor mode.",
    )
    bot_mode: Literal["auto", "executors", "bot"] = Field(
        default="auto",
        description="auto = controller mode iff bot_name is set (default); "
        "bot = controller mode on, deriving bot_name as {agent_slug}-{strategy_slug} "
        "when empty; executors = force executor mode even with a bot_name set.",
    )
    canvas_enabled: bool = Field(
        default=True,
        description="Keep a session canvas (the agent's running narrative) and "
        "publish a live session report. Costs ~1.2k input tokens per tick.",
    )
    canvas_nudge_ticks: int = Field(
        default=12,
        description="Remind the agent to revise its canvas after this many ticks "
        "without a revision",
    )
    canvas_band_usd: float = Field(
        default=25.0,
        description="PnL drift since the last canvas revision that triggers a "
        "revise-your-canvas nudge",
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
        # Translate dry_run shorthand → execution_mode
        if d.get("dry_run") and "execution_mode" not in d:
            cleaned["execution_mode"] = "dry_run"
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
    atomic_write_text(
        config_path,
        yaml.dump(config.model_dump(), default_flow_style=False, sort_keys=False),
    )


def load_full_config(
    agent_dir: Path, defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Load config preserving both AgentConfig fields and strategy-specific keys.

    Starts from strategy defaults, overlays saved config.yml, then validates
    core fields via AgentConfig and merges defaults for any missing core fields.
    """
    result = dict(defaults or {})

    config_path = agent_dir / "config.yml"
    if config_path.exists():
        try:
            saved = yaml.safe_load(config_path.read_text()) or {}
            result.update(saved)
        except Exception:
            pass

    # Validate core fields and fill in any missing AgentConfig defaults
    core = AgentConfig.from_dict(result)
    core_defaults = core.model_dump()
    for k, v in core_defaults.items():
        result.setdefault(k, v)

    return result


def save_full_config(agent_dir: Path, config: dict[str, Any]) -> None:
    """Save a raw config dict as YAML (no filtering through AgentConfig)."""
    config_path = agent_dir / "config.yml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        config_path, yaml.dump(config, default_flow_style=False, sort_keys=False)
    )


# ── Confidential run config (Condor Vaults, plan §6 hooks 1 and 2) ──
#
# A vault's overlay reaches the engine as ordinary run-config keys: the tunables
# the manager keeps private, listed under ``confidential``, and the private
# trading context as ``system_prompt``. The tick still sees the tunables (they
# render into [CURRENT CONFIG] like any other key) — what changes is what the
# run leaves on disk. Three surfaces write the config or the prompt that
# contains it, and all three go through these helpers: the session's
# ``config.yml`` (``persistable_config``), the snapshot files
# (``redact_confidential`` at write time) and the dashboard's snapshot read
# (``redact_confidential`` again, by key name, for files already on disk).

#: Keys that are confidential on every run, listed or not. ``system_prompt``
#: is the vault's private context; it never renders into the tick prompt and
#: never persists.
ALWAYS_CONFIDENTIAL_KEYS: tuple[str, ...] = ("system_prompt",)

CONFIDENTIAL_MARK = "[confidential]"


def confidential_keys(config: dict[str, Any]) -> tuple[str, ...]:
    """Every key this run treats as confidential, ``system_prompt`` included.

    ``confidential`` must be a list of non-empty strings when present; anything
    else is refused with a ValueError rather than read as "nothing is
    confidential", because a typo there would persist the overlay in clear.
    """
    listed = config.get("confidential")
    keys: list[str] = list(ALWAYS_CONFIDENTIAL_KEYS)
    if listed is None:
        return tuple(keys)
    if not isinstance(listed, list) or not all(
        isinstance(k, str) and k.strip() for k in listed
    ):
        raise ValueError(
            "config key `confidential` must be a list of key names, got " f"{listed!r}"
        )
    for key in listed:
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def persistable_config(config: dict[str, Any]) -> dict[str, Any]:
    """The run config minus every confidential value, for ``config.yml``.

    The ``confidential`` list itself stays: it is the record of *which* keys
    were withheld, which the dashboard's snapshot read needs to redact by name.
    """
    keys = set(confidential_keys(config))
    return {k: v for k, v in config.items() if k not in keys}


def redact_confidential(text: str, keys: Iterable[str]) -> str:
    """Replace every ``key: value`` line of a confidential key with ``key: [confidential]``.

    Matches the line the tick prompt renders for a config key (``f"{k}: {v}"``),
    at any indentation, so the same call serves the snapshot file at write time
    and the dashboard read afterwards. A key that never rendered leaves the text
    untouched.
    """
    for key in keys:
        pattern = re.compile(
            rf"^(?P<indent>[ \t]*){re.escape(key)}:(?:[ \t].*)?$", re.MULTILINE
        )
        text = pattern.sub(rf"\g<indent>{key}: {CONFIDENTIAL_MARK}", text)
    return text


#: A system-prompt line shorter than this is not scrubbed from a snapshot: a
#: three-word line ("Never call it otherwise.") would hit ordinary prose, and
#: the prompt's substance lives in its long lines.
MIN_VERBATIM_LINE = 24


def redact_verbatim(text: str, secrets: Iterable[str]) -> str:
    """Replace every verbatim line of ``secrets`` found in ``text`` with the mark.

    The second half of hook 1. The system prompt never enters the tick prompt,
    but the model that read it can quote it back — in its reply, in a journal
    line, in a tool argument — and all three land in the snapshot. Scrubbing
    each of the prompt's lines wherever it recurs word for word removes the
    echo; a paraphrase is the exhaust plan §10.5 accepts and the vault page
    discloses.
    """
    for secret in secrets:
        for line in (secret or "").splitlines():
            line = line.strip()
            if len(line) >= MIN_VERBATIM_LINE and line in text:
                text = text.replace(line, CONFIDENTIAL_MARK)
    return text
