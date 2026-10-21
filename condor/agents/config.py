"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from condor.fsutil import atomic_write_text

# The execution modes that run a single tick as an experiment: no journal, the
# tick captured as a dry-run snapshot instead. The one definition of that set —
# AgentConfig.execution_mode below is the Literal naming every mode.
EXPERIMENT_MODES: frozenset[str] = frozenset({"dry_run", "run_once"})


def is_experiment_mode(mode: str) -> bool:
    """True for an execution mode that runs as an experiment (dry_run/run_once)."""
    return mode in EXPERIMENT_MODES


# The fields a *session* pins rather than inherits: the Hummingbot API server it
# trades on and the LLM it thinks with. One definition, because a restart has to
# carry exactly these across from the dead session while re-reading everything
# else from the strategy's config as it stands now (CORR-728) — and a set that
# is re-guessed per call site is a set that diverges.
PIN_FIELDS: tuple[str, ...] = ("server_name", "agent_key", "model_base_url")


def pin_snapshot(config: Mapping[str, Any]) -> dict[str, Any]:
    """Just the pins of ``config``, for recording and for overlaying.

    An empty pin is dropped, not recorded as empty: "" means *inherit* for
    ``agent_key`` and ``model_base_url``, so carrying it over as a value would
    pin the absence of a pin and shadow a strategy default set since.
    """
    return {
        name: config[name] for name in PIN_FIELDS if config.get(name) not in (None, "")
    }


#: What an effective-vs-file diff compares: the fields a session pins, plus the
#: risk limits it is enforcing. Deliberately not every key — a cosmetic edit to
#: ``trading_context`` must not badge a running session as drifted — and
#: deliberately a set this module already owns, so the drift set and the overlay
#: set cannot diverge (CORR-728).
DRIFT_FIELDS: tuple[str, ...] = (*PIN_FIELDS, "risk_limits")


def _comparable(value: Any) -> Any:
    """``value`` as something comparable: a pydantic model as its dict."""
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value


def config_drift(
    running: Mapping[str, Any], on_disk: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Which of :data:`DRIFT_FIELDS` a running session no longer shares with the file.

    ``{field: {"running": <what the process holds>, "file": <what config.yml
    says now>}}``, empty when the two agree. Both values, not a boolean: what
    an operator needs is what a restart would change, and a badge that only
    says "drifted" sends them back to the logs this exists to replace.

    A dict field (``risk_limits``) is compared per sub-key and reported dotted
    (``risk_limits.max_leverage``), so one changed limit reads as one changed
    limit rather than as two blobs to diff by eye.

    A field ``running`` does not carry at all is skipped rather than reported
    as drifted against the file: that is a caller who did not read it, not a
    session that disagrees.

    An engine copies its config at start and nothing swaps it mid-tick on
    purpose (CORR-732), so this is the only way to see a session still trading
    on a pin the file has since changed, short of reading the logs.
    """
    drift: dict[str, dict[str, Any]] = {}
    for name in DRIFT_FIELDS:
        if name not in running:
            continue
        live = _comparable(running.get(name))
        filed = _comparable(on_disk.get(name))
        if isinstance(live, dict) or isinstance(filed, dict):
            live_d = live if isinstance(live, dict) else {}
            file_d = filed if isinstance(filed, dict) else {}
            for key in sorted(set(live_d) | set(file_d)):
                if live_d.get(key) != file_d.get(key):
                    drift[f"{name}.{key}"] = {
                        "running": live_d.get(key),
                        "file": file_d.get(key),
                    }
        elif live != filed:
            drift[name] = {"running": live, "file": filed}
    return drift


def load_session_pins(session_dir: Path) -> dict[str, Any]:
    """The pins a session recorded in its own ``config.yml``.

    The per-session copy ``save_full_config`` writes at session creation is the
    only place a start request's overrides land, so it is what a restart reads
    for a session whose status file predates the recorded snapshot. Unreadable
    or absent reads as "no pins", never as an error: the restart still happens,
    on the strategy's config.
    """
    path = Path(session_dir) / "config.yml"
    try:
        saved = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}
    if not isinstance(saved, dict):
        return {}
    return pin_snapshot(saved)


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


class TradingRequirementsConfig(BaseModel):
    """What a loop needs in place before it can trade (FEAT-139).

    Read by :mod:`condor.preflight`, which checks each stated requirement
    against the venue and the account *before* a start commits capital. Every
    field is optional and an empty ``connector`` means "nothing stated": a loop
    that declares no requirements has nothing to check against, so the preflight
    reports what it can and refuses nothing. Only a requirement written here can
    fail a start, which is what keeps a working loop safe from a false negative.
    """

    connector: str = Field(
        default="",
        description="Venue this loop trades (e.g. 'binance', 'binance_perpetual'). "
        "Empty = no trading requirements stated, and the preflight checks none.",
    )
    trading_pairs: list[str] = Field(
        default_factory=list,
        description="Pairs this loop trades; the venue's rules are reported per pair",
    )
    account_name: str = Field(
        default="",
        description="Account holding the capital. Empty = master_account",
    )
    capital_asset: str = Field(
        default="",
        description="The EXACT asset the capital must be in (e.g. 'USDC'). Empty = "
        "the quote currency of the first configured pair",
    )
    capital_amount: float = Field(
        default=-1.0,
        description="Units of capital_asset that must be available; -1 = use "
        "total_amount_quote when capital_asset is the pairs' quote currency",
    )
    position_mode: str = Field(
        default="",
        description="Position mode the account must be in ('HEDGE' or 'ONEWAY'); "
        "empty = no requirement",
    )
    leverage: float = Field(
        default=-1.0,
        description="Leverage the account must be set to; -1 = no requirement",
    )


class AgentConfig(BaseModel):
    # Empty, not ``"local"``: a default that is a concrete server *name* is not
    # an absent setting — it was a name that then fell through the resolution
    # chain, so "no server configured" and "configured for the server called
    # local" were the same state and an install whose server is named anything
    # else silently traded wherever resolution landed (CORR-729). Empty means
    # unset, and resolution chooses; a config that really names ``local`` still
    # says so on disk and is honoured as the explicit setting it is.
    server_name: str = Field(default="", description="Hummingbot API server name")
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
    trading_requirements: TradingRequirementsConfig = Field(
        default_factory=TradingRequirementsConfig
    )

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
