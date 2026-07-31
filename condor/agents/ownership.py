"""Session-scoped bot ownership (FEAT-017).

An agent running in controller mode owns the bots it deploys, and the **name is
the proof**: every bot a given ``(agent, strategy)`` may touch lives under the
namespace ``{agent_slug}-{strategy_slug}``. Slugs never contain ``-``
(``strategy._slugify`` maps ``[\\s-]+ → _``), so ``-`` delimits the namespace
unambiguously and two different agents can never claim the same bot — uniqueness
is structural, with no registry, lock or lookup.

The namespace answers *who*. The per-session ledger (``owned_bots.json`` in the
session dir) answers *since when* — the instant a session took a bot over, which
a name cannot express and which per-session PnL attribution ([[FEAT-018]]) needs
to tell the session that deployed a long-lived bot from the one that adopted it
after a restart.

Enforcement lives at the tick's permission callback
(``risk.auto_approve_with_risk_check``), the same seam that already validates
``controller_id`` on executor creates.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

LEDGER_FILENAME = "owned_bots.json"

# Hummingbot deploys under an *instance* name with a timestamp appended to the
# requested name: "brigado-ema_trend-btc" → "brigado-ema_trend-btc-20260731-101500".
_DEPLOY_SUFFIX_RE = re.compile(r"-\d{8}-\d{6}$")

# How many refusals we keep — enough to show the model its recent mistakes on the
# next tick without growing the file unboundedly.
_MAX_VIOLATIONS = 20


def bot_namespace(agent_slug: str, strategy_slug: str) -> str:
    """Stable prefix every bot this (agent, strategy) may deploy under."""
    return f"{agent_slug}-{strategy_slug}"


def in_namespace(name: str, ns: str) -> bool:
    """True for the base itself, a tagged sibling, and any deployed instance.

    ``brigado-ema_trend``, ``brigado-ema_trend-btc``,
    ``brigado-ema_trend-btc-20260731-101500`` → all owned.
    """
    if not name or not ns:
        return False
    return name == ns or name.startswith(f"{ns}-")


def strip_deploy_suffix(name: str) -> str:
    """Drop the ``-YYYYMMDD-HHMMSS`` a deploy appends, leaving the asked-for name."""
    return _DEPLOY_SUFFIX_RE.sub("", name or "")


def resolve_bot_name(
    config: dict[str, Any], agent_slug: str, strategy_slug: str
) -> str:
    """Effective ``bot_name`` for a run — empty string means executor mode.

    ``bot_mode`` decouples the mode from the name without changing any existing
    agent's behavior:

    - ``auto`` (default): controller mode iff ``bot_name`` is non-empty. Exactly
      today's semantics, so nothing silently flips.
    - ``bot``: controller mode on; an empty ``bot_name`` is **derived** as the
      namespace (the default ``README`` has always promised).
    - ``executors``: force executor mode even with a ``bot_name`` set.
    """
    mode = (config.get("bot_mode") or "auto").strip()
    name = (config.get("bot_name") or "").strip()
    if mode == "executors":
        return ""
    if mode == "bot":
        return name or bot_namespace(agent_slug, strategy_slug)
    return name


def declared_names(config: dict[str, Any], namespace: str) -> list[str]:
    """Configured bot names that sit OUTSIDE the namespace — the legacy escape hatch.

    A ``bot_name`` written before this convention existed (e.g. ``river-scalper``)
    is owned by its agent even though the prefix doesn't prove it, so existing
    agents keep operating their bot with no config change.
    """
    name = (config.get("bot_name") or "").strip()
    if name and not in_namespace(name, namespace):
        return [name]
    return []


@dataclass
class OwnedBot:
    base: str  # the name as the agent asked for it (no deploy timestamp)
    origin: str  # "deployed" | "adopted" | "legacy"
    since: float  # epoch this session took ownership
    last_seen: float


def _parse_bots(data: dict[str, Any]) -> dict[str, OwnedBot]:
    """Decode the ``bots`` map of a serialized ledger, skipping corrupt entries."""
    out: dict[str, OwnedBot] = {}
    for base, raw in (data.get("bots") or {}).items():
        try:
            out[base] = OwnedBot(
                base=raw.get("base", base),
                origin=raw.get("origin", "adopted"),
                since=float(raw.get("since", 0.0)),
                last_seen=float(raw.get("last_seen", 0.0)),
            )
        except Exception:
            continue
    return out


def read_owned(session_dir: Path | None) -> list[OwnedBot]:
    """Bots a session recorded owning, oldest takeover first.

    Read-only view of ``{session_dir}/owned_bots.json`` for consumers outside the
    tick loop — per-session PnL attribution ([[FEAT-018]]) needs the takeover
    instants without instantiating the live ledger. Empty when the session
    predates the ledger or the file is unreadable, which callers read as "no
    evidence" and fall back on.
    """
    if session_dir is None:
        return []
    path = Path(session_dir) / LEDGER_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text()) or {}
    except Exception:
        log.warning("BotLedger: unreadable %s", path)
        return []
    return sorted(_parse_bots(data).values(), key=lambda b: (b.since, b.base))


class BotLedger:
    """Per-session record of the bots a session owns.

    Persisted to ``{session_dir}/owned_bots.json``; in-memory only when there is
    no session dir (experiments — see ``TickEngine.__post_init__``).
    """

    def __init__(
        self,
        namespace: str,
        session_dir: Path | None = None,
        declared: Iterable[str] = (),
    ) -> None:
        self.namespace = namespace
        self.session_dir = session_dir
        self.declared: list[str] = [d for d in declared if d]
        self.bots: dict[str, OwnedBot] = {}
        self.violations: list[dict[str, Any]] = []
        self._pending_violations: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self.session_dir / LEDGER_FILENAME if self.session_dir else None

    def owns(self, name: str) -> bool:
        """In-namespace, or under one of the explicitly declared legacy names."""
        name = (name or "").strip()
        if not name:
            return False
        if in_namespace(name, self.namespace):
            return True
        return any(in_namespace(name, d) for d in self.declared)

    def bases(self) -> list[str]:
        return sorted(self.bots)

    def owned(self) -> list[OwnedBot]:
        """Owned bots, oldest takeover first."""
        return sorted(self.bots.values(), key=lambda b: (b.since, b.base))

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def note_deploy(self, name: str, now: float | None = None) -> None:
        self._record(name, "deployed", now)

    def adopt(self, name: str, now: float | None = None) -> None:
        """Take over a bot that was already running (restart / handover).

        Never downgrades a bot this session deployed itself.
        """
        self._record(name, "adopted", now)

    def note_violation(self, name: str, action: str, now: float | None = None) -> None:
        entry = {
            "name": name or "",
            "action": action,
            "at": time.time() if now is None else now,
        }
        self.violations.append(entry)
        self.violations = self.violations[-_MAX_VIOLATIONS:]
        self._pending_violations.append(entry)
        self._save()

    def drain_violations(self) -> list[dict[str, Any]]:
        """Return refusals not yet journaled, clearing the pending list."""
        pending, self._pending_violations = self._pending_violations, []
        return pending

    def _record(self, name: str, origin: str, now: float | None) -> None:
        base = strip_deploy_suffix((name or "").strip())
        if not base:
            return
        ts = time.time() if now is None else now
        existing = self.bots.get(base)
        if existing is None:
            self.bots[base] = OwnedBot(base=base, origin=origin, since=ts, last_seen=ts)
        else:
            # First claim wins: a bot this session deployed stays "deployed" even
            # if a later adoption pass sees it running.
            existing.last_seen = ts
        self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "declared": list(self.declared),
            "bots": {k: asdict(v) for k, v in self.bots.items()},
            "violations": list(self.violations),
        }

    def _load(self) -> None:
        path = self.path
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text()) or {}
        except Exception:
            log.warning("BotLedger: unreadable %s, starting empty", path)
            return
        self.bots = _parse_bots(data)
        self.violations = list(data.get("violations") or [])[-_MAX_VIOLATIONS:]

    def _save(self) -> None:
        path = self.path
        if not path:
            return  # experiments: in-memory only
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2))
            tmp.replace(path)
        except Exception:
            log.warning("BotLedger: could not write %s", path, exc_info=True)
