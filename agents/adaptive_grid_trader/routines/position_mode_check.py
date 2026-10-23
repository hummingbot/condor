import logging
import math
from typing import Any

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Read the exact-pair trading state without changing exchange state."""

    connector_name: str = Field(default="binance_perpetual")
    account_name: str = Field(default="master_account")
    trading_pair: str = Field(default="SOL-USDT")
    leverage: Any = Field(default=None)


def _parse_mode(result: Any) -> str:
    """Defensively pull the mode out of the API response."""
    raw = result
    if isinstance(result, dict):
        for key in ("position_mode", "positionMode", "mode", "data"):
            if key in result:
                raw = result[key]
                break
    if isinstance(raw, dict):
        raw = raw.get("position_mode", raw.get("mode", ""))
    text = str(raw).strip().upper().replace("-", "").replace("_", "")
    if text == "HEDGE":
        return "HEDGE"
    if text == "ONEWAY":
        return "ONEWAY"
    return "UNKNOWN"


def _parse_records(result: Any, keys: tuple[str, ...]) -> list[dict] | None:
    """Return records, preserving the distinction between empty and malformed."""
    records = result
    if isinstance(result, dict):
        records = next(
            (result[key] for key in keys if isinstance(result.get(key), list)),
            None,
        )
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        return None
    return records


def _is_incomplete(result: Any) -> bool:
    """Reject paginated responses whose remaining records were not read."""
    if not isinstance(result, dict):
        return False
    pagination = result.get("pagination")
    pagination = pagination if isinstance(pagination, dict) else {}
    return bool(
        result.get("next_cursor")
        or pagination.get("next_cursor")
        or pagination.get("cursor")
        or pagination.get("has_more") is True
    )


def _number(record: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in record:
            continue
        try:
            value = float(record[key])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None
    return None


def _resolve_leverage(value: Any) -> tuple[str, str, bool]:
    """Resolve the explicit create leverage or fail closed on invalid input."""
    if value is None:
        return "5", "5x (default)", True
    if isinstance(value, bool):
        return "INVALID", "invalid", False
    try:
        leverage = float(value)
    except (TypeError, ValueError):
        return "INVALID", "invalid", False
    if not math.isfinite(leverage) or leverage <= 0 or not leverage.is_integer():
        return "INVALID", "invalid", False
    leverage_int = int(leverage)
    return str(leverage_int), f"{leverage_int}x (configured)", True


def _pair(record: dict) -> str | None:
    for key in ("trading_pair", "tradingPair", "symbol"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper().replace("/", "-")
    return None


def _is_open(position: dict) -> bool | None:
    amount = _number(
        position,
        (
            "amount",
            "position_size",
            "net_amount_base",
            "position_amt",
            "positionAmt",
            "base_amount",
            "size",
        ),
    )
    return None if amount is None else abs(amount) > 0


def _order_ids(result: Any) -> list[str] | None:
    if _is_incomplete(result):
        return None
    orders = _parse_records(result, ("data", "orders", "active_orders"))
    if orders is None:
        return None
    order_ids = []
    for order in orders:
        order_id = next(
            (
                str(order[key]).strip()
                for key in ("client_order_id", "clientOrderId", "order_id", "id")
                if order.get(key) is not None and str(order[key]).strip()
            ),
            None,
        )
        if order_id is None:
            return None
        order_ids.append(order_id)
    if len(set(order_ids)) != len(order_ids):
        return None
    return sorted(order_ids)


def _position_state(result: Any, trading_pair: str) -> tuple[str, str, str, bool]:
    if _is_incomplete(result):
        return "UNREADABLE", "n/a", "UNREADABLE", False
    positions = _parse_records(result, ("data", "positions"))
    if positions is None:
        return "UNREADABLE", "n/a", "UNREADABLE", False

    target = trading_pair.upper()
    matching = []
    for position in positions:
        is_open = _is_open(position)
        if is_open is None:
            return "UNREADABLE", "n/a", "UNREADABLE", False
        if not is_open:
            continue
        position_pair = _pair(position)
        if position_pair is None:
            return "UNREADABLE", "n/a", "UNREADABLE", False
        if position_pair == target:
            matching.append(position)

    if not matching:
        return "FLAT", "n/a", "n/a (flat)", True
    if len(matching) != 1:
        return "UNREADABLE", "n/a", "UNREADABLE", False

    position = matching[0]
    amount = _number(
        position,
        (
            "amount",
            "position_size",
            "net_amount_base",
            "position_amt",
            "positionAmt",
            "base_amount",
            "size",
        ),
    )
    if amount is None or amount == 0:
        return "UNREADABLE", "n/a", "UNREADABLE", False

    raw_side = next(
        (
            str(position[key]).strip().upper()
            for key in ("side", "position_side", "positionSide", "trade_type")
            if position.get(key) is not None and str(position[key]).strip()
        ),
        "",
    )
    if raw_side in {"LONG", "BUY"}:
        if amount < 0:
            return "UNREADABLE", "n/a", "UNREADABLE", False
        side = "LONG"
    elif raw_side in {"SHORT", "SELL"}:
        if amount > 0:
            return "UNREADABLE", "n/a", "UNREADABLE", False
        side = "SHORT"
    elif raw_side in {"", "BOTH"}:
        # Hummingbot represents a Binance ONEWAY position as PositionSide.BOTH.
        # In one-way mode the signed amount is the authoritative direction.
        side = "LONG" if amount > 0 else "SHORT"
    else:
        return "UNREADABLE", "n/a", "UNREADABLE", False

    pnl = _number(
        position,
        (
            "unrealized_pnl_quote",
            "unrealized_pnl",
            "unrealizedPnl",
            "unrealized_profit",
            "unrealizedProfit",
        ),
    )
    pnl_text = "n/a" if pnl is None else f"{pnl:g}"
    position_leverage = _number(position, ("leverage",))
    leverage_text = (
        "UNREADABLE"
        if position_leverage is None or position_leverage <= 0
        else f"{position_leverage:g}x"
    )
    return f"{side} {abs(amount):g}", pnl_text, leverage_text, True


def _available_balance(
    result: Any,
    account_name: str,
    connector_name: str,
    quote_asset: str,
) -> tuple[str, bool]:
    if not isinstance(result, dict):
        return "UNREADABLE", False
    account = result.get(account_name)
    if not isinstance(account, dict):
        return "UNREADABLE", False
    balances = account.get(connector_name)
    if not isinstance(balances, list) or not all(
        isinstance(balance, dict) for balance in balances
    ):
        return "UNREADABLE", False

    for balance in balances:
        token = balance.get("token", balance.get("asset"))
        if str(token).upper() != quote_asset.upper():
            continue
        available = _number(balance, ("available_units", "available", "units"))
        if available is None or available < 0:
            return "UNREADABLE", False
        return f"{available:g} {quote_asset}", True
    return f"0 {quote_asset}", True


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    leverage, leverage_source, leverage_ok = _resolve_leverage(config.leverage)

    mode_read = "CONFIRMED"
    try:
        mode_result = await client.trading.get_position_mode(
            config.account_name,
            config.connector_name,
        )
        mode = _parse_mode(mode_result)
        if mode == "UNKNOWN":
            mode_read = "UNREADABLE"
    except Exception as error:
        logger.warning("position mode read failed: %s", type(error).__name__)
        mode = "UNKNOWN"
        mode_read = f"ERROR: {type(error).__name__}"

    pair_state_ok = True
    try:
        orders_result = await client.trading.get_active_orders(
            account_names=[config.account_name],
            connector_names=[config.connector_name],
            trading_pairs=[config.trading_pair],
            limit=1000,
        )
        pair_order_ids = _order_ids(orders_result)
        if pair_order_ids is None:
            pair_state_ok = False
            pair_orders = "UNREADABLE"
        elif pair_order_ids:
            pair_orders = f"{len(pair_order_ids)} [{', '.join(pair_order_ids)}]"
        else:
            pair_orders = "0 [NONE]"
    except Exception as error:
        logger.warning("active order read failed: %s", type(error).__name__)
        pair_state_ok = False
        pair_orders = "UNREADABLE"

    try:
        positions_result = await client.trading.get_open_positions(
            config.account_name,
            config.connector_name,
        )
        pair_position, pair_pnl, exchange_leverage, position_ok = _position_state(
            positions_result,
            config.trading_pair,
        )
        pair_state_ok = pair_state_ok and position_ok
    except Exception as error:
        logger.warning("open position read failed: %s", type(error).__name__)
        pair_position = "UNREADABLE"
        pair_pnl = "n/a"
        exchange_leverage = "UNREADABLE"
        pair_state_ok = False

    quote_asset = config.trading_pair.rsplit("-", 1)[-1].upper()
    try:
        portfolio_result = await client.portfolio.get_state(
            account_names=[config.account_name],
            connector_names=[config.connector_name],
            refresh=True,
        )
        quote_available, balance_ok = _available_balance(
            portfolio_result,
            config.account_name,
            config.connector_name,
            quote_asset,
        )
    except Exception as error:
        logger.warning("balance read failed: %s", type(error).__name__)
        quote_available = "UNREADABLE"
        balance_ok = False

    oneway_ok = mode == "ONEWAY" and mode_read == "CONFIRMED"
    if oneway_ok:
        reason = "exchange positively confirmed ONEWAY mode"
    elif mode == "HEDGE":
        reason = "account is in HEDGE mode; AGT is one-way-only"
    else:
        reason = "position mode was not positively confirmed; fail closed"

    if not leverage_ok:
        leverage_match = "INVALID"
        leverage_state_read = "INVALID"
    elif pair_position == "FLAT":
        leverage_match = "N/A (flat; set immediately before grid create)"
        leverage_state_read = "CONFIRMED"
    elif exchange_leverage == "UNREADABLE":
        leverage_match = "UNREADABLE"
        leverage_state_read = "UNREADABLE"
    else:
        actual_leverage = float(exchange_leverage.removesuffix("x"))
        requested_leverage = float(leverage)
        leverage_matches = math.isclose(actual_leverage, requested_leverage)
        leverage_match = "YES" if leverage_matches else "NO"
        leverage_state_read = "CONFIRMED" if leverage_matches else "MISMATCH"

    lines = [
        f"Position Mode — {config.connector_name} / {config.trading_pair}",
        f"mode: {mode}",
        f"oneway_ok: {'YES' if oneway_ok else 'NO'}",
        f"mode_read: {mode_read}",
        f"pair_orders: {pair_orders}",
        f"pair_position: {pair_position}",
        f"pair_unrealized_pnl: {pair_pnl}",
        f"exchange_leverage: {exchange_leverage}",
        f"leverage_match: {leverage_match}",
        f"pair_state_read: {'CONFIRMED' if pair_state_ok else 'UNREADABLE'}",
        f"quote_available_balance: {quote_available}",
        f"balance_state_read: {'CONFIRMED' if balance_ok else 'UNREADABLE'}",
        f"leverage: {leverage}",
        f"leverage_source: {leverage_source}",
        f"leverage_state_read: {leverage_state_read}",
        f"reason: {reason}",
    ]
    return "\n".join(lines)
