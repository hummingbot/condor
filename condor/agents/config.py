"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

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


def apply_overrides(
    config: Mapping[str, Any], overrides: Mapping[str, Any]
) -> dict[str, Any]:
    """``config`` with ``overrides`` applied as a **patch**, not a replacement.

    A start request names the few settings its dialog offers and inherits the
    rest, so a key it does not mention must survive — including a key *inside*
    a block it does mention. ``dict.update`` cannot express that: it replaces a
    whole value, so a dialog sending three of a loop's risk limits deleted
    every other limit that loop had configured (SH-2). The LP operator lost
    ``min_wallet_sol_reserve`` and ``max_open_slots`` on every start this way,
    with the UI reporting the start as successful — the limits looked saved and
    were not there.

    Nested mappings are merged one level at a time; anything else replaces. The
    rule is general rather than a list naming ``risk_limits``, because the
    property wanted is "a patch does not delete what it does not mention", and
    a rule that has to be extended for each new nested block is a rule that
    will be forgotten for one. A scalar, a list and a mapping arriving over a
    non-mapping all replace outright: those are statements of what the value
    *is*, and only two mappings describe the same shape well enough to merge.

    Deliberately NOT used by the config editor's route, which sends the whole
    document and whose deletions must therefore stand as deletions — merging
    there would make a removed key un-removable (see ``update_strategy_config``
    and ``StrategyConfigEditor``'s own dropped-key warning). Patch semantics
    belong to the partial payload, not to every write.
    """
    merged = dict(config)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = apply_overrides(current, value)
        else:
            merged[key] = value
    return merged


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


#: Config keys denominated in seconds, whose confirmation line should also
#: spell out minutes/hours (FEAT-192) — a raw ``frequency_sec: 1800`` means
#: nothing to a human deciding whether to approve a chat-driven edit.
_SECONDS_FIELDS = frozenset({"frequency_sec", "tick_timeout_sec"})
#: Config keys denominated in the loop's quote currency.
_QUOTE_FIELDS = frozenset(
    {
        "total_amount_quote",
        "max_position_size_quote",
        "max_drift_quote",
        "canvas_band_usd",
        "capital_amount",
    }
)
#: Config keys already a percentage; -1 is this family's shared "disabled".
_PERCENT_FIELDS = frozenset({"max_drawdown_pct", "shutdown_drawdown_pct"})


def _human_duration(seconds: float) -> str:
    """``seconds`` as the coarsest round unit it divides evenly into."""
    whole = int(seconds)
    if whole <= 0:
        return "0s"
    if whole % 3600 == 0:
        return f"{whole // 3600}h"
    if whole % 60 == 0:
        return f"{whole // 60}m"
    minutes, secs = divmod(whole, 60)
    return f"{minutes}m{secs}s" if minutes else f"{secs}s"


def humanize_config_value(dotted_key: str, value: Any) -> str:
    """One config value in the unit a human reads, not just the one it is stored in.

    ``dotted_key`` is the key as a loop-update diff names it (``frequency_sec``,
    or ``risk_limits.max_drawdown_pct`` for a nested one) — only the last segment
    decides the unit, since ``AgentConfig`` and ``RiskLimitsConfig`` do not share
    a field name.
    """
    bare = dotted_key.rsplit(".", 1)[-1]
    if value is None:
        return "(not set)"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if bare in _SECONDS_FIELDS:
            return f"{value:g}s ({_human_duration(value)})"
        if bare in _QUOTE_FIELDS:
            return f"{value:g} quote"
        if bare in _PERCENT_FIELDS:
            return "disabled" if value < 0 else f"{value:g}%"
        # No known unit (e.g. ``max_leverage``, a bare multiplier): still
        # trimmed of a numeric type's trailing ``.0`` so the diff reads the
        # way a human would type it.
        return f"{value:g}"
    return str(value)


#: The loop fields (as ``manage_loops``' own wire arguments name them) a chat
#: update can change outside ``config`` — compared as scalars, never diffed by
#: sub-key the way ``config`` is.
_LOOP_SCALAR_FIELDS: tuple[str, ...] = ("name", "description", "agent_key")


def _diff_config_values(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """Dotted old→new lines for a ``config``/``default_config`` comparison.

    A key ``old`` has and ``new`` omits is reported as ``(removed)`` rather
    than silently matching old-equals-new — callers that merge the incoming
    config onto the current one before diffing (``update_strategy``,
    ``update_strategy_config``) never trigger this; it only fires for a
    caller that genuinely replaces the dict whole. A nested dict
    (``risk_limits``) is compared per sub-key, dotted, the same way
    :func:`config_drift` already reports one.
    """
    lines: list[str] = []
    for key in sorted(set(old) | set(new)):
        old_v, new_v = old.get(key), new.get(key)
        if isinstance(old_v, Mapping) or isinstance(new_v, Mapping):
            old_d = old_v if isinstance(old_v, Mapping) else {}
            new_d = new_v if isinstance(new_v, Mapping) else {}
            for sub in sorted(set(old_d) | set(new_d)):
                dotted = f"{key}.{sub}"
                sub_old, sub_new = old_d.get(sub), new_d.get(sub)
                if sub_old == sub_new:
                    continue
                old_text = (
                    "(not set)"
                    if sub not in old_d
                    else humanize_config_value(dotted, sub_old)
                )
                new_text = (
                    "(removed)"
                    if sub not in new_d
                    else humanize_config_value(dotted, sub_new)
                )
                lines.append(f"{dotted}: {old_text} → {new_text}")
            continue
        if old_v == new_v:
            continue
        old_text = "(not set)" if key not in old else humanize_config_value(key, old_v)
        new_text = "(removed)" if key not in new else humanize_config_value(key, new_v)
        lines.append(f"{key}: {old_text} → {new_text}")
    return lines


def describe_loop_update(
    current: Mapping[str, Any], incoming: Mapping[str, Any]
) -> list[str]:
    """Old→new lines for a chat-driven ``manage_loops(action="update")`` (FEAT-192).

    ``current`` is the loop's stored record, keyed the way ``manage_loops``
    itself names its arguments (``name``, ``description``, ``instructions``,
    ``agent_key``, ``skills``, ``config``). ``incoming`` is the same shape,
    carrying only the keys this particular call actually sent — a key it omits
    never appears here, since ``update_strategy`` leaves a field it was not
    given untouched. ``config`` is the caller's own merge of the incoming
    values onto the loop's *effective* current config (``load_full_config``,
    which a saved ``config.yml`` overrides ``default_config`` on) — the
    omitted-key-is-removed reading of :func:`_diff_config_values` only fires
    for a hand-built ``current``/``incoming`` pair that genuinely drops one.

    This is what a human reads in the confirmation prompt before approving the
    write — the only place it is shown to anyone.
    """
    lines: list[str] = []
    for field in _LOOP_SCALAR_FIELDS:
        if field in incoming and incoming[field] != current.get(field):
            old = current.get(field) or "(none)"
            lines.append(f"{field}: {old} → {incoming[field]}")
    if "instructions" in incoming and incoming["instructions"] != current.get(
        "instructions"
    ):
        lines.append("instructions: (playbook text changed)")
    if "skills" in incoming and incoming["skills"] != current.get("skills"):
        old_skills = current.get("skills") or []
        lines.append(f"skills: {old_skills} → {incoming['skills']}")
    if "config" in incoming and incoming["config"]:
        lines.extend(
            _diff_config_values(current.get("config") or {}, incoming["config"])
        )
    return lines


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

    # Validate core fields and fill in any missing AgentConfig defaults. A
    # value already on disk can be invalid (e.g. a correction writes a bad
    # field, then a crash or a cancelled chat turn leaves it there) — fall
    # back to bare defaults rather than raising, so a caller merging a
    # correction on top of this result (``update_strategy``) can still load
    # and overwrite the bad value instead of being blocked by it.
    try:
        core = AgentConfig.from_dict(result)
    except ValidationError:
        core = AgentConfig()
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
