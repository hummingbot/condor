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


# Spec fields whose value is a nested dict: an update patch merges into the
# existing value key-by-key rather than replacing it wholesale, so a partial
# edit ("change take_profit_pct") never silently drops the other keys. Same
# rationale as merge_launch_config for launches (see config.py).
_MERGE_PATCH_FIELDS = {"default_config", "risk_limits", "schedule"}


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge ``patch`` into ``base`` (patch wins per key); nested
    dicts merge key-by-key so a partial config edit preserves unspecified keys.
    (To fully replace a nested field, edit AGENT.md directly.)"""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


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
            "risk_limits",
            "denomination",
            "account",
            "account_label",
            "default_config",
            "default_trading_context",
            "schedule",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise LifecycleError(422, f"unknown agent fields: {sorted(unknown)}")
        for k, v in patch.items():
            if k in _MERGE_PATCH_FIELDS and isinstance(v, dict):
                existing = getattr(agent, k, None)
                if isinstance(existing, dict) and existing:
                    v = _deep_merge(existing, v)
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
                f"agent '{slug}' still has {len(open_execs)} nonterminal or "
                "inventory-bearing executor scope(s) "
                f"with live work or attributed inventory {open_execs[:5]} — "
                "stop/close or explicitly dispose of them first",
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
        """Durable financial-state deletion guard.

        Terminal single-leg executors may still own inventory, so terminal
        status alone is not deletion-safe. Read the log even when no runtime
        exists and let projection failures propagate (fail closed).
        """
        from condor.executors.log import _TERMINAL, ExecutorLog
        from condor.executors.orders import LandedOrder, live_orders, owned_net_base
        from condor.executors.service import peek_executor_runtime

        runtime = peek_executor_runtime()
        store = runtime.store if runtime is not None else ExecutorLog()
        blocked: list[str] = []
        for record in store.load_by_slug(slug):
            if record.status not in _TERMINAL:
                blocked.append(record.id)
                continue
            cfg = record.config or {}
            orders = [
                LandedOrder(**raw) for raw in ((record.state or {}).get("orders") or [])
            ]
            product = record.type.split("_", 1)[1] if "_" in record.type else "spot"
            net = owned_net_base(
                orders,
                product=product,
                base_asset=str(cfg.get("base_token") or ""),
            )
            if live_orders(orders) or net:
                blocked.append(record.id)
        return blocked

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(
        self,
        slug: str,
        config: Optional[dict] = None,
        trading_context: str = "",
        kind: str = "",
        scheduled_for: str = "",
    ) -> dict:
        """Start a session/experiment run (execution_mode comes from config).
        The scheduler passes ``kind="scheduled"`` + the fire time (§5.4)."""
        self._reject_tombstoned(slug, "launching")
        return await start_session(
            slug,
            config=config,
            trading_context=trading_context,
            kind=kind,
            scheduled_for=scheduled_for,
        )

    async def control(
        self,
        slug: str,
        verb: str,
        agent_id: Optional[str] = None,
        close: bool = False,
    ) -> dict:
        """pause | resume | stop [--close] | shutdown (§5.2/§6.2 verbs)."""
        return await apply_verb(slug, agent_id, verb, close=close)

    def list_runs(
        self,
        slug: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 50,
        live_only: bool = False,
    ) -> list[dict]:
        """Run history from the RunStore (one slug or all), newest first,
        with live engine info overlaid on running runs. ``live_only`` returns
        just the live engine instances (the old behavior)."""
        instances = {i["agent_id"]: i for i in list_instances()}
        if live_only:
            out = list(instances.values())
            if slug:
                out = [i for i in out if i.get("agent_slug") == slug]
            return out

        from condor.agents.runstore import get_run_store

        store = get_run_store()
        slugs = [slug] if slug else store.agent_slugs_with_runs()
        runs: list[dict] = []
        for s in slugs:
            runs.extend(store.list_runs(s, kind=kind, limit=limit))
        for meta in runs:
            live = instances.get(meta["run_id"])
            if live is not None:
                meta["live"] = live
                meta["status"] = live.get("status", meta.get("status"))
        runs.sort(key=lambda m: m.get("started_at") or 0, reverse=True)
        return runs[:limit]

    def get_run(self, run_id: str, include_events: bool = False) -> dict:
        """One run: live engine info while running, RunStore meta after."""
        from condor.agents.engine import get_engine
        from condor.agents.runstore import get_run_store

        store = get_run_store()
        path = store.find_run_path(run_id)
        if path is None:
            raise LifecycleError(404, f"run '{run_id}' not found")
        slug = store.slug_from_path(path)
        meta = store.run_meta(slug, run_id)
        engine = get_engine(run_id)
        if engine is not None:
            meta["live"] = engine.get_info()
            meta["status"] = engine.status
        if include_events:
            meta["events"] = store.read_events(slug, run_id)
        return meta

    def export_run(self, run_id: str) -> str:
        """Markdown export of one run (generated view, §7.1)."""
        from condor.agents.exports import render_run_markdown

        meta = self.get_run(run_id, include_events=True)
        events = meta.pop("events")
        return render_run_markdown(meta, events)

    def inject_directive(
        self, slug: str, text: str, agent_id: Optional[str] = None
    ) -> dict:
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
    ) -> str:
        from condor.agents.consult import run_consult

        self._reject_tombstoned(slug, "consult")
        return await run_consult(
            slug=slug,
            task=task,
            context=context,
        )

    async def delegate(
        self,
        slug: str,
        task: str,
        risk_limits: Optional[dict] = None,
        timeout_s: Optional[int] = None,
    ) -> dict:
        from condor.agents.delegate import DEFAULT_TIMEOUT_S, start_delegation

        self._reject_tombstoned(slug, "delegate")
        run_id = await start_delegation(
            agent_slug=slug,
            task=task,
            risk_limits=risk_limits,
            timeout_s=timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S,
        )
        # A delegation is a run; the caller tracks it via get_run/control_run.
        return {"run_id": run_id, "status": "running"}

    # ------------------------------------------------------------------
    # Serialization helper shared by the adapters
    # ------------------------------------------------------------------

    @staticmethod
    def agent_summary(agent: Agent) -> dict:
        d = asdict(agent)
        d["consultable"] = agent.consultable
        d["can_trade"] = agent.can_trade
        return d
