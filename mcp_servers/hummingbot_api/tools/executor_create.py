"""Typed executor creation for the Hummingbot MCP server (FEAT-062).

One impl per executor type, behind one tool each. The typed signature registered in
``server.py`` *is* the config schema, so a wrong field is a host-side validation error
naming the field rather than a server round trip — which is what let the old
``manage_executors(action="create", executor_config={...})`` cost two calls per create
and still fail late.

Two invariants every impl here shares:

- **Omitted means omitted.** Every optional parameter arrives as ``None`` and is dropped
  by :func:`_compact` instead of being sent as an explicit default. The backend owns its
  own defaults, and — decisively — the user's saved defaults merge *underneath* whatever
  the call sends, so writing a default into the payload would silently clobber a saved
  one.
- **Saved defaults still merge.** Each impl runs the assembled config through
  ``executor_preferences.merge_with_defaults`` exactly as the mega-tool did, so a default
  saved before the split keeps applying after it.

Signatures were verified field-by-field against ``GET /executors/types/{type}/config`` on
a live API server. See ``mcp_servers/TOOL_STYLE.md``.
"""

import asyncio
import logging
from typing import Any

from mcp_servers.hummingbot_api.executor_preferences import executor_preferences
from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache

logger = logging.getLogger("hummingbot-mcp")


def _compact(config: dict[str, Any]) -> dict[str, Any]:
    """Drop the keys the caller left unset.

    An omitted parameter must not reach the backend as an explicit ``None``: the
    backend's own default would be overwritten, and so would the user's saved default,
    which merges underneath this dict.
    """
    return {key: value for key, value in config.items() if value is not None}


def _triple_barrier(
    *,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    time_limit: int | None = None,
    trailing_stop_activation_price: float | None = None,
    trailing_stop_trailing_delta: float | None = None,
    open_order_type: int | None = None,
    take_profit_order_type: int | None = None,
    stop_loss_order_type: int | None = None,
    time_limit_order_type: int | None = None,
) -> dict[str, Any]:
    """Assemble a ``TripleBarrierConfig`` from flat, typed parameters.

    The nested shape is the backend's; flattening it is the whole point of the split, so
    it is rebuilt here rather than asked of the model. A trailing stop needs BOTH of its
    fields to mean anything, so a half-specified one is dropped rather than sent.
    """
    barrier = _compact(
        {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "time_limit": time_limit,
            "open_order_type": open_order_type,
            "take_profit_order_type": take_profit_order_type,
            "stop_loss_order_type": stop_loss_order_type,
            "time_limit_order_type": time_limit_order_type,
        }
    )
    if trailing_stop_activation_price is not None and (
        trailing_stop_trailing_delta is not None
    ):
        barrier["trailing_stop"] = {
            "activation_price": trailing_stop_activation_price,
            "trailing_delta": trailing_stop_trailing_delta,
        }
    return barrier


def _amount(value: Any) -> float | None:
    """Read a config amount as a positive float, or ``None`` if it is not one.

    ``order_executor`` types its amount as a string and a saved default can carry
    anything; a value that will not parse is one this check has nothing to say
    about, not one to refuse.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _rule(rules: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = _amount(rules.get(name))
        if value is not None:
            return value
    return None


async def _price(
    client: Any, connector: str, pair: str, config: dict[str, Any]
) -> float | None:
    """The config's own price if it names one, else the venue's current price."""
    price = _amount(config.get("entry_price")) or _amount(config.get("price"))
    if price is not None:
        return price
    # Imported here, not at module scope: `condor.fetchers.market_data` is
    # reached through the `condor.fetchers` package __init__, which pulls the
    # connectors -> pool_data chain, and this MCP server is also run
    # standalone. Same lazy-import precedent as tools/portfolio.py.
    from condor.fetchers.market_data import fetch_current_price

    # `_amount` still guards the result: a price a check can divide by has
    # to be a positive number, and the fetcher returns the payload's value
    # verbatim. A price we cannot read blocks nothing.
    try:
        return _amount(await fetch_current_price(client, connector, pair))
    except Exception as exc:
        logger.debug("price for %s %s unavailable: %s", connector, pair, exc)
        return None


async def _base_amount_violation(
    client: Any,
    *,
    connector: str,
    pair: str,
    config: dict[str, Any],
    min_order_size: float | None,
    min_notional: float | None,
) -> str | None:
    """Check a base-denominated amount (position, order) against both minimums."""
    amount = _amount(config.get("amount"))
    if amount is None:
        return None

    if min_order_size is not None and amount < min_order_size:
        return (
            f"Order size {amount:g} is below {connector}'s minimum of "
            f"{min_order_size:g} for {pair}. Increase amount to at least "
            f"{min_order_size:g} (amount is in the BASE currency)."
        )

    if min_notional is None:
        return None
    price = await _price(client, connector, pair, config)
    if price is None:
        return None
    notional = amount * price
    if notional >= min_notional:
        return None
    base = pair.split("-")[0]
    return (
        f"Order notional {_usd(notional)} is below {connector}'s minimum of "
        f"{_usd(min_notional)} for {pair}. Increase the amount to at least "
        f"{min_notional / price:.8g} {base} at a price of {price:g}."
    )


async def _trading_rule_violation(
    client: Any, executor_type: str, config: dict[str, Any]
) -> str | None:
    """Name the venue rule this config breaks, or ``None`` to let it through.

    The point is WHERE this runs: before the POST, so the caller is told the
    minimum it missed instead of reading a backend stack trace — or, worse,
    watching an executor sit there never filling. It refuses only on a rule the
    venue actually stated; anything unknown (no rules endpoint, no price, an
    unparseable amount) passes, because a rules outage must not stop trading.
    """
    connector = config.get("connector_name")
    pair = config.get("trading_pair")
    if not isinstance(connector, str) or not isinstance(pair, str):
        return None

    rules = await trading_rules_cache.get(client, connector, pair)
    if not rules:
        return None
    min_order_size = _rule(rules, "min_order_size")
    min_notional = _rule(rules, "min_notional_size", "min_notional")

    if executor_type in ("position_executor", "order_executor"):
        return await _base_amount_violation(
            client,
            connector=connector,
            pair=pair,
            config=config,
            min_order_size=min_order_size,
            min_notional=min_notional,
        )

    if min_notional is None:
        # Every remaining type is funded in quote, so min_order_size — a base
        # amount — says nothing about it without a price.
        return None

    if executor_type == "grid_executor":
        per_level = _amount(config.get("min_order_amount_quote"))
        if per_level is not None and per_level < min_notional:
            return (
                f"Grid min_order_amount_quote {_usd(per_level)} is below "
                f"{connector}'s minimum order notional of {_usd(min_notional)} for "
                f"{pair}. Raise min_order_amount_quote to at least "
                f"{_usd(min_notional)}."
            )
        total = _amount(config.get("total_amount_quote"))
        if total is not None and total < min_notional:
            return (
                f"Grid total_amount_quote {_usd(total)} is below {connector}'s "
                f"minimum order notional of {_usd(min_notional)} for {pair} — not "
                f"one level could be placed. Fund the grid with at least "
                f"{_usd(min_notional)}."
            )
        return None

    if executor_type == "dca_executor":
        levels = config.get("amounts_quote")
        if not isinstance(levels, (list, tuple)):
            return None
        for index, level in enumerate(levels, start=1):
            value = _amount(level)
            if value is not None and value < min_notional:
                return (
                    f"DCA level {index} of {len(levels)} is {_usd(value)}, below "
                    f"{connector}'s minimum order notional of {_usd(min_notional)} "
                    f"for {pair}. Raise that level to at least {_usd(min_notional)}."
                )
        return None

    if executor_type == "lp_executor":
        quote_amount = _amount(config.get("quote_amount"))
        if quote_amount is not None and quote_amount < min_notional:
            return (
                f"LP quote_amount {_usd(quote_amount)} is below {connector}'s "
                f"minimum order notional of {_usd(min_notional)} for {pair}. Fund "
                f"the position with at least {_usd(min_notional)}."
            )
    return None


async def create_executor(
    client: Any,
    executor_type: str,
    config: dict[str, Any],
    *,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> dict[str, Any]:
    """Merge saved defaults, stamp the type, and create the executor.

    The shared tail of all five ``create_*_executor`` tools. ``config`` arrives already
    compacted and already type-checked by the host, so there is nothing left to validate
    server-side — the hand-written field validation the mega-tool needed
    (``validate_executor_config``) existed only because the config was an opaque dict.

    After the POST the new executor is read back briefly (:func:`_read_back`), because
    an accepted config is not a started executor: one the account cannot fund
    terminates at once with ``INSUFFICIENT_BALANCE``, and reporting its id as a
    success is what let an agent announce a position that never opened.
    """
    account = account_name or "master_account"
    tag = controller_id or "main"

    merged_config = executor_preferences.merge_with_defaults(executor_type, config)
    merged_config["type"] = executor_type
    # A saved default may carry a controller_id; the explicit tool parameter is the
    # one the risk gate attributed the position to, so it wins and never travels
    # inside the config.
    merged_config.pop("controller_id", None)

    logger.info(
        "create_%s: controller_id=%r, account=%s, pair=%s",
        executor_type,
        tag,
        account,
        merged_config.get("trading_pair"),
    )

    violation = await _trading_rule_violation(client, executor_type, merged_config)
    if violation:
        # Refused here, not by the backend: below a venue minimum the API answers
        # with an opaque error or accepts an order that never fills.
        logger.info("create_%s refused by a trading rule: %s", executor_type, violation)
        return {
            "action": "create",
            "executor_type": executor_type,
            "error": violation,
            "formatted_output": f"Error creating {executor_type}: {violation}",
        }

    shortfall = await _balance_violation(client, executor_type, merged_config, account)
    if shortfall:
        # Without this the API accepts the config and the executor terminates
        # itself at start with INSUFFICIENT_BALANCE; refusing here creates nothing.
        logger.info("create_%s refused for balance: %s", executor_type, shortfall)
        return {
            "action": "create",
            "executor_type": executor_type,
            "error": shortfall,
            "formatted_output": f"Error creating {executor_type}: {shortfall}",
        }

    try:
        result = await client.executors.create_executor(
            executor_config=merged_config,
            account_name=account,
            controller_id=tag,
        )
    except Exception as exc:
        return {
            "action": "create",
            "executor_type": executor_type,
            "error": str(exc),
            "formatted_output": f"Error creating {executor_type}: {exc}",
        }

    if save_as_default:
        executor_preferences.update_defaults(executor_type, config)

    executor_id = result.get("executor_id") or result.get("id")

    # The POST answering 200 only means the backend accepted the config. The
    # executor checks its budget when it starts and terminates itself if the
    # account cannot fund it, so what it did is read back before anything is
    # reported.
    executor = await _read_back(client, executor_id) if executor_id else None
    status = str(executor.get("status") or "").upper() if executor else ""
    close_type = str(executor.get("close_type") or "").upper() if executor else ""

    header = (
        f"Executor ID: {executor_id or 'N/A'}\n"
        f"Type: {executor_type}\n"
        f"Account: {account}\n"
        f"Controller: {tag}\n"
    )

    if status == "TERMINATED" and close_type in _NEVER_STARTED_CLOSE_TYPES:
        reason = f"it terminated immediately with close_type {close_type}"
        if close_type == "INSUFFICIENT_BALANCE":
            reason += ": the account cannot fund it"
            balances = await _balances_line(
                client, account, merged_config.get("connector_name")
            )
            if balances:
                reason += f" ({balances})"
        error = (
            f"The {executor_type} was accepted by the API but {reason}. No position "
            "was opened and nothing was traded. Do NOT report it as open."
        )
        logger.info("create_%s terminated at start: %s", executor_type, error)
        return {
            "action": "create",
            "executor_id": executor_id,
            "executor_type": executor_type,
            "status": status,
            "close_type": close_type,
            "error": error,
            "formatted_output": f"Error creating {executor_type}: {error}\n\n{header}",
        }

    filled = _amount(executor.get("filled_amount_quote")) if executor else None
    if executor is None:
        state = (
            "Executor submitted, but its state could not be read back. It is NOT "
            "confirmed running: check get_executor before saying a position is open."
        )
    elif status == "TERMINATED":
        state = (
            f"Executor already finished (close_type {close_type or 'unknown'}, "
            f"volume {_usd(filled or 0.0)})."
        )
    elif filled:
        state = f"Executor {status or 'running'}; filled {_usd(filled)} so far."
    else:
        state = (
            f"Executor {status or 'running'}; nothing filled yet. The entry is "
            "not confirmed: check get_executor before saying a position is open."
        )

    formatted = f"{state}\n\n{header}"
    if save_as_default:
        formatted += f"\nConfiguration saved as default for {executor_type}"

    return {
        "action": "create",
        "executor_id": executor_id,
        "executor_type": executor_type,
        "account": account,
        "controller_id": tag,
        "status": status or None,
        "close_type": close_type or None,
        "filled_amount_quote": filled,
        "config_used": merged_config,
        "saved_as_default": save_as_default,
        "result": result,
        "formatted_output": formatted,
    }


#: Close types an executor reaches when it never got going. Anything else on a
#: just-created executor (a market order executor filling and completing, say)
#: is a real outcome, reported but not called an error.
_NEVER_STARTED_CLOSE_TYPES = frozenset({"INSUFFICIENT_BALANCE", "FAILED"})

#: How long to watch a new executor, and how often. The budget check runs in the
#: executor's on_start, so a failure shows within about a tick; a healthy one
#: costs the whole window, which is why it is short.
READ_BACK_WINDOW_SECONDS = 3.0
READ_BACK_INTERVAL_SECONDS = 0.75


async def _read_back(client: Any, executor_id: str) -> dict[str, Any] | None:
    """Watch a new executor for the read-back window; return what it last showed.

    Returns early once it is TERMINATED. ``None`` means its state could not be
    read at all — reported as unconfirmed, never as success or failure.
    """
    get_executor = getattr(getattr(client, "executors", None), "get_executor", None)
    if get_executor is None:
        return None

    last: dict[str, Any] | None = None
    polls = max(1, round(READ_BACK_WINDOW_SECONDS / READ_BACK_INTERVAL_SECONDS))
    for _ in range(polls):
        await asyncio.sleep(READ_BACK_INTERVAL_SECONDS)
        try:
            detail = await get_executor(executor_id)
        except Exception as exc:
            # Right after the POST the executor may not be listed yet; keep
            # watching rather than give up on the first miss.
            logger.debug("read-back of %s failed: %s", executor_id, exc)
            continue
        if isinstance(detail, dict):
            last = detail
            if str(detail.get("status") or "").upper() == "TERMINATED":
                break
    return last


async def _balance_rows(
    client: Any, account: str, connector: str, *, refresh: bool = False
) -> list[dict[str, Any]] | None:
    """The connector's balance rows, or ``None`` if they cannot be read.

    ``refresh=False`` is the API's own cached state: no exchange round trip.
    """
    portfolio = getattr(client, "portfolio", None)
    if portfolio is None:
        return None
    kwargs: dict[str, Any] = {
        "account_names": [account],
        "connector_names": [connector],
    }
    if refresh:
        kwargs["refresh"] = True
    try:
        state = await portfolio.get_state(**kwargs)
    except Exception as exc:
        logger.debug("balances for %s/%s unavailable: %s", account, connector, exc)
        return None
    if not isinstance(state, dict):
        return None
    account_state = state.get(account)
    rows = account_state.get(connector) if isinstance(account_state, dict) else None
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _available(rows: list[dict[str, Any]], token: str) -> float:
    """Units of ``token`` free to spend; a token with no row holds nothing."""
    for row in rows:
        if str(row.get("token", "")).upper() == token.upper():
            return _amount(row.get("available_units", row.get("units"))) or 0.0
    return 0.0


def _describe_balances(connector: str, rows: list[dict[str, Any]]) -> str:
    held = []
    for row in rows:
        units = _amount(row.get("available_units", row.get("units")))
        if units is None:
            continue
        value = _amount(row.get("value"))
        held.append(
            f"{row.get('token', '?')} {units:g}"
            + (f" (~{_usd(value)})" if value is not None else "")
        )
    if not held:
        return f"{connector} holds no available balance"
    return f"{connector} available: " + ", ".join(held)


async def _balances_line(client: Any, account: str, connector: Any) -> str | None:
    """Name what the connector holds, so an unfunded executor says what is there."""
    if not isinstance(connector, str):
        return None
    rows = await _balance_rows(client, account, connector)
    return None if rows is None else _describe_balances(connector, rows)


def _is_buy(side: Any) -> bool | None:
    if side in (1, "1") or str(side).upper() == "BUY":
        return True
    if side in (2, "2") or str(side).upper() == "SELL":
        return False
    return None


async def _required_spend(
    client: Any, executor_type: str, config: dict[str, Any], connector: str, pair: str
) -> tuple[str, float, str] | None:
    """What the executor spends up front: ``(token, units, how)``, or ``None``.

    Spot only. ``how`` is the arithmetic, so the refusal can show its working.
    """
    buy = _is_buy(config.get("side"))
    if buy is None or "-" not in pair:
        return None
    base, quote = pair.split("-", 1)

    if executor_type in ("position_executor", "order_executor"):
        amount = _amount(config.get("amount"))
        if amount is None:
            return None
        if not buy:
            return base, amount, f"a {amount:g} {base} sell"
        price = await _price(client, connector, pair, config)
        if price is None:
            return None
        return quote, amount * price, f"{amount:g} {base} at {price:g}"

    if executor_type == "grid_executor":
        total = _amount(config.get("total_amount_quote"))
    elif executor_type == "dca_executor":
        levels = config.get("amounts_quote")
        if not isinstance(levels, (list, tuple)):
            return None
        total = sum(_amount(level) or 0.0 for level in levels) or None
    else:
        # An LP position is funded in two tokens on a gateway wallet; its
        # own open transaction is the check.
        return None
    if total is None:
        return None
    if buy:
        return quote, total, f"{_usd(total)} of {quote}"
    price = await _price(client, connector, pair, config)
    if price is None:
        return None
    return base, total / price, f"{_usd(total)} of {base} at {price:g}"


async def _balance_violation(
    client: Any, executor_type: str, config: dict[str, Any], account: str
) -> str | None:
    """Refuse a spot executor the account visibly cannot fund, before the POST.

    Read from the API's cached balances, so the common case costs no exchange
    call. A cached shortfall is re-read with ``refresh=True`` before refusing,
    so a stale cache (a deposit that just landed) can never block a trade.

    Perpetuals are left to the executor's own start-up check (collateral,
    leverage and fees are the connector's arithmetic, not ours), as is anything
    this cannot read: the post-create read-back still catches those.
    """
    connector = config.get("connector_name")
    pair = config.get("trading_pair")
    if not isinstance(connector, str) or not isinstance(pair, str):
        return None
    if "perpetual" in connector:
        return None

    need = await _required_spend(client, executor_type, config, connector, pair)
    if need is None:
        return None
    token, required, how = need

    rows = await _balance_rows(client, account, connector)
    if not rows or _available(rows, token) >= required:
        # No rows at all is "unknown", not "empty": the connector may just be
        # missing from the state.
        return None
    rows = await _balance_rows(client, account, connector, refresh=True)
    if not rows:
        return None
    available = _available(rows, token)
    if available >= required:
        return None
    return (
        f"Insufficient balance on {connector} ({account}): this {executor_type} "
        f"needs {required:.8g} {token} ({how}) but only {available:.8g} {token} is "
        f"available. {_describe_balances(connector, rows)}. Nothing was created. "
        "Fund the account or ask the user how to proceed; do not switch to another "
        "connector (a perpetual, say) without asking."
    )


async def create_position_executor(
    client: Any,
    *,
    connector_name: str,
    trading_pair: str,
    side: int,
    amount: float,
    entry_price: float | None = None,
    leverage: int | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    time_limit: int | None = None,
    trailing_stop_activation_price: float | None = None,
    trailing_stop_trailing_delta: float | None = None,
    open_order_type: int | None = None,
    take_profit_order_type: int | None = None,
    stop_loss_order_type: int | None = None,
    time_limit_order_type: int | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> dict[str, Any]:
    """Open a directional position with triple-barrier exits."""
    config = _compact(
        {
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "entry_price": entry_price,
            "leverage": leverage,
            "level_id": level_id,
        }
    )
    barrier = _triple_barrier(
        stop_loss=stop_loss,
        take_profit=take_profit,
        time_limit=time_limit,
        trailing_stop_activation_price=trailing_stop_activation_price,
        trailing_stop_trailing_delta=trailing_stop_trailing_delta,
        open_order_type=open_order_type,
        take_profit_order_type=take_profit_order_type,
        stop_loss_order_type=stop_loss_order_type,
        time_limit_order_type=time_limit_order_type,
    )
    if barrier:
        config["triple_barrier_config"] = barrier

    return await create_executor(
        client,
        "position_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )


async def create_grid_executor(
    client: Any,
    *,
    connector_name: str,
    trading_pair: str,
    side: int,
    start_price: float,
    end_price: float,
    limit_price: float,
    total_amount_quote: float,
    take_profit: float | None = None,
    time_limit: int | None = None,
    open_order_type: int | None = None,
    take_profit_order_type: int | None = None,
    min_spread_between_orders: float | None = None,
    min_order_amount_quote: float | None = None,
    max_open_orders: int | None = None,
    max_orders_per_batch: int | None = None,
    order_frequency: int | None = None,
    activation_bounds: float | None = None,
    safe_extra_spread: float | None = None,
    leverage: int | None = None,
    keep_position: bool | None = None,
    coerce_tp_to_step: bool | None = None,
    deduct_base_fees: bool | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> dict[str, Any]:
    """Run a grid of limit orders across a price range.

    Raises ``ValueError`` when the three prices do not describe the requested direction:
    the backend accepts an inverted grid and it simply never fills, so the invariant the
    guide states in prose is checked here where the caller can still fix it.
    """
    if side == 1 and not (limit_price < start_price < end_price):
        raise ValueError(
            "LONG grid (side=1) requires limit_price < start_price < end_price, got "
            f"limit_price={limit_price}, start_price={start_price}, "
            f"end_price={end_price}"
        )
    if side == 2 and not (start_price < end_price < limit_price):
        raise ValueError(
            "SHORT grid (side=2) requires start_price < end_price < limit_price, got "
            f"start_price={start_price}, end_price={end_price}, "
            f"limit_price={limit_price}"
        )

    config = _compact(
        {
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "side": side,
            "start_price": start_price,
            "end_price": end_price,
            "limit_price": limit_price,
            "total_amount_quote": total_amount_quote,
            "min_spread_between_orders": min_spread_between_orders,
            "min_order_amount_quote": min_order_amount_quote,
            "max_open_orders": max_open_orders,
            "max_orders_per_batch": max_orders_per_batch,
            "order_frequency": order_frequency,
            "activation_bounds": activation_bounds,
            "safe_extra_spread": safe_extra_spread,
            "leverage": leverage,
            "keep_position": keep_position,
            "coerce_tp_to_step": coerce_tp_to_step,
            "deduct_base_fees": deduct_base_fees,
            "level_id": level_id,
        }
    )
    # Required by the backend schema, unlike the position executor's, so it is always
    # sent — even empty, which means "every barrier at its default".
    config["triple_barrier_config"] = _triple_barrier(
        take_profit=take_profit,
        time_limit=time_limit,
        open_order_type=open_order_type,
        take_profit_order_type=take_profit_order_type,
    )

    return await create_executor(
        client,
        "grid_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )


async def create_dca_executor(
    client: Any,
    *,
    connector_name: str,
    trading_pair: str,
    side: int,
    amounts_quote: list[float],
    prices: list[float],
    leverage: int | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    time_limit: int | None = None,
    trailing_stop_activation_price: float | None = None,
    trailing_stop_trailing_delta: float | None = None,
    mode: str | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> dict[str, Any]:
    """Average into a position over a ladder of price levels.

    Raises ``ValueError`` when ``amounts_quote`` and ``prices`` are not parallel: they
    are two halves of one list of levels, and a length mismatch is a silently wrong
    ladder rather than an error the backend reports.
    """
    if len(amounts_quote) != len(prices):
        raise ValueError(
            "amounts_quote and prices are parallel lists — one entry each per DCA "
            f"level, got {len(amounts_quote)} amounts and {len(prices)} prices"
        )
    if not amounts_quote:
        raise ValueError("amounts_quote must contain at least one level")

    config = _compact(
        {
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "side": side,
            "amounts_quote": amounts_quote,
            "prices": prices,
            "leverage": leverage,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "time_limit": time_limit,
            "mode": mode,
            "level_id": level_id,
        }
    )
    if trailing_stop_activation_price is not None and (
        trailing_stop_trailing_delta is not None
    ):
        # Flat on the DCA config, not nested in a triple barrier.
        config["trailing_stop"] = {
            "activation_price": trailing_stop_activation_price,
            "trailing_delta": trailing_stop_trailing_delta,
        }

    return await create_executor(
        client,
        "dca_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )


async def create_order_executor(
    client: Any,
    *,
    connector_name: str,
    trading_pair: str,
    side: int,
    amount: str,
    execution_strategy: str,
    price: float | None = None,
    chaser_distance: float | None = None,
    chaser_refresh_threshold: float | None = None,
    leverage: int | None = None,
    position_action: str | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> dict[str, Any]:
    """Place one order with a chosen execution strategy.

    Raises ``ValueError`` when the strategy and its companion field disagree — a LIMIT
    with no price, or a LIMIT_CHASER with no chaser config, is rejected by the backend
    only after a round trip.
    """
    if execution_strategy in ("LIMIT", "LIMIT_MAKER") and price is None:
        raise ValueError(f"execution_strategy={execution_strategy} requires a price")
    if execution_strategy == "LIMIT_CHASER" and (
        chaser_distance is None or chaser_refresh_threshold is None
    ):
        raise ValueError(
            "execution_strategy=LIMIT_CHASER requires both chaser_distance and "
            "chaser_refresh_threshold"
        )

    config = _compact(
        {
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "execution_strategy": execution_strategy,
            "price": price,
            "leverage": leverage,
            "position_action": position_action,
            "level_id": level_id,
        }
    )
    if chaser_distance is not None and chaser_refresh_threshold is not None:
        config["chaser_config"] = {
            "distance": chaser_distance,
            "refresh_threshold": chaser_refresh_threshold,
        }

    return await create_executor(
        client,
        "order_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )


async def create_lp_executor(
    client: Any,
    *,
    connector_name: str,
    lp_provider: str,
    trading_pair: str,
    pool_address: str,
    lower_price: float,
    upper_price: float,
    side: int,
    base_amount: float | None = None,
    quote_amount: float | None = None,
    upper_limit_price: float | None = None,
    lower_limit_price: float | None = None,
    swap_provider: str | None = None,
    keep_position: bool | None = None,
    extra_params: dict[str, Any] | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> dict[str, Any]:
    """Open a CLMM liquidity position inside a price range.

    Raises ``ValueError`` on a range that is not a range, or on a position funded with
    nothing: both are accepted by the backend and fail on-chain.
    """
    if lower_price >= upper_price:
        raise ValueError(
            f"lower_price must be below upper_price, got {lower_price} >= {upper_price}"
        )
    if not (base_amount or quote_amount):
        raise ValueError(
            "an LP position needs funding — give base_amount, quote_amount, or both"
        )

    config = _compact(
        {
            "connector_name": connector_name,
            "lp_provider": lp_provider,
            "trading_pair": trading_pair,
            "pool_address": pool_address,
            "lower_price": lower_price,
            "upper_price": upper_price,
            "side": side,
            "base_amount": base_amount,
            "quote_amount": quote_amount,
            "upper_limit_price": upper_limit_price,
            "lower_limit_price": lower_limit_price,
            "swap_provider": swap_provider,
            "keep_position": keep_position,
            "extra_params": extra_params,
        }
    )

    return await create_executor(
        client,
        "lp_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )
