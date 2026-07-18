"""Unified domain *Agent* model + discovery/CRUD store.

An **Agent** is a specialized domain agent with an identity, domain knowledge,
a tool allowlist, an ``agent_key`` (its default model) and its own
memory/skills store (keyed by the directory slug — the "brain"). Since the
Agent + Strategy collapse (simplification plan §5.3) the ``AGENT.md`` is the
ONE spec: identity + strategy body + ``default_config`` + ``denomination``
(the numeraire its risk limits are expressed in) + optional ``schedule``.
An Agent:

- is **consultable** (CONSULT mode: run its own brain to completion → answer), and
- is **runnable** (RUN mode: ``TickEngine`` loops its spec).

Disk layout::

    agents/{slug}/
        AGENT.md                       # the spec (frontmatter + body)
        skills/<slug>/SKILL.md         # shared skills (consult + runs)
        store/                         # learned memory (the shared brain)
        sessions/  experiments/  delegations/   # history (journal.py)

An Agent may be **authored in the repo** (e.g. ``memecoin_trender``) or
**created at runtime**; either way ``AgentStore`` can create/update/delete it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from condor.memory.store import _parse_frontmatter

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "agents"


def agents_data_root() -> Path:
    """Root of the on-disk agent tree (``agents/`` at repo root)."""
    return _DATA_ROOT


def _slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug.

    Example: "RIVER Scalper v2" -> "river_scalper_v2"
    """
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "_", s)
    return s.strip("_") or "unnamed"


def _render_frontmatter(meta: dict, body: str) -> str:
    """Render YAML frontmatter + markdown body."""
    frontmatter = yaml.dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


@dataclass
class Agent:
    slug: str  # directory name == agent_slug for the domain store
    name: str
    description: str = ""
    instructions: str = ""  # AGENT.md body: identity + domain knowledge + strategy
    agent_key: str = ""  # default model (ACP key, e.g. "claude-code")
    # Declared tool scope, enforced on BOTH consult and loop (scope_gate for
    # system mutations, can_trade for the risk baseline requirement).
    # Names match full (``mcp__condor__manage_skill``) or short (``manage_skill``).
    # Empty => UNRESTRICTED (all discovered tools) — and therefore trading.
    tools: list[str] = field(default_factory=list)
    when_to_consult: str = ""  # empty => not offered as consultable
    # Agent-level risk baseline (RiskLimits keys). Governs unattended delegations
    # (zero-seeded risk_gate) and is the baseline for tick sessions (launch
    # overrides may only TIGHTEN it, §5.3). The AGENT.md defines what the agent
    # does — an agent that can trade MUST declare a baseline (enforced on save);
    # {max_position_size_quote: 0, max_open_executors: 0} is the explicit
    # "read-only, never trades" statement.
    risk_limits: dict = field(default_factory=dict)
    # The numeraire the risk limits are expressed in (e.g. "USDC", "SOL",
    # "USD") — REQUIRED whenever risk_limits are declared (§5.3/§6.1).
    denomination: str = ""
    # Canonical custody address selected for this agent. At authoring time a
    # display-name selector is resolved and this field is rewritten to the
    # address, so later renames cannot change or break execution. Empty means
    # resolve the venue's default account at run freeze time.
    account: str = ""
    account_label: str = ""
    # Launch defaults (the former strategy default_config): AgentConfig keys.
    # The run-only "cadence" sub-block — frequency_sec/execution_mode/max_ticks
    # — is what only a session (run_agent) uses; the rest (venue, guardrails,
    # executor tactic defaults) is shared by any trading verb (see AgentConfig
    # and docs/consult-delegate-merge.md, Q2.2).
    default_config: dict = field(default_factory=dict)
    # Default trading context injected when a launch passes none.
    default_trading_context: str = ""
    # Optional unattended schedule: {cron: "0 * * * *", tz: "UTC"} (§5.4).
    # Schema-validated on save; scheduler EXECUTION lands in Phase 4.
    schedule: dict = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def agent_dir(self) -> Path:
        return _DATA_ROOT / self.slug

    @property
    def routines_dir(self) -> Path:
        """Agent-level routines directory."""
        return self.agent_dir / "routines"

    @property
    def path(self) -> Path:
        return self.agent_dir / "AGENT.md"

    # The one tool that places/stops live executors. An agent that lists it can
    # move real capital and MUST carry a risk baseline (#4).
    _TRADING_TOOL = "manage_executors"

    @property
    def can_trade(self) -> bool:
        """True if this agent can reach the executor tool: it declares
        ``manage_executors``, or declares no tool list at all (empty =
        unrestricted — the full surface includes the executor tool). Such an
        agent MUST declare a risk baseline — enforced on save and at
        delegation (#4)."""
        if not self.tools:
            return True
        return any(
            t == self._TRADING_TOOL or t.endswith(f"__{self._TRADING_TOOL}")
            for t in self.tools
        )

    @property
    def consultable(self) -> bool:
        """DERIVED capability: any Agent with a non-empty consult trigger.

        Consult works on any model — there is no separate "expert" kind of
        agent. Every mutation is gated by the user's confirmation (consult.py).
        """
        return bool(self.when_to_consult)


def _load_agent_from_dir(agent_dir: Path) -> Agent | None:
    """Load an Agent from ``<agent_dir>/AGENT.md`` (any dir with the file)."""
    path = agent_dir / "AGENT.md"
    if not path.exists():
        return None
    try:
        meta, body = _parse_frontmatter(path.read_text())
        return Agent(
            slug=agent_dir.name,
            name=meta.get("name", agent_dir.name),
            description=meta.get("description", ""),
            instructions=body,
            agent_key=meta.get("agent_key", ""),
            tools=meta.get("tools", []) or [],
            when_to_consult=meta.get("when_to_consult", ""),
            risk_limits=meta.get("risk_limits", {}) or {},
            denomination=meta.get("denomination", "") or "",
            account=meta.get("account", "") or "",
            account_label=meta.get("account_label", "") or "",
            default_config=meta.get("default_config", {}) or {},
            default_trading_context=meta.get("default_trading_context", "") or "",
            schedule=meta.get("schedule", {}) or {},
            created_at=meta.get("created_at", ""),
        )
    except Exception:
        log.exception("Failed to load agent from %s", path)
        return None


class AgentStore:
    """Discovery + CRUD for Agents under ``agents/*/AGENT.md``.

    Every directory with an ``AGENT.md`` is an Agent; whether it is
    consultable/runnable is derived from its definition.
    """

    def get(self, slug: str) -> Agent | None:
        if not slug:
            return None
        return _load_agent_from_dir(_DATA_ROOT / slug)

    def source_text(self, slug: str) -> str:
        """The authored AGENT.md bytes (the ``source_spec_hash`` input)."""
        path = _DATA_ROOT / slug / "AGENT.md"
        try:
            return path.read_text()
        except FileNotFoundError:
            return ""

    def list_all(self) -> list[Agent]:
        agents: list[Agent] = []
        for d in self._iter_agent_dirs():
            a = _load_agent_from_dir(d)
            if a is not None:
                agents.append(a)
        return agents

    def list_consultable_index(self) -> str:
        """Injectable index — one line per *consultable* Agent (mirrors SKILLS).

        Empty string when none are consultable, so callers inject nothing.
        """
        lines = [
            f"- [{a.slug}] {a.when_to_consult or a.description}"
            for a in self.list_all()
            if a.consultable
        ]
        return "\n".join(lines)

    def create(
        self,
        name: str,
        description: str = "",
        instructions: str = "",
        agent_key: str = "",
        tools: list[str] | None = None,
        when_to_consult: str = "",
        risk_limits: dict | None = None,
        denomination: str = "",
        account: str = "",
        account_label: str = "",
        default_config: dict | None = None,
        default_trading_context: str = "",
        schedule: dict | None = None,
    ) -> Agent:
        agent = Agent(
            slug=_slugify(name),
            name=name,
            description=description,
            instructions=instructions,
            agent_key=agent_key,
            tools=tools or [],
            when_to_consult=when_to_consult,
            risk_limits=risk_limits or {},
            denomination=denomination,
            account=account,
            account_label=account_label,
            default_config=default_config or {},
            default_trading_context=default_trading_context,
            schedule=schedule or {},
        )
        self._save(agent)
        log.info("Created agent %s (dir: %s)", agent.name, agent.slug)
        return agent

    def update(self, agent: Agent) -> None:
        self._save(agent)

    def delete(self, slug: str) -> bool:
        agent_dir = _DATA_ROOT / slug
        path = agent_dir / "AGENT.md"
        if not path.exists():
            return False
        path.unlink()
        # Remove the whole dir only when nothing else lives there (no store or
        # skills) — the brain/history must not be silently dropped.
        try:
            if not any(agent_dir.iterdir()):
                agent_dir.rmdir()
        except Exception:
            pass
        log.info("Deleted agent %s", slug)
        return True

    def _save(self, agent: Agent) -> None:
        # The AGENT.md defines what the agent does — for an agent that can
        # trade that includes how much it may do unattended. Refusing to save
        # an incomplete definition moves the loud error from delegate time to
        # authoring time.
        if agent.can_trade and not agent.risk_limits:
            why = (
                "owns manage_executors"
                if agent.tools
                else "declares no tool scope (unrestricted ⇒ trading)"
            )
            raise ValueError(
                f"Agent '{agent.slug}' {why} but declares no risk_limits "
                "baseline. Say the numbers in AGENT.md — use "
                "{'max_position_size_quote': 0, 'max_open_executors': 0} "
                "for a read-only agent that must never trade."
            )
        # Cross-field spec validation (§5.3/§5.4): risk limits need a
        # denomination; a schedule needs a valid cron/tz and a bounded
        # duration.
        from condor.agents.spec import validate_agent_spec

        validate_agent_spec(agent)
        if agent.account:
            # Persist the canonical custody address, never a display-name
            # selector. Venue deployment is part of AccountRef identity.
            from condor.executors.wallets import account_store

            venue_id = str((agent.default_config or {}).get("venue") or "solana")
            ref = account_store().resolve(venue_id, agent.account)
            if not agent.account_label:
                agent.account_label = agent.account
            agent.account = ref.custody_address
        meta = {
            "name": agent.name,
            "description": agent.description,
            "agent_key": agent.agent_key,
            "tools": agent.tools,
            "when_to_consult": agent.when_to_consult,
            "risk_limits": agent.risk_limits,
            "denomination": agent.denomination,
            "default_config": agent.default_config,
            "default_trading_context": agent.default_trading_context,
            "created_at": agent.created_at,
        }
        if agent.account:
            meta["account"] = agent.account
        if agent.account_label:
            meta["account_label"] = agent.account_label
        if agent.schedule:
            meta["schedule"] = agent.schedule
        agent.agent_dir.mkdir(parents=True, exist_ok=True)
        (agent.agent_dir / "AGENT.md").write_text(
            _render_frontmatter(meta, agent.instructions)
        )

    def _iter_agent_dirs(self):
        if not _DATA_ROOT.exists():
            return
        for d in sorted(_DATA_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "strategies":
                continue
            yield d
