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
            # user_id/chat_id are gone from ExecutorConfig (§4.3) but stay
            # excluded here so a replay of a pre-migration create request
            # (config carrying the old keys) hashes identically.
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
            _enforce_agent_caps(
                runtime,
                cap.agent_slug,
                declaration,
                cfg=config_data,
                type_=type,
                run_id=cap.run_id,
                run_limits=cap.risk_limits or None,
            )
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


# USD-family stables treated as 1:1 with each other for denomination checks.
_USD_FAMILY = {"USD", "USDC"}


def _quote_unit(cfg: dict) -> str:
    """The quote currency an executor's amounts are expressed in."""
    q = cfg.get("quote_token")
    if q:
        return str(q).upper()
    # perp (Hyperliquid USDC-margined) and pred (USDC-quoted CLOBs)
    return "USDC"


def _assert_denomination_convertible(agent, cfg: dict) -> None:
    """§6.1: every exposure bucket converts into the agent's declared
    denomination before caps apply — and pricing FAILS CLOSED. Instruments
    already quoted in the denomination convert at 1 (the common case).
    Fresh-price cross conversion arrives with the venue packages' price
    surface; until then a cross-denomination create is rejected with a clear
    error, never priced at zero or passed through unconverted."""
    denom = (getattr(agent, "denomination", "") or "").strip().upper()
    if not denom:
        return  # no denomination → no risk limits (validated at spec save)
    unit = _quote_unit(cfg)
    if unit == denom:
        return
    if unit in _USD_FAMILY and denom in _USD_FAMILY:
        return
    raise ExecutorOpError(
        422,
        f"denomination conversion unavailable: this executor is quoted in "
        f"{unit} but agent risk limits are denominated in {denom} — "
        f"cross-denomination pricing fails closed (§6.1); trade "
        f"{denom}-quoted instruments or update the agent's denomination",
    )


def _record_exposure(record) -> float:
    """One open executor's exposure as a DISJOINT projection (§6.1):

    1. pre-ack reservation — no landed orders yet (SUBMITTING/OPENING):
       reserved at declared size;
    2. confirmed inventory — cost of landed entry fills minus exit proceeds;
    3. unfilled risk-increasing remainder of live entry/trade orders.

    A partial fill atomically transfers value from bucket 3 to bucket 2 —
    total exposure must not jump as an order moves SUBMITTING → OPEN →
    partially filled → FILLED.
    """
    from condor.executors.orders import LandedOrder, OrderRole
    from condor.executors.performance import open_notional

    raw_orders = (record.state or {}).get("orders") or []
    if not raw_orders:
        # Bucket 1: reservation at declared size.
        try:
            return float(open_notional(record))
        except (TypeError, ValueError, ArithmeticError):
            return 0.0

    entries_quote = 0.0
    exits_quote = 0.0
    remainder_quote = 0.0
    declared = 0.0
    try:
        declared = float(open_notional(record))
    except (TypeError, ValueError, ArithmeticError):
        pass
    for raw in raw_orders:
        o = LandedOrder(**raw)
        filled_q = float(o.cumulative_filled_quote_qty)
        if o.role in (OrderRole.ENTRY, OrderRole.TRADE):
            entries_quote += filled_q
            if o.status.is_live:
                # Bucket 3: live unfilled remainder, in quote terms.
                if o.requested_unit == "quote":
                    rem = float(o.requested_qty) - filled_q
                else:
                    filled_b = float(o.cumulative_filled_base_qty)
                    req_b = float(o.requested_qty)
                    frac = 1.0 - (filled_b / req_b) if req_b > 0 else 0.0
                    rem = declared * max(0.0, min(1.0, frac))
                remainder_quote += max(0.0, rem)
        elif o.role == OrderRole.EXIT:
            exits_quote += filled_q
    inventory = max(0.0, entries_quote - exits_quote)
    return inventory + remainder_quote


def _scope_open_records(records) -> list:
    return [r for r in records if r.status in _OPEN_STATUSES]


def _is_risk_reducing(records, cfg: dict, type_: str) -> bool:
    """§6.1 risk-reducing exemption — a PROSPECTIVE predicate at authorization
    time: the new order must (1) oppose the sign of the scope's current
    owned_net_base on the SAME instrument, and (2) keep the aggregate
    projection (position plus EVERY nonterminal owned opposite-side order,
    protection included, fully filled) at zero or the original sign — never
    cross it. Anything unresolvable fails closed to the normal cap check."""
    from condor.executors.orders import LandedOrder, owned_net_base

    instrument = type_.split("_", 1)[1] if "_" in type_ else ""
    base, quote = cfg.get("base_token"), cfg.get("quote_token")
    inst_id = (
        f"{base}-{quote}" if base and quote
        else str(cfg.get("coin") or cfg.get("market") or "")
    )
    if not inst_id:
        return False

    side = str(cfg.get("side") or cfg.get("position") or "").upper()
    is_sell = side in ("SELL", "SHORT")
    try:
        requested_base = float(cfg.get("amount") or cfg.get("size") or 0)
    except (TypeError, ValueError):
        return False
    if requested_base <= 0:
        return False

    product = "perp" if instrument == "perp" else instrument or "spot"
    scope_orders: list[LandedOrder] = []
    for r in records:
        rcfg = r.config or {}
        r_inst = (
            f"{rcfg.get('base_token')}-{rcfg.get('quote_token')}"
            if rcfg.get("base_token") and rcfg.get("quote_token")
            else str(rcfg.get("coin") or rcfg.get("market") or "")
        )
        if r_inst != inst_id:
            continue
        for raw in (r.state or {}).get("orders") or []:
            scope_orders.append(LandedOrder(**raw))

    net = float(
        owned_net_base(scope_orders, product=product, base_asset=str(base or ""))
    )
    if net == 0:
        return False
    opposes = (net > 0 and is_sell) or (net < 0 and not is_sell)
    if not opposes:
        return False

    # Reducing capacity is reserved atomically across EVERY live opposite-side
    # order in the scope, regardless of label (incl. native TP/SL).
    from condor.executors.orders import unfilled_remainder

    live_opposite_base = 0.0
    for o in scope_orders:
        if not o.status.is_live:
            continue
        o_is_sell = o.side == "sell"
        if (net > 0 and o_is_sell) or (net < 0 and not o_is_sell):
            if o.requested_unit == "base":
                live_opposite_base += float(unfilled_remainder(o))
    projected = abs(net) - live_opposite_base - requested_base
    # zero or original sign — never crossed
    return projected >= 0


def _enforce_agent_caps(
    runtime: ExecutorRuntime,
    agent_slug: str,
    declaration,
    *,
    cfg: dict | None = None,
    type_: str = "",
    run_id: str = "",
    run_limits: dict | None = None,
) -> None:
    """Enforce risk caps composed by scope (§6.1) — a platform invariant on
    the ONLY create path. Both must pass:

    | scope | cap source            | checked against                       |
    | agent | AGENT.md baseline     | slug-wide attributed exposure/count   |
    | run   | frozen spec (stricter)| run-attributed exposure/count         |

    Cleanup orders that satisfy the risk-reducing exemption predicate are
    exempt from both caps (an over-cap agent can always reduce)."""
    if not agent_slug:
        return
    try:
        from condor.agents.agent import AgentStore

        agent = AgentStore().get(agent_slug)
    except Exception:
        agent = None
    limits = (agent.risk_limits or {}) if agent else {}
    if not limits and not run_limits:
        return

    if agent is not None and cfg is not None:
        _assert_denomination_convertible(agent, cfg)

    slug_records = runtime.store.load_by_slug(agent_slug)
    slug_open = _scope_open_records(slug_records)

    if cfg is not None and _is_risk_reducing(slug_open, cfg, type_):
        return  # §6.1 exemption: reducing orders bypass the caps

    def _check(scope_name: str, scope_limits: dict, open_records: list) -> None:
        max_open = int(scope_limits.get("max_open_executors") or 0)
        if max_open and len(open_records) + 1 > max_open:
            raise ExecutorOpError(
                409,
                f"risk cap ({scope_name}): {len(open_records)} nonterminal "
                f"executor(s) already attributed (max_open_executors={max_open})",
            )
        max_pos = float(scope_limits.get("max_position_size_quote") or 0)
        if max_pos:
            exposure = sum(_record_exposure(r) for r in open_records)
            requested = float(declaration.max_notional_quote)
            if exposure + requested > max_pos:
                raise ExecutorOpError(
                    409,
                    f"risk cap ({scope_name}): attributed exposure "
                    f"{exposure:.4f} + requested {requested:.4f} exceeds "
                    f"max_position_size_quote={max_pos:g}",
                )

    if limits:
        _check(f"agent '{agent_slug}'", limits, slug_open)
    if run_limits and run_id:
        run_open = [r for r in slug_open if r.agent_id == run_id]
        _check(f"run '{run_id}'", run_limits, run_open)


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
