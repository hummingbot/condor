"""Transport-agnostic executor operations.

The create/stop/get/list/performance logic, factored out of the web route so the
same code backs both the (being-retired) REST route and the daemon control
socket. Pure async functions over an ``ExecutorRuntime`` — no FastAPI, no socket.
Raises :class:`ExecutorOpError` (with an HTTP-ish status) which each transport
maps to its own error shape.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

from condor.executors.runtime import ExecutorRuntime, _EXECUTOR_TYPES

# Serializes the risk-cap check + create so two concurrent creates cannot both
# pass the check against the same pre-create snapshot.
_create_lock = asyncio.Lock()

_OPEN_STATUSES = ("PENDING", "ACTIVE", "CLOSING")


class ExecutorOpError(Exception):
    """An executor operation failed. ``status`` mirrors HTTP semantics
    (404 not found, 409 conflict, 422 bad request, 502 upstream)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def record_to_dict(record) -> dict:
    return {
        "id": record.id,
        "type": record.type,
        "status": record.status,
        "agent_slug": record.agent_slug,
        "agent_id": record.agent_id,
        "strategy": record.strategy,
        "config": record.config,
        "state": record.state,
        "close_reason": record.close_reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _request_hash(type: str, config: dict) -> str:
    """Canonical create-request hash (§6.2): the trade intent, excluding
    attribution/routing fields (those come from the capability, not the
    caller) — same canonicalization rules as the spec hashes (§5.3)."""
    from condor.agents.spec import canonical_json

    intent = {
        k: v
        for k, v in config.items()
        if k
        not in (
            "agent_slug",
            "agent_id",
            "strategy",
            "origin",
            "request_hash",
            "user_id",
            "chat_id",
            "notify_trades",
        )
    }
    intent["type"] = type
    import hashlib

    return hashlib.sha256(canonical_json(intent).encode("utf-8")).hexdigest()[:32]


def _transitional_account_ref(venue: str):
    """AccountRef for the lease key. Until the strict account store is the
    live credential source, the process trades exactly one account per venue
    (the flat venues.json / env creds), so a fixed custody placeholder is
    faithful: one lease space per venue. Account activation replaces this
    with the executor's resolved custody address."""
    from condor.accounts.model import AccountRef

    return AccountRef(venue_id=venue or "solana", custody_address="_default")


async def create(
    runtime: ExecutorRuntime,
    *,
    type: str,
    config: dict,
    capability: str = "",
    executor_id: str = "",
    user_id: int = 0,
    chat_id: int = 0,
) -> dict:
    """Create an executor. Authority comes from ``capability`` — an opaque id
    minted by the platform (agent run() or condor-direct registration §6.2).
    Caller-supplied attribution fields are never trusted; absence of a
    capability is a rejection, not a fallback.

    ``executor_id`` (client-generated) is the create identity: replaying the
    same id with the same canonical request hash returns the original result;
    the same id with a DIFFERENT hash is rejected — an id can never be
    silently rebound to a different trade.
    """
    from pydantic import ValidationError

    from condor.executors.capabilities import (
        CapabilityError,
        get_capability_registry,
    )
    from condor.executors.leases import LeaseConflict
    from condor.executors.service import runtime_reconciling

    if runtime_reconciling():
        raise ExecutorOpError(
            503,
            "executor runtime is still reconciling after startup — retry shortly",
        )
    if type not in _EXECUTOR_TYPES:
        raise ExecutorOpError(
            422, f"Unknown executor type '{type}' (known: {sorted(_EXECUTOR_TYPES)})"
        )
    config_cls, _ = _EXECUTOR_TYPES[type]

    # -- creation authority: the server-side capability entry, nothing else --
    try:
        cap = get_capability_registry().resolve(capability)
    except CapabilityError as e:
        raise ExecutorOpError(403, str(e))

    request_hash = _request_hash(type, config)

    # -- idempotent replay (§6.2) --
    if executor_id:
        existing = runtime.store.load(executor_id)
        if existing is not None:
            stored_hash = (existing.config or {}).get("request_hash", "")
            if stored_hash and stored_hash == request_hash:
                return {
                    "id": existing.id,
                    "status": existing.status,
                    "replayed": True,
                }
            raise ExecutorOpError(
                409,
                f"executor_id '{executor_id}' is already bound to a different "
                "create request (same id may never be rebound to a different "
                "trade) — use a fresh executor_id",
            )

    config_data = dict(config)
    config_data["type"] = type
    config_data["origin"] = cap.origin
    config_data["request_hash"] = request_hash
    config_data["agent_slug"] = cap.agent_slug
    config_data["agent_id"] = cap.run_id
    config_data["strategy"] = ""
    config_data["user_id"] = user_id
    config_data["chat_id"] = chat_id

    # order_spot: fill the declared notional from a live quote when omitted, so
    # the risk declaration is always computable.
    if type == "order_spot" and not config_data.get("notional_quote"):
        connector = runtime.connector_for_spec("order_spot", config_data.get("venue"))
        try:
            quote = await connector.quote_swap(
                chain_network=config_data["chain_network"],
                base_token=config_data["base_token"],
                quote_token=config_data["quote_token"],
                amount=float(config_data["amount"]),
                side=config_data["side"],
                connector=config_data.get("connector"),
            )
        except KeyError as e:
            raise ExecutorOpError(422, f"Missing swap config field: {e}")
        except Exception as e:
            raise ExecutorOpError(502, f"Cannot price swap: {e}")
        config_data["notional_quote"] = str(
            Decimal(str(quote["price"])) * Decimal(str(config_data["amount"]))
        )

    try:
        cfg = config_cls(**config_data)
        from condor.executors.base import validate_risk_declaration

        declaration = validate_risk_declaration(cfg.risk_declaration())
    except (ValidationError, ValueError) as e:
        raise ExecutorOpError(422, str(e))

    # Risk caps are a PLATFORM invariant enforced here — the only create path —
    # not just an LLM permission check (risk_gate remains the early UX check).
    # The lock makes check-then-lease-then-create atomic across concurrent
    # creates. Condor-direct creates are deliberately NOT risk-capped (§6.2 —
    # the human is the risk authority for their own trades); venue safety
    # (the lease) applies to every origin.
    account_ref = _transitional_account_ref(config_data.get("venue", ""))
    instrument = cfg.instrument_id()
    actor = cap.run_id if cap.origin == "agent" else "condor"
    async with _create_lock:
        if cap.origin == "agent":
            _enforce_agent_caps(runtime, cap.agent_slug, declaration)
        try:
            runtime.leases.acquire(
                account_ref,
                instrument,
                owner=actor,
                executor_id=executor_id or "pending",
            )
        except LeaseConflict as e:
            raise ExecutorOpError(409, str(e))
        try:
            created_id = runtime.create_executor(cfg, executor_id=executor_id or None)
        except Exception:
            runtime.leases.release(
                account_ref, instrument, executor_id=executor_id or "pending"
            )
            raise
        if not executor_id:
            # Re-key the lease holder from the placeholder to the real id.
            runtime.leases.release(account_ref, instrument, executor_id="pending")
            runtime.leases.acquire(
                account_ref, instrument, owner=actor, executor_id=created_id
            )
    return {
        "id": created_id,
        "status": "PENDING",
        "origin": cap.origin,
        "risk_declaration": {
            "max_notional_quote": float(declaration.max_notional_quote),
            "max_loss_quote": float(declaration.max_loss_quote),
        },
    }


def _enforce_agent_caps(runtime: ExecutorRuntime, agent_slug: str, declaration) -> None:
    """Enforce the owning agent's baseline risk limits against ALL of that
    agent's open executors (every session/delegation of the slug, not one
    session's snapshot). Raises :class:`ExecutorOpError` (409) on breach.

    No agent baseline (chat-driven / unattributed executors) → nothing to
    enforce here; those paths are human-gated upstream."""
    if not agent_slug:
        return
    try:
        from condor.agents.agent import AgentStore

        agent = AgentStore().get(agent_slug)
    except Exception:
        agent = None
    limits = (agent.risk_limits or {}) if agent else {}
    if not limits:
        return

    from condor.executors.performance import open_notional

    records = runtime.store.load_by_slug(agent_slug)
    open_records = [r for r in records if r.status in _OPEN_STATUSES]

    max_open = int(limits.get("max_open_executors") or 0)
    if max_open and len(open_records) + 1 > max_open:
        raise ExecutorOpError(
            409,
            f"risk cap: agent '{agent_slug}' already has {len(open_records)} open "
            f"executor(s) (baseline max_open_executors={max_open})",
        )

    max_pos = float(limits.get("max_position_size_quote") or 0)
    if max_pos:
        exposure = 0.0
        for r in open_records:
            try:
                exposure += float(open_notional(r))
            except (TypeError, ValueError, ArithmeticError):
                pass
        requested = float(declaration.max_notional_quote)
        if exposure + requested > max_pos:
            raise ExecutorOpError(
                409,
                f"risk cap: agent '{agent_slug}' open exposure {exposure:.4f} + "
                f"requested {requested:.4f} exceeds baseline "
                f"max_position_size_quote={max_pos:g}",
            )


async def stop(
    runtime: ExecutorRuntime, *, executor_id: str, keep_position: bool = True
) -> dict:
    record = runtime.store.load(executor_id)
    if record is None:
        raise ExecutorOpError(404, "Executor not found")
    try:
        runtime.stop_executor(executor_id, keep_position=keep_position)
    except KeyError:
        raise ExecutorOpError(
            409, f"Executor {executor_id} is not running (status: {record.status})"
        )
    return {"id": executor_id, "stopping": True, "keep_position": keep_position}


def get(runtime: ExecutorRuntime, executor_id: str) -> dict:
    record = runtime.store.load(executor_id)
    if record is None:
        raise ExecutorOpError(404, "Executor not found")
    return record_to_dict(record)


def list_(runtime: ExecutorRuntime, *, agent_id: Optional[str] = None, limit: int = 50) -> dict:
    store = runtime.store
    records = (
        store.load_by_agent(agent_id, limit=limit) if agent_id else store.list_all(limit=limit)
    )
    return {
        "executors": [record_to_dict(r) for r in records],
        "running": runtime.list_running(),
    }


def performance(
    runtime: ExecutorRuntime,
    *,
    group_by: str = "agent",
    agent_id: Optional[str] = None,
    agent_slug: Optional[str] = None,
) -> dict:
    from condor.executors.performance import GROUP_KEYS, aggregate_performance

    if group_by not in GROUP_KEYS:
        raise ExecutorOpError(422, f"group_by must be one of {GROUP_KEYS}")
    return {
        "group_by": group_by,
        "groups": aggregate_performance(
            runtime.store, group_by=group_by, agent_id=agent_id, agent_slug=agent_slug
        ),
    }
