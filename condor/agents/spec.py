"""AgentSpec: validation, freezing, canonicalization, and spec hashes (§5.3).

The Agent + Strategy collapse makes ``AGENT.md`` the ONE spec source: identity
+ strategy body + ``default_config`` + ``denomination`` + optional
``schedule``. This module owns what happens at ``run()``:

- **freeze**: one validated effective spec (model, resolved account, schedule,
  risk, tools, prompt) is computed from AGENT.md defaults + launch overrides.
  Risk overrides are STRICTER-ONLY — widening any baseline cap is rejected.
- **two hashes**: ``source_spec_hash`` (authored AGENT.md bytes) and
  ``resolved_spec_hash`` (frozen effective spec incl. resolved AccountRef and
  materialized defaults). A ``default_account`` change alters only the latter.
- **canonicalization**: the resolved hash is computed over a canonical typed
  serialization — UTF-8 JSON, sorted keys, no insignificant whitespace, all
  schema defaults materialized, numbers as normalized decimal strings, null
  fields omitted, credentials never present.
- **schedule schema** (§5.4 — schema only in Phase 2): cron + optional IANA
  timezone; a schedule REQUIRES a bounded duration (``max_ticks`` > 0) so
  every later fire cannot overlap-skip forever.
- **denomination**: a spec with risk limits but no ``denomination`` fails
  validation — risk caps need a numeraire (§6.1 converts into it).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from condor.accounts.model import AccountRef


class SpecValidationError(ValueError):
    """An AgentSpec (or launch override) failed validation."""


# ---------------------------------------------------------------------------
# Canonicalization + hashes
# ---------------------------------------------------------------------------


def _canonical_value(v: Any) -> Any:
    """Normalize one value for canonical serialization.

    Numbers become normalized decimal STRINGS so 500, 500.0, and "500.00"
    hash identically; bools stay bools; None is handled by the caller
    (omitted); enums/paths fall back to ``str``.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v)).normalize()
        # Decimal('5E+2') → '500': re-render scientific notation as plain.
        return format(d, "f")
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return {
            str(k): _canonical_value(val)
            for k, val in sorted(v.items(), key=lambda kv: str(kv[0]))
            if val is not None
        }
    if isinstance(v, (list, tuple)):
        return [_canonical_value(x) for x in v]
    if isinstance(v, AccountRef):
        return _canonical_value(v.as_dict())
    return str(v)


def canonical_json(obj: dict) -> str:
    """Canonical serialization: UTF-8 JSON, keys sorted, no insignificant
    whitespace, numbers as normalized decimal strings, nulls omitted."""
    return json.dumps(
        _canonical_value(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def source_spec_hash(agent_md_text: str) -> str:
    """Hash of the AUTHORED spec — the exact AGENT.md bytes."""
    return hashlib.sha256(agent_md_text.encode("utf-8")).hexdigest()


def resolved_spec_hash(frozen: dict) -> str:
    """Hash of the frozen EFFECTIVE spec (canonical serialization)."""
    return hashlib.sha256(canonical_json(frozen).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Schedule schema (§5.4 — Phase 2 ships the SCHEMA; execution lands Phase 4)
# ---------------------------------------------------------------------------

_CRON_FIELD = re.compile(r"^(\*|\d+)(-\d+)?(/\d+)?(,(\*|\d+)(-\d+)?(/\d+)?)*$")
_CRON_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def validate_cron(expr: str) -> None:
    """Validate a standard 5-field cron expression (minute hour dom month dow)."""
    fields = expr.split()
    if len(fields) != 5:
        raise SpecValidationError(
            f"schedule.cron must have 5 fields (minute hour dom month dow), "
            f"got {len(fields)}: {expr!r}"
        )
    for f_, (lo, hi) in zip(fields, _CRON_RANGES):
        if not _CRON_FIELD.match(f_):
            raise SpecValidationError(f"invalid cron field {f_!r} in {expr!r}")
        for num in re.findall(r"\d+", f_):
            if not (lo <= int(num) <= hi):
                raise SpecValidationError(
                    f"cron field value {num} out of range [{lo},{hi}] in {expr!r}"
                )


def validate_schedule(schedule: dict, default_config: dict) -> None:
    """Validate a ``schedule:`` block. A schedule requires a bounded duration
    (``max_ticks`` > 0 in the same spec) — an unbounded looping agent would
    make every later fire overlap-skip forever."""
    if not isinstance(schedule, dict):
        raise SpecValidationError("schedule must be a mapping {cron, tz?}")
    cron = schedule.get("cron")
    if not cron:
        raise SpecValidationError("schedule requires a 'cron' expression")
    validate_cron(str(cron))
    tz = schedule.get("tz", "UTC")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(str(tz))
    except Exception:
        raise SpecValidationError(f"schedule.tz is not an IANA timezone: {tz!r}")
    unknown = set(schedule) - {"cron", "tz"}
    if unknown:
        raise SpecValidationError(f"unknown schedule fields: {sorted(unknown)}")
    max_ticks = int((default_config or {}).get("max_ticks", 0) or 0)
    if max_ticks <= 0:
        raise SpecValidationError(
            "a scheduled agent requires a bounded duration: set max_ticks > 0 "
            "in default_config (schedule-without-bound would overlap-skip "
            "forever)"
        )


# ---------------------------------------------------------------------------
# Spec-level validation
# ---------------------------------------------------------------------------


def validate_agent_spec(agent) -> None:
    """Cross-field validation of an authored Agent (AGENT.md).

    - risk limits declared → ``denomination`` required (the numeraire the
      caps are expressed in, §6.1);
    - ``schedule`` → valid cron/tz + bounded duration.
    Called on save (AgentStore) and at freeze time.
    """
    if agent.risk_limits and not (agent.denomination or "").strip():
        raise SpecValidationError(
            f"Agent '{agent.slug}' declares risk_limits but no denomination. "
            "Say the numeraire the limits are expressed in — e.g. "
            "`denomination: USDC` (or SOL, USD) in AGENT.md frontmatter."
        )
    if agent.schedule:
        validate_schedule(agent.schedule, agent.default_config or {})


# ---------------------------------------------------------------------------
# Risk override merging: stricter-only
# ---------------------------------------------------------------------------

# Caps where a LOWER value is stricter. Drawdown fields use -1 as the
# "disabled" sentinel — enabling (disabled → any bound) is stricter,
# disabling (bound → -1) or raising the bound is widening.
_LOWER_IS_STRICTER = {"max_position_size_quote", "max_open_executors"}
_DRAWDOWN_FIELDS = {"max_drawdown_pct", "shutdown_drawdown_pct"}


def merge_risk_stricter(baseline: dict, override: dict | None) -> dict:
    """Merge a launch risk override into the baseline, rejecting widening.

    Every overridden cap must be equal or stricter than the baseline value;
    a widening override raises :class:`SpecValidationError` naming the field.
    """
    merged = dict(baseline or {})
    for key, val in (override or {}).items():
        if key not in merged:
            merged[key] = val  # baseline silent on this cap → override binds
            continue
        base = merged[key]
        if key in _LOWER_IS_STRICTER:
            if float(val) > float(base):
                raise SpecValidationError(
                    f"risk override widens {key}: {val} > baseline {base} "
                    "(launch overrides may only tighten risk)"
                )
        elif key in _DRAWDOWN_FIELDS:
            base_f, val_f = float(base), float(val)
            base_disabled, val_disabled = base_f == -1.0, val_f == -1.0
            if base_disabled and not val_disabled:
                pass  # enabling a drawdown stop is stricter
            elif val_disabled and not base_disabled:
                raise SpecValidationError(
                    f"risk override disables {key} (baseline {base}) — "
                    "launch overrides may only tighten risk"
                )
            elif not val_disabled and val_f > base_f:
                raise SpecValidationError(
                    f"risk override widens {key}: {val} > baseline {base} "
                    "(launch overrides may only tighten risk)"
                )
        else:
            # Unknown cap key: let RiskLimitsConfig's extra="forbid" fail it
            # downstream rather than silently accepting a typo here.
            merged[key] = val
            continue
        merged[key] = val
    return merged


# ---------------------------------------------------------------------------
# Freezing
# ---------------------------------------------------------------------------


@dataclass
class FrozenSpec:
    """The one validated effective spec a run executes — stored with the run."""

    agent_slug: str
    model: str
    denomination: str
    instructions: str
    config: dict[str, Any]  # full effective launch config (risk merged)
    tools: list[str] = field(default_factory=list)
    schedule: dict | None = None
    account_ref: AccountRef | None = None
    source_hash: str = ""
    resolved_hash: str = ""

    def to_public_dict(self) -> dict:
        """The hashable/persistable view — never contains credentials."""
        d: dict[str, Any] = {
            "agent_slug": self.agent_slug,
            "model": self.model,
            "denomination": self.denomination,
            "instructions": self.instructions,
            "config": self.config,
            "tools": list(self.tools),
        }
        if self.schedule:
            d["schedule"] = self.schedule
        if self.account_ref is not None:
            d["account_ref"] = self.account_ref.as_dict()
        return d


def freeze_spec(
    agent,
    launch_config: dict[str, Any],
    *,
    source_text: str = "",
    account_ref: AccountRef | None = None,
) -> FrozenSpec:
    """Freeze one validated effective spec for a run.

    ``launch_config`` is the ALREADY-MERGED effective config (defaults +
    overrides via ``lifecycle.start_session``); risk stricter-only enforcement
    happens in the merge step (``merge_risk_stricter``) before this is called.
    ``account_ref`` is the resolved account (None until the account store is
    activated in Phase 3 — resolution is dormant, §6.2b phasing).
    """
    validate_agent_spec(agent)
    frozen = FrozenSpec(
        agent_slug=agent.slug,
        model=launch_config.get("agent_key") or agent.agent_key,
        denomination=(agent.denomination or "").strip(),
        instructions=agent.instructions,
        config=dict(launch_config),
        tools=list(agent.tools or []),
        schedule=dict(agent.schedule) if agent.schedule else None,
        account_ref=account_ref,
        source_hash=source_spec_hash(source_text) if source_text else "",
    )
    frozen.resolved_hash = resolved_spec_hash(frozen.to_public_dict())
    return frozen


def agent_venue(agent) -> str:
    """The venue an agent trades on — fixed AGENT identity, not a per-launch
    override (``venue`` is not a launch-overridable key, §5.3), so every path
    (session, consult, experiment) reads it the same way. Declared in
    ``default_config.venue``; ``solana`` is the default."""
    return str((getattr(agent, "default_config", None) or {}).get("venue") or "solana")


def resolve_agent_account(agent) -> AccountRef:
    """Resolve the exact custody identity for a trading run.

    An authored ``agent.account`` is already a canonical custody address.
    Empty means the venue default is intentionally late-bound once, here, at
    run freeze time. The returned AccountRef is then carried by the frozen
    spec and capability; executor requests never re-resolve a default.

    Venue comes from the agent's identity (:func:`agent_venue`), NOT a launch
    config — a consult task never reaches into ``default_config`` for it
    (Q2.1).
    """
    from condor.executors.wallets import account_store

    selector = (getattr(agent, "account", "") or "").strip() or None
    return account_store().resolve(agent_venue(agent), selector)
