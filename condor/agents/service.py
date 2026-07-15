"""AgentService — the one owner of agent CRUD + lifecycle (§5.2).

Web routes, MCP tools, and control-socket handlers are thin adapters over
this service. It composes what already existed — ``AgentStore`` (CRUD),
``lifecycle`` (run/pause/resume/stop/shutdown), ``consult``/``delegate`` —
and owns the guard logic that used to be duplicated per surface.

Errors are :class:`condor.agents.lifecycle.LifecycleError` (HTTP-ish status)
so every transport maps them the same way.

``delete`` is a **tombstone**, not an erase (§5.2):

- rejected while the agent has nonterminal state (running engines or
  nonterminal executor records);
- history (sessions, executor records) is preserved and stays readable;
- the slug is RESERVED — a future ``create`` cannot re-acquire the old
  attribution;
- launches and editing are disabled.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

import yaml

from condor.agents.agent import Agent, AgentStore, agents_data_root
from condor.agents.lifecycle import (
    LifecycleError,
    apply_verb,
    list_instances,
    select_engines,
    start_session,
)

log = logging.getLogger(__name__)

_TOMBSTONE_FILE = "TOMBSTONE.yml"


def _tombstone_path(slug: str):
    return agents_data_root() / slug / _TOMBSTONE_FILE


class AgentService:
    """Facade over the agent stores + engine lifecycle. Stateless — safe to
    construct per call."""

    def __init__(self, store: AgentStore | None = None):
        self.store = store or AgentStore()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, slug: str) -> Agent:
        agent = self.store.get(slug)
        if agent is None:
            raise LifecycleError(404, f"Agent '{slug}' not found")
        return agent

    def list(self, include_tombstoned: bool = False) -> list[Agent]:
        agents = self.store.list_all()
        if include_tombstoned:
            return agents
        return [a for a in agents if not self.is_tombstoned(a.slug)]

    def create(self, **fields) -> Agent:
        # A tombstoned slug is reserved forever — a recreated agent must never
        # inherit (or launder away) the old one's financial history.
        from condor.agents.agent import _slugify

        name = fields.get("name", "")
        slug = _slugify(name)
        if self.is_tombstoned(slug):
            raise LifecycleError(
                409,
                f"slug '{slug}' belonged to a deleted agent and is reserved "
                "(its financial history is preserved) — pick a different name",
            )
        if self.store.get(slug) is not None:
            raise LifecycleError(409, f"Agent '{slug}' already exists")
        try:
            return self.store.create(**fields)
        except ValueError as e:
            raise LifecycleError(422, str(e))

    def update(self, slug: str, patch: dict[str, Any]) -> Agent:
        self._reject_tombstoned(slug, "edit")
        agent = self.get(slug)
        allowed = {
            "name",
            "description",
            "instructions",
            "agent_key",
            "tools",
            "when_to_consult",
            "server_required",
            "server_name",
            "risk_limits",
            "denomination",
            "default_config",
            "default_trading_context",
            "schedule",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise LifecycleError(422, f"unknown agent fields: {sorted(unknown)}")
        for k, v in patch.items():
            setattr(agent, k, v)
        try:
            self.store.update(agent)
        except ValueError as e:
            raise LifecycleError(422, str(e))
        return agent

    def delete(self, slug: str, reason: str = "") -> dict:
        """Tombstone the agent (§5.2). History survives; the slug is reserved."""
        agent = self.get(slug)
        if self.is_tombstoned(slug):
            return {"deleted": True, "already": True}

        # Guard 1: running engines (any live run of this agent).
        from condor.agents.engine import get_all_engines

        running = [
            e.agent_id
            for e in get_all_engines().values()
            if e.agent.slug == slug and e.is_running
        ]
        if running:
            raise LifecycleError(
                409,
                f"agent '{slug}' has running sessions {running} — stop them "
                "before deleting",
            )

        # Guard 2: nonterminal executor records (open orders/positions still
        # attributed to this agent — deleting would orphan financial state).
        open_execs = self._nonterminal_executors(slug)
        if open_execs:
            raise LifecycleError(
                409,
                f"agent '{slug}' still has {len(open_execs)} nonterminal "
                f"executor(s) {open_execs[:5]} — stop/close them first",
            )

        tomb = {
            "tombstoned_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason or "deleted by operator",
            "name": agent.name,
        }
        _tombstone_path(slug).write_text(yaml.dump(tomb, sort_keys=False))
        log.info("Agent %s tombstoned", slug)
        return {"deleted": True, "tombstoned": True}

    def is_tombstoned(self, slug: str) -> bool:
        return _tombstone_path(slug).exists()

    def _reject_tombstoned(self, slug: str, action: str) -> None:
        if self.is_tombstoned(slug):
            raise LifecycleError(
                409,
                f"agent '{slug}' is deleted (tombstoned) — {action} is "
                "disabled; its history remains readable",
            )

    @staticmethod
    def _nonterminal_executors(slug: str) -> list[str]:
        try:
            from condor.executors.log import _TERMINAL
            from condor.executors.service import peek_executor_runtime

            runtime = peek_executor_runtime()
            if runtime is None:
                return []
            records = runtime.store.load_by_slug(slug)
            return [r.id for r in records if r.status not in _TERMINAL]
        except Exception:
            log.exception("nonterminal-executor check failed for %s", slug)
            return []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(
        self,
        slug: str,
        config: Optional[dict] = None,
        trading_context: str = "",
        chat_id: int = 0,
        user_id: int = 0,
    ) -> dict:
        """Start a session/experiment run (execution_mode comes from config)."""
        self._reject_tombstoned(slug, "launching")
        return await start_session(
            slug,
            config=config,
            trading_context=trading_context,
            chat_id=chat_id,
            user_id=user_id,
        )

    async def control(self, slug: str, verb: str, agent_id: Optional[str] = None) -> dict:
        """pause | resume | stop | shutdown (§5.2 verbs)."""
        return await apply_verb(slug, agent_id, verb)

    def list_runs(self, slug: Optional[str] = None) -> list[dict]:
        """Live engine instances (all agents, or one slug)."""
        instances = list_instances()
        if slug:
            instances = [i for i in instances if i.get("agent_slug") == slug]
        return instances

    def get_run(self, agent_id: str) -> dict:
        from condor.agents.engine import get_engine

        engine = get_engine(agent_id)
        if engine is None:
            raise LifecycleError(404, f"run '{agent_id}' not found")
        return engine.get_info()

    def inject_directive(self, slug: str, text: str, agent_id: Optional[str] = None) -> dict:
        engines = select_engines(slug, agent_id, running_only=True)
        for e in engines:
            e.inject_directive(text)
        return {"queued": True, "runs": [e.agent_id for e in engines]}

    # ------------------------------------------------------------------
    # Consult / delegate
    # ------------------------------------------------------------------

    async def consult(
        self,
        slug: str,
        task: str,
        context: str = "",
        user_id: int = 0,
        chat_id: int = 0,
        server_name: Optional[str] = None,
    ) -> str:
        from condor.agents.consult import run_consult

        self._reject_tombstoned(slug, "consult")
        return await run_consult(
            slug=slug,
            user_id=user_id,
            chat_id=chat_id,
            server_name=server_name,
            task=task,
            context=context,
        )

    async def delegate(
        self,
        slug: str,
        task: str,
        user_id: int = 0,
        chat_id: int = 0,
        server_name: Optional[str] = None,
        risk_limits: Optional[dict] = None,
        timeout_s: Optional[int] = None,
    ) -> dict:
        from condor.agents.delegate import DEFAULT_TIMEOUT_S, start_delegation

        self._reject_tombstoned(slug, "delegate")
        dt = await start_delegation(
            agent_slug=slug,
            task=task,
            user_id=user_id,
            chat_id=chat_id,
            server_name=server_name,
            risk_limits=risk_limits,
            timeout_s=timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S,
        )
        return dt.to_dict() if hasattr(dt, "to_dict") else dt

    # ------------------------------------------------------------------
    # Serialization helper shared by the adapters
    # ------------------------------------------------------------------

    @staticmethod
    def agent_summary(agent: Agent) -> dict:
        d = asdict(agent)
        d["consultable"] = agent.consultable
        d["can_trade"] = agent.can_trade
        return d
