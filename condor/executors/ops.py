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

from condor.executors.runtime import _EXECUTOR_TYPES, ExecutorRuntime

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
            "capability_owner",
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

    config_data = dict(config)
    venue = str(config_data.get("venue") or "solana")

    # Resolve the custody identity from server-owned run context. Agent calls
    # may not select/rebind an account in executor config. Condor-direct calls
    # may provide a name/address selector, which is resolved immediately.
    from condor.executors import wallets

    account_store = wallets.account_store()
    selector = config_data.pop("account", None)
    try:
        with account_store.locked_snapshot() as account_data:
            if cap.origin == "agent":
                account_ref = cap.account_ref
                if account_ref is None:
                    raise ExecutorOpError(
                        403,
                        "agent run capability has no frozen account binding",
                    )
                if selector:
                    requested = account_store._resolve_in_data(
                        account_data, venue, str(selector)
                    )
                    if requested != account_ref:
                        raise ExecutorOpError(
                            403,
                            "executor request cannot override the run's frozen account",
                        )
            else:
                account_ref = account_store._resolve_in_data(
                    account_data, venue, str(selector) if selector else None
                )
            if account_ref.venue_id != venue:
                raise ExecutorOpError(
                    422,
                    f"account {account_ref} does not belong to venue {venue!r}",
                )
            credential_snapshot = dict(
                account_data[venue]["accounts"][account_ref.custody_address]
            )
    except ExecutorOpError:
        raise
    except Exception as e:
        raise ExecutorOpError(422, f"cannot resolve execution account: {e}") from e

    # Caller wallet/network strings are presentation leftovers, not authority.
    # Derive both from the resolved AccountRef + registered deployment.
    from condor.accounts.registry import default_registry

    network = default_registry().get(venue).network
    config_data["venue"] = venue
    config_data["account_ref"] = account_ref.as_dict()
    config_data["wallet_address"] = account_ref.custody_address
    config_data["chain_network"] = f"{venue}-{network}"

    request_hash = _request_hash(type, config_data)

    # -- idempotent replay (§6.2) --
    if executor_id:
        existing = runtime.store.load(executor_id)
        if existing is not None:
            stored = existing.config or {}
            same_binding = (
                stored.get("request_hash") == request_hash
                and stored.get("origin") == cap.origin
                and stored.get("agent_slug", "") == cap.agent_slug
                and stored.get("agent_id", "") == cap.run_id
                and stored.get("capability_owner", "")
                == (cap.run_id or cap.connection_id)
                and stored.get("account_ref") == account_ref.as_dict()
            )
            if same_binding:
                return {
                    "id": existing.id,
                    "status": existing.status,
                    "replayed": True,
                }
            raise ExecutorOpError(
                409,
                f"executor_id '{executor_id}' is already bound to a different "
                "request, owner, run, or account — use a fresh executor_id",
            )

    config_data["type"] = type
    config_data["origin"] = cap.origin
    config_data["capability_owner"] = cap.run_id or cap.connection_id
    config_data["request_hash"] = request_hash
    config_data["agent_slug"] = cap.agent_slug
    config_data["agent_id"] = cap.run_id
    config_data["strategy"] = ""

    # order_spot: fill the declared notional from a live quote when omitted, so
    # the risk declaration is always computable.
    if type == "order_spot" and not config_data.get("notional_quote"):
        connector = runtime.connector_for_spec("order_spot", venue, account_ref)
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
    else:
        connector = runtime.connector_for_spec(type, venue, account_ref)

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
            # Optimistic account transaction: connector credentials were bound
            # before the read-only quote. Re-acquire the same process-wide
            # guard, prove the account did not change, then persist the opener
            # synchronously before an edit/remove can interleave.
            with account_store.locked_snapshot() as current_accounts:
                current = current_accounts[venue]["accounts"][
                    account_ref.custody_address
                ]
                if current != credential_snapshot:
                    raise ExecutorOpError(
                        409,
                        f"account {account_ref} changed during executor creation; retry",
                    )
                created_id = runtime.create_executor(
                    cfg,
                    executor_id=executor_id or None,
                    connector=connector,
                )
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


def _scope_exposure(records) -> float:
    """Conservative attributed exposure folded by financial scope.

    Terminal entry and exit executors must offset when they belong to the same
    account/instrument, but never across accounts or instruments. Confirmed
    fills are folded as signed quote cashflow. Live remainders are projected
    one side at a time and the worst absolute outcome is reserved, so two
    opposing resting orders cannot cancel each other before either has filled.
    """
    from collections import defaultdict

    from condor.executors.orders import LandedOrder
    from condor.executors.performance import open_notional

    buckets = defaultdict(
        lambda: {
            "net_base": 0.0,
            "buy_base": 0.0,
            "buy_quote": 0.0,
            "sell_base": 0.0,
            "sell_quote": 0.0,
            "buy": 0.0,
            "sell": 0.0,
        }
    )
    for record in records:
        cfg = record.config or {}
        account = cfg.get("account_ref") or {}
        custody = str(account.get("custody_address") or "")
        account_key = (
            str(account.get("venue_id") or cfg.get("venue") or ""),
            custody or f"unbound:{record.id}",
        )
        instrument = (
            f"{cfg.get('base_token')}-{cfg.get('quote_token')}"
            if cfg.get("base_token") and cfg.get("quote_token")
            else str(cfg.get("coin") or cfg.get("market") or record.type)
        )
        key = (*account_key, instrument, _quote_unit(cfg))
        bucket = buckets[key]
        raw_orders = (record.state or {}).get("orders") or []
        try:
            declared = float(open_notional(record))
        except (TypeError, ValueError, ArithmeticError):
            declared = 0.0

        if not raw_orders:
            # Only a nonterminal, pre-ack executor reserves declared size.
            # A terminal record with no landed order owns no known inventory.
            if record.status in _OPEN_STATUSES:
                side = str(cfg.get("side") or cfg.get("position") or "BUY").upper()
                bucket["sell" if side in ("SELL", "SHORT") else "buy"] += declared
            continue

        for raw in raw_orders:
            order = LandedOrder(**raw)
            side = str(order.side).lower()
            filled_base = float(order.cumulative_filled_base_qty)
            filled_quote = float(order.cumulative_filled_quote_qty)
            if side == "buy":
                bucket["net_base"] += filled_base
                bucket["buy_base"] += filled_base
                bucket["buy_quote"] += filled_quote
            else:
                bucket["net_base"] -= filled_base
                bucket["sell_base"] += filled_base
                bucket["sell_quote"] += filled_quote
            if (record.type.split("_", 1)[-1] in ("spot", "pred")) and cfg.get(
                "base_token"
            ):
                bucket["net_base"] -= float(
                    order.fees_by_asset.get(str(cfg["base_token"]), 0)
                )
            if not order.status.is_live:
                continue
            if order.requested_unit == "quote":
                remainder = float(order.requested_qty) - filled_quote
            else:
                requested_base = float(order.requested_qty)
                filled_base = float(order.cumulative_filled_base_qty)
                fraction = (
                    max(0.0, 1.0 - filled_base / requested_base)
                    if requested_base > 0
                    else 0.0
                )
                remainder = declared * fraction
            bucket["sell" if side == "sell" else "buy"] += max(0.0, remainder)

    total = 0.0
    for bucket in buckets.values():
        net_base = bucket["net_base"]
        if net_base >= 0 and bucket["buy_base"] > 0:
            price = bucket["buy_quote"] / bucket["buy_base"]
        elif net_base < 0 and bucket["sell_base"] > 0:
            price = bucket["sell_quote"] / bucket["sell_base"]
        else:
            price = 0.0
        confirmed = net_base * price
        total += max(
            abs(confirmed),
            abs(confirmed + bucket["buy"]),
            abs(confirmed - bucket["sell"]),
        )
    return total


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
        f"{base}-{quote}"
        if base and quote
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
    requested_account = cfg.get("account_ref")
    requested_venue = cfg.get("venue") or "solana"
    for r in records:
        rcfg = r.config or {}
        if (rcfg.get("venue") or "solana") != requested_venue:
            continue
        if requested_account and rcfg.get("account_ref") != requested_account:
            continue
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

    if cfg is not None and _is_risk_reducing(slug_records, cfg, type_):
        return  # §6.1 exemption: reducing orders bypass the caps

    def _check(
        scope_name: str,
        scope_limits: dict,
        all_records: list,
        open_records: list,
    ) -> None:
        max_open = int(scope_limits.get("max_open_executors") or 0)
        if max_open and len(open_records) + 1 > max_open:
            raise ExecutorOpError(
                409,
                f"risk cap ({scope_name}): {len(open_records)} nonterminal "
                f"executor(s) already attributed (max_open_executors={max_open})",
            )
        max_pos = float(scope_limits.get("max_position_size_quote") or 0)
        if max_pos:
            # Terminal executors can still own venue inventory (single-leg
            # order fills, detached positions). Count open executors only for
            # the concurrency cap, but fold financial exposure from every
            # attributed record until landed exits reduce it.
            exposure = _scope_exposure(all_records)
            requested = float(declaration.max_notional_quote)
            if exposure + requested > max_pos:
                raise ExecutorOpError(
                    409,
                    f"risk cap ({scope_name}): attributed exposure "
                    f"{exposure:.4f} + requested {requested:.4f} exceeds "
                    f"max_position_size_quote={max_pos:g}",
                )

    if limits:
        _check(f"agent '{agent_slug}'", limits, slug_records, slug_open)
    if run_limits and run_id:
        run_records = [r for r in slug_records if r.agent_id == run_id]
        run_open = _scope_open_records(run_records)
        _check(f"run '{run_id}'", run_limits, run_records, run_open)


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


def list_(
    runtime: ExecutorRuntime, *, agent_id: Optional[str] = None, limit: int = 50
) -> dict:
    store = runtime.store
    records = (
        store.load_by_agent(agent_id, limit=limit)
        if agent_id
        else store.list_all(limit=limit)
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
