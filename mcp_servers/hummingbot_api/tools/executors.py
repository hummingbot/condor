"""
Executor management tools for Hummingbot MCP Server.

This module provides business logic for managing trading executors including
creation, viewing, stopping, and position management with progressive disclosure.
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from handlers.cex._shared import get_correct_pair_format, validate_trading_pair
from mcp_servers.hummingbot_api.executor_preferences import executor_preferences
from mcp_servers.hummingbot_api.formatters.executors import (
    format_executor_detail,
    format_executor_schema_table,
    format_executors_table,
    format_positions_held_table,
    format_positions_summary,
)
from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest

logger = logging.getLogger("hummingbot-mcp")

# Internal fields injected by the MCP layer, not user-supplied
_INTERNAL_FIELDS = {"type", "executor_type", "id"}

# Executor types whose `side` must be Hummingbot TradeType-style ints (1=LONG/BUY, 2=SHORT/SELL, …).
_EXECUTOR_TYPES_WITH_TRADE_SIDE = frozenset(
    {"position_executor", "grid_executor", "order_executor", "dca_executor"},
)


def _coerce_trade_side_inplace(cfg: dict[str, Any], executor_type: str) -> bool:
    """Map common string sides to ints expected by hummingbot-api. Returns True if config changed."""
    if executor_type not in _EXECUTOR_TYPES_WITH_TRADE_SIDE or "side" not in cfg:
        return False
    raw = cfg["side"]
    if isinstance(raw, bool):
        return False
    if isinstance(raw, int):
        cfg["side"] = raw
        return False
    if isinstance(raw, float) and raw.is_integer():
        cfg["side"] = int(raw)
        return True
    if isinstance(raw, str):
        token = raw.strip().upper()
        if token in ("BUY", "LONG", "L", "B"):
            cfg["side"] = 1
            return True
        if token in ("SELL", "SHORT", "S"):
            cfg["side"] = 2
            return True
        if token.isdigit():
            cfg["side"] = int(token)
            return True
    return False


def _position_executor_positive_amount_issue(cfg: dict[str, Any]) -> str | None:
    """position_executor sizes in BASE units; zero/missing amount often yields exchange-side failures."""
    raw = cfg.get("amount")
    if raw is None:
        return (
            "position_executor requires positive `amount` in **base** units (e.g. BTC for BTC-USD), "
            "not USD. Convert: amount ≈ usd_notional / spot_price. Fetch a price/mark first if needed."
        )
    try:
        size = float(raw)
    except (TypeError, ValueError):
        return "position_executor `amount` must be a number (base currency size)."
    if size <= 0:
        return (
            "`amount` must be > 0 (base currency). For a ~\\$10 notional position: "
            "amount ≈ 10 / current_BTC_price, not literal 10 (that would mean 10 BTC)."
        )
    return None


def _quantize_base_amount_to_step(amount: float, step: float, rounding: str) -> float:
    """Snap base size to exchange lot step using decimal arithmetic."""
    if amount <= 0 or step <= 0:
        return amount
    d_amt = Decimal(str(amount))
    d_step = Decimal(str(step))
    mode = ROUND_UP if rounding == "up" else ROUND_DOWN
    units = (d_amt / d_step).to_integral_value(rounding=mode)
    return float(units * d_step)


async def _fetch_mid_price_for_pair(client: Any, connector_name: str, trading_pair: str) -> float | None:
    """Return mid/mark price for trading_pair from market_data.get_prices, or None."""
    try:
        data = await client.market_data.get_prices(
            connector_name=str(connector_name).strip(),
            trading_pairs=[trading_pair],
        )
    except Exception as exc:
        logger.warning(
            "position_executor sizing: price fetch failed for %s/%s: %s",
            connector_name,
            trading_pair,
            exc,
        )
        return None
    prices: dict[Any, Any] = {}
    if isinstance(data, dict):
        raw = data.get("prices")
        if isinstance(raw, dict):
            prices = raw
    needle = trading_pair
    hit = prices.get(needle)
    if hit is None:
        needle_u = needle.upper().replace("_", "-")
        for sym, val in prices.items():
            if str(sym).upper().replace("_", "-") == needle_u:
                hit = val
                break
    try:
        p = float(hit) if hit is not None else None
        if p is not None and math.isfinite(p) and p > 0:
            return p
    except (TypeError, ValueError):
        pass
    return None


async def _apply_position_amount_from_trading_rules(
    client: Any, merged_config: dict[str, Any]
) -> tuple[str | None, str]:
    """Adjust position_executor base `amount` using connector trading rules.

    Reads min_base_amount_increment (lot step), min_order_size (base minimum),
    and min_notional_size (quote minimum) via Hummingbot API trading rules.

    Returns:
        (error_message_or_None, note_for_user_when_adjusted_or_empty)

    Mutates merged_config["amount"] and may normalize merged_config["trading_pair"] to rule keys.
    """
    connector_name = merged_config.get("connector_name")
    trading_pair = merged_config.get("trading_pair")
    if not connector_name or not trading_pair:
        return None, ""

    raw_amt = merged_config.get("amount")
    try:
        original = float(raw_amt)
    except (TypeError, ValueError):
        return None, ""

    if original <= 0:
        return None, ""

    try:
        trading_rules = await client.connectors.get_trading_rules(
            connector_name=str(connector_name).strip()
        )
    except Exception as exc:
        logger.warning(
            "position_executor sizing: trading rules unavailable for %s: %s",
            connector_name,
            exc,
        )
        return None, ""

    if not isinstance(trading_rules, dict) or not trading_rules:
        return None, ""

    rules_pair = get_correct_pair_format(trading_rules, str(trading_pair))
    if rules_pair:
        if rules_pair != merged_config.get("trading_pair"):
            merged_config["trading_pair"] = rules_pair
        rules = trading_rules.get(rules_pair)
    else:
        rules = trading_rules.get(str(trading_pair))

    if not isinstance(rules, dict):
        return None, ""

    try:
        step = float(rules.get("min_base_amount_increment") or 0)
    except (TypeError, ValueError):
        step = 0.0
    try:
        min_order = float(rules.get("min_order_size") or 0)
    except (TypeError, ValueError):
        min_order = 0.0
    try:
        min_notional = float(rules.get("min_notional_size") or 0)
    except (TypeError, ValueError):
        min_notional = 0.0

    final_pair = str(merged_config["trading_pair"])
    snapped_down = (
        _quantize_base_amount_to_step(original, step, "down") if step > 0 else original
    )

    targets: list[float] = [snapped_down]
    if min_order > 0:
        targets.append(_quantize_base_amount_to_step(min_order, step, "up") if step > 0 else min_order)

    mid = None
    if min_notional > 0:
        mid = await _fetch_mid_price_for_pair(client, str(connector_name).strip(), final_pair)
        if mid and mid > 0:
            need_base = min_notional / mid
            targets.append(
                _quantize_base_amount_to_step(need_base, step, "up") if step > 0 else need_base
            )

    amount = max(targets)
    if step > 0 and amount > 0:
        amount = _quantize_base_amount_to_step(amount, step, "up")

    if amount <= 0:
        return (
            f"Adjusted position size would be zero for {final_pair}. "
            f"Check min_base_amount_increment (step={step}) and requested amount ({original}).",
            "",
        )

    if mid and min_notional > 0:
        try:
            notion = Decimal(str(amount)) * Decimal(str(mid))
            min_q = Decimal(str(min_notional))
            slack = max(Decimal("0.05"), min_q * Decimal("0.0005"))
        except Exception:
            notion = Decimal(str(amount * mid))
            min_q = Decimal(str(min_notional))
            slack = max(Decimal("0.05"), min_q * Decimal("0.0005"))
        if notion < min_q - slack:
            return (
                f"Cannot satisfy min notional ~${min_notional:.2f} for {final_pair} at price {mid:g} "
                f"even after sizing to step (last size {amount}). Increase target notional or amount.",
                "",
            )

    if min_order > 0 and amount + 1e-12 < min_order:
        return (
            f"Position size {amount} is below min_order_size {min_order} for {final_pair}.",
            "",
        )

    merged_config["amount"] = amount
    note = ""
    if abs(amount - original) > max(1e-12, (step or 1e-12) * 0.01):
        parts = [f"`amount` adjusted from {original:g} → {amount:g} for `{final_pair}` (exchange rules)."]
        if step > 0:
            parts.append(f"Step (min_base_amount_increment): {step:g}.")
        if min_order > 0:
            parts.append(f"Min base size: {min_order:g}.")
        if min_notional > 0:
            if mid:
                parts.append(f"Min notional: ~${min_notional:.2f} (price ≈ {mid:g}).")
            else:
                parts.append(
                    f"Min notional: ~${min_notional:.2f} (mid price unavailable — confirm notional manually)."
                )
        note = " ".join(parts) + "\n"
    return None, note


def _normalize_hyperliquid_perp_usdc_pair(connector_name: str, trading_pair: str) -> str | None:
    """Hyperliquid perpetuals often list *-USD; price feeds may return *-USDC."""
    if not trading_pair or "hyperliquid" not in connector_name.lower():
        return None
    if "perpetual" not in connector_name.lower():
        return None
    raw = trading_pair.strip().replace("_", "-")
    if ":" in raw.upper():
        return None
    parts = raw.rsplit("-", 1)
    if len(parts) != 2 or parts[1].upper() != "USDC":
        return None
    return f"{parts[0]}-USD"


def _hoist_executor_create_fields(merged_config: dict[str, Any], request: ManageExecutorsRequest) -> None:
    """Merge tool-level connector/pair and lift them out of nested dicts LLMs often use."""

    def _empty(v: Any) -> bool:
        return v is None or (isinstance(v, str) and not v.strip())

    if _empty(merged_config.get("connector_name")) and request.connector_name:
        merged_config["connector_name"] = str(request.connector_name).strip()
    if _empty(merged_config.get("trading_pair")) and request.trading_pair:
        merged_config["trading_pair"] = str(request.trading_pair).strip()

    tbc = merged_config.get("triple_barrier_config")
    if isinstance(tbc, dict):
        if _empty(merged_config.get("connector_name")) and not _empty(tbc.get("connector_name")):
            merged_config["connector_name"] = str(tbc["connector_name"]).strip()
        if _empty(merged_config.get("trading_pair")) and not _empty(tbc.get("trading_pair")):
            merged_config["trading_pair"] = str(tbc["trading_pair"]).strip()

    for nest_key in ("config", "position_executor", "executor_config", "params", "settings"):
        nested = merged_config.get(nest_key)
        if not isinstance(nested, dict):
            continue
        if _empty(merged_config.get("connector_name")) and not _empty(nested.get("connector_name")):
            merged_config["connector_name"] = str(nested["connector_name"]).strip()
        if _empty(merged_config.get("trading_pair")) and not _empty(nested.get("trading_pair")):
            merged_config["trading_pair"] = str(nested["trading_pair"]).strip()



def _truncate_audit_text(text: str, max_len: int = 12000) -> str:
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…(+{len(text) - max_len} chars)"


def _condor_mcp_audit(event: dict[str, Any]) -> None:
    """Append one JSON line to CONDOR_MCP_AUDIT_LOG when set (e.g. MCP stdio detached from Condor logs)."""
    path = os.environ.get("CONDOR_MCP_AUDIT_LOG", "").strip()
    if not path:
        return
    payload = dict(event)
    payload["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("CONDOR_MCP_AUDIT_LOG write failed (%s): %s", path, exc)


def _exception_audit_detail(exc: BaseException) -> str:
    detail = repr(exc)
    resp = getattr(exc, "response", None)
    if resp is None:
        return detail
    txt = getattr(resp, "text", None)
    if txt is None:
        raw = getattr(resp, "content", None)
        if isinstance(raw, (bytes, bytearray)):
            txt = raw.decode(errors="replace")
        elif raw is not None:
            txt = str(raw)
    if txt:
        detail = f"{detail} | http_body={_truncate_audit_text(txt)}"
    return detail


def validate_executor_config(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate config keys against the backend schema properties.

    Returns a list of error strings. An empty list means the config is valid.
    """
    errors: list[str] = []
    _validate_level(config, schema, "", errors)
    return errors


def _validate_level(config: dict[str, Any], schema: dict[str, Any], path: str, errors: list[str]) -> None:
    """Recursively validate config keys against schema properties."""
    properties = schema.get("properties", {})
    if not properties:
        return

    allowed = set(properties.keys())

    for key in config:
        if not path and key in _INTERNAL_FIELDS:
            continue
        if key not in allowed:
            field_list = ", ".join(sorted(allowed - _INTERNAL_FIELDS))
            location = f" inside '{path}'" if path else ""
            errors.append(f"Unknown field '{key}'{location}. Allowed fields: {field_list}")
            continue
        # Recurse into nested objects
        prop_schema = properties[key]
        if isinstance(prop_schema, dict) and isinstance(config[key], dict) and "properties" in prop_schema:
            _validate_level(config[key], prop_schema, key, errors)


async def manage_executors(client: Any, request: ManageExecutorsRequest) -> dict[str, Any]:
    """
    Manage executors with progressive disclosure.

    Args:
        client: Hummingbot API client
        request: ManageExecutorsRequest with action and parameters

    Returns:
        Dictionary containing results and formatted output
    """
    flow_stage = request.get_flow_stage()

    if flow_stage == "list_types":
        # Brief static response — full descriptions are in the tool docstring
        formatted = (
            "Available Executor Types:\n\n"
            "- **position_executor** — Directional trading with entry, stop-loss, and take-profit\n"
            "- **dca_executor** — Dollar-cost averaging for gradual position building\n"
            "- **grid_executor** — Grid trading across multiple price levels in ranging markets\n"
            "- **order_executor** — Simple BUY/SELL order with execution strategy\n"
            "- **lp_executor** — Liquidity provision on CLMM DEXs (Meteora, Raydium)\n\n"
            "Provide `executor_type` to see the configuration schema."
        )

        return {
            "action": "list_types",
            "formatted_output": formatted,
            "next_step": "Call again with 'executor_type' to see the configuration schema",
            "example": "manage_executors(executor_type='position_executor')",
        }

    elif flow_stage == "show_schema":
        # Stage 2: Show config schema with user defaults
        try:
            schema = await client.executors.get_executor_config_schema(request.executor_type)
        except Exception as e:
            return {
                "action": "show_schema",
                "error": f"Failed to get schema for {request.executor_type}: {e}",
                "formatted_output": f"Error: Failed to get schema for {request.executor_type}: {e}",
            }

        # Get user defaults
        user_defaults = executor_preferences.get_defaults(request.executor_type)

        # Get the guide from the markdown file
        executor_guide = executor_preferences.get_executor_guide(request.executor_type)

        formatted = f"Configuration Schema for {request.executor_type}\n\n"
        if executor_guide:
            formatted += f"{executor_guide}\n\n"

        formatted += format_executor_schema_table(schema, user_defaults)

        if user_defaults:
            formatted += f"\n\nYour saved defaults for {request.executor_type}:\n"
            for key, value in user_defaults.items():
                formatted += f"  {key}: {value}\n"
            formatted += f"\nPreferences file: {executor_preferences.get_preferences_path()}"

        return {
            "action": "show_schema",
            "executor_type": request.executor_type,
            "schema": schema,
            "user_defaults": user_defaults,
            "formatted_output": formatted,
            "next_step": "Call with action='create' and executor_config to create an executor",
            "example": f"manage_executors(action='create', executor_type='{request.executor_type}', executor_config={{...}})",
        }

    elif flow_stage == "create":
        # Stage 3: Create executor
        executor_type = request.executor_type or request.executor_config.get("type") or request.executor_config.get("executor_type")

        if not executor_type:
            return {
                "action": "create",
                "error": "executor_type is required for creating an executor",
                "formatted_output": "Error: Please provide executor_type",
            }

        # Merge with defaults
        merged_config = executor_preferences.merge_with_defaults(executor_type, request.executor_config)

        # Ensure type is set in config
        if "type" not in merged_config and "executor_type" not in merged_config:
            merged_config["type"] = executor_type

        _hoist_executor_create_fields(merged_config, request)

        cn_hl = merged_config.get("connector_name")
        tp_hl = merged_config.get("trading_pair")
        if cn_hl and tp_hl:
            usd_pair = _normalize_hyperliquid_perp_usdc_pair(str(cn_hl), str(tp_hl))
            if usd_pair:
                merged_config["trading_pair"] = usd_pair

        side_normalized = _coerce_trade_side_inplace(merged_config, executor_type)
        if side_normalized:
            logger.info(
                "create_executor: normalized side from %r to %s for %s",
                request.executor_config.get("side") if request.executor_config else None,
                merged_config.get("side"),
                executor_type,
            )

        if executor_type == "position_executor":
            def _missing_core_pair_field() -> bool:
                cn = merged_config.get("connector_name")
                tp = merged_config.get("trading_pair")
                return cn is None or (isinstance(cn, str) and not cn.strip()) or tp is None or (
                    isinstance(tp, str) and not tp.strip()
                )

            if _missing_core_pair_field():
                return {
                    "action": "create",
                    "error": (
                        "position_executor requires connector_name and trading_pair at the top level of "
                        "executor_config (not only inside triple_barrier_config). "
                        "You may also pass manage_executors(..., connector_name=..., trading_pair=...) "
                        "with the create call."
                    ),
                    "formatted_output": (
                        "Error: position_executor needs `connector_name` and `trading_pair` as top-level keys "
                        "inside executor_config — the same nesting level as `amount` and `leverage`, "
                        "not only under triple_barrier_config. Alternatively pass manage_executors's "
                        "connector_name and trading_pair arguments alongside the create call."
                    ),
                }

            sizing_issue = _position_executor_positive_amount_issue(merged_config)
            if sizing_issue:
                return {
                    "action": "create",
                    "error": sizing_issue,
                    "formatted_output": f"Error: {sizing_issue}",
                }

        # Validate config fields against backend schema before sending
        try:
            schema = await client.executors.get_executor_config_schema(executor_type)
            validation_errors = validate_executor_config(merged_config, schema)
            if validation_errors:
                error_list = "\n".join(f"  - {e}" for e in validation_errors)
                return {
                    "action": "create",
                    "error": f"Invalid executor configuration:\n{error_list}",
                    "formatted_output": (
                        f"Error: Invalid configuration for {executor_type}:\n\n"
                        f"{error_list}\n\n"
                        f"Please fix the fields above and try again."
                    ),
                }
        except Exception:
            pass  # If schema fetch fails, skip validation

        # Validate and normalize trading pair format before creating executor.
        # This is especially important for Hyperliquid HIP3 symbols (issuer:symbol-QUOTE).
        pair_normalization_note = ""
        connector_name = merged_config.get("connector_name")
        trading_pair = merged_config.get("trading_pair")
        if connector_name and trading_pair:
            try:
                validation_cache: dict[str, Any] = {}
                is_valid, error_msg, suggestions, correct_pair = await validate_trading_pair(
                    validation_cache, client, connector_name, trading_pair
                )
                if not is_valid:
                    suggestions_text = f" Suggestions: {', '.join(suggestions)}" if suggestions else ""
                    return {
                        "action": "create",
                        "error": f"Invalid trading pair '{trading_pair}' for {connector_name}. {error_msg}.{suggestions_text}",
                        "formatted_output": (
                            f"Error: Invalid trading pair '{trading_pair}' for {connector_name}.\n"
                            f"{error_msg or 'Pair validation failed.'}\n"
                            f"{suggestions_text}".strip()
                        ),
                    }

                if correct_pair and correct_pair != trading_pair:
                    merged_config["trading_pair"] = correct_pair
                    pair_normalization_note = (
                        f"\nTrading pair normalized: {trading_pair} -> {correct_pair}\n"
                    )
            except Exception as e:
                logger.warning(
                    "Trading pair validation failed for %s on %s, continuing with original pair: %s",
                    trading_pair,
                    connector_name,
                    e,
                )

        amount_rules_note = ""
        if executor_type == "position_executor":
            amt_err, amount_rules_note = await _apply_position_amount_from_trading_rules(
                client, merged_config
            )
            if amt_err:
                return {
                    "action": "create",
                    "error": amt_err,
                    "formatted_output": f"Error: {amt_err}",
                }

        account = request.account_name or "master_account"
        # Check both top-level param and executor_config (agents sometimes put it in the wrong place)
        controller_id = request.controller_id or merged_config.pop("controller_id", None) or "main"

        logger.info(
            "create_executor: controller_id=%r (request=%r, config_had=%r), type=%s, account=%s",
            controller_id,
            request.controller_id,
            "controller_id" in (request.executor_config or {}),
            executor_type,
            account,
        )
        _condor_mcp_audit(
            {
                "event": "executor_create_request",
                "executor_type": executor_type,
                "account": account,
                "controller_id": controller_id,
                "merged_config": merged_config,
            }
        )

        try:
            result = await client.executors.create_executor(
                executor_config=merged_config,
                account_name=account,
                controller_id=controller_id,
            )

            # Save as default if requested
            if request.save_as_default:
                executor_preferences.update_defaults(executor_type, request.executor_config)

            executor_id = result.get("executor_id") or result.get("id")

            _condor_mcp_audit(
                {
                    "event": "executor_create_ok",
                    "executor_type": executor_type,
                    "account": account,
                    "controller_id": controller_id,
                    "executor_id": executor_id,
                    "result_keys": sorted(result.keys()) if isinstance(result, dict) else None,
                }
            )

            formatted = f"Executor created successfully!\n\n"
            formatted += f"Executor ID: {executor_id or 'N/A'}\n"
            formatted += f"Type: {executor_type}\n"
            formatted += f"Account: {account}\n"
            if pair_normalization_note:
                formatted += pair_normalization_note
            if amount_rules_note:
                formatted += ("\n" if not amount_rules_note.startswith("\n") else "") + amount_rules_note

            if request.save_as_default:
                formatted += f"\nConfiguration saved as default for {executor_type}"

            return {
                "action": "create",
                "executor_id": executor_id,
                "executor_type": executor_type,
                "account": account,
                "config_used": merged_config,
                "saved_as_default": request.save_as_default,
                "result": result,
                "formatted_output": formatted,
            }

        except Exception as e:
            _condor_mcp_audit(
                {
                    "event": "executor_create_failed",
                    "executor_type": executor_type,
                    "account": account,
                    "controller_id": controller_id,
                    "error": _exception_audit_detail(e),
                }
            )
            return {
                "action": "create",
                "error": str(e),
                "formatted_output": f"Error creating executor: {e}",
            }

    elif flow_stage == "search":
        # Search executors, or get detail for a specific executor_id
        try:
            if request.executor_id:
                # Get specific executor detail
                result = await client.executors.get_executor(request.executor_id)
                formatted = format_executor_detail(result)
                return {
                    "action": "search",
                    "executor_id": request.executor_id,
                    "executor": result,
                    "formatted_output": formatted,
                }

            result = await client.executors.search_executors(
                account_names=request.account_names,
                connector_names=request.connector_names,
                trading_pairs=request.trading_pairs,
                executor_types=request.executor_types,
                status=request.status,
                cursor=request.cursor,
                limit=request.limit,
                controller_ids=request.controller_ids,
            )

            executors = result.get("data", result) if isinstance(result, dict) else result
            if not isinstance(executors, list):
                executors = [executors] if executors else []

            formatted = f"Executors Found: {len(executors)}\n\n"
            formatted += format_executors_table(executors)

            # Add pagination info if available
            if isinstance(result, dict) and "next_cursor" in result:
                formatted += f"\n\nNext cursor: {result.get('next_cursor')}"

            return {
                "action": "search",
                "executors": executors,
                "count": len(executors),
                "cursor": result.get("next_cursor") if isinstance(result, dict) else None,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "search",
                "error": str(e),
                "formatted_output": f"Error searching executors: {e}",
            }

    elif flow_stage == "stop":
        # Stage 6: Stop executor
        try:
            result = await client.executors.stop_executor(
                executor_id=request.executor_id,
                keep_position=request.keep_position,
            )

            formatted = f"Executor stopped successfully!\n\n"
            formatted += f"Executor ID: {request.executor_id}\n"
            formatted += f"Keep Position: {request.keep_position}\n"

            return {
                "action": "stop",
                "executor_id": request.executor_id,
                "keep_position": request.keep_position,
                "result": result,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "stop",
                "error": str(e),
                "formatted_output": f"Error stopping executor {request.executor_id}: {e}",
            }

    elif flow_stage == "get_logs":
        # Get executor logs via direct API call (not yet in client library)
        try:
            params = {"limit": request.limit}
            if request.log_level:
                params["level"] = request.log_level.upper()

            resp = await client.executors.session.get(
                f"{client.executors.base_url}/executors/{request.executor_id}/logs",
                params=params,
            )
            resp.raise_for_status()
            result = await resp.json()

            logs = result.get("logs", [])
            total = result.get("total_count", len(logs))

            formatted = f"Executor Logs: {request.executor_id}\n"
            formatted += f"Total entries: {total}"
            if request.log_level:
                formatted += f" (filtered: {request.log_level.upper()})"
            formatted += f", showing: {len(logs)}\n\n"

            if not logs:
                formatted += "No log entries found. Note: logs are only available for active executors and are cleared on completion."
            else:
                for entry in logs:
                    ts = entry.get("timestamp", "")
                    level = entry.get("level", "")
                    msg = entry.get("message", "")
                    formatted += f"[{ts}] {level}: {msg}\n"
                    exc = entry.get("exc_info")
                    if exc:
                        formatted += f"  Exception: {exc}\n"

            return {
                "action": "get_logs",
                "executor_id": request.executor_id,
                "logs": logs,
                "total_count": total,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "get_logs",
                "error": str(e),
                "formatted_output": f"Error getting logs for executor {request.executor_id}: {e}",
            }

    elif flow_stage == "get_preferences":
        # Stage 8: Get saved preferences (returns raw markdown content)
        raw_content = executor_preferences.get_raw_content()

        formatted = f"Preferences file: {executor_preferences.get_preferences_path()}\n\n"
        formatted += raw_content

        return {
            "action": "get_preferences",
            "executor_type": request.executor_type,
            "raw_content": raw_content,
            "preferences_path": executor_preferences.get_preferences_path(),
            "formatted_output": formatted,
        }

    elif flow_stage == "save_preferences":
        # Stage 9: Save full preferences file content
        executor_preferences.save_content(request.preferences_content)

        formatted = f"Preferences file saved successfully.\n\n"
        formatted += f"Preferences file: {executor_preferences.get_preferences_path()}"

        return {
            "action": "save_preferences",
            "preferences_path": executor_preferences.get_preferences_path(),
            "formatted_output": formatted,
        }

    elif flow_stage == "reset_preferences":
        # Stage 10: Reset preferences to defaults (preserves YAML configs)
        preserved = executor_preferences.reset_to_defaults()
        preserved_count = sum(1 for c in preserved.values() if c)

        formatted = "Preferences documentation updated to latest version.\n\n"
        if preserved_count > 0:
            preserved_names = [k for k, v in preserved.items() if v]
            formatted += f"Preserved {preserved_count} config(s): {', '.join(preserved_names)}\n"
        else:
            formatted += "No existing configs to preserve.\n"
        formatted += f"\nPreferences file: {executor_preferences.get_preferences_path()}"

        return {
            "action": "reset_preferences",
            "preserved_configs": preserved,
            "preserved_count": preserved_count,
            "formatted_output": formatted,
        }

    # Position management stages (merged from manage_executor_positions)

    elif flow_stage == "positions_summary":
        # Get all positions, or specific position if connector_name + trading_pair given
        try:
            if request.connector_name and request.trading_pair:
                # Get specific position detail
                account = request.account_name or "master_account"
                result = await client.executors.get_position_held(
                    connector_name=request.connector_name,
                    trading_pair=request.trading_pair,
                    account_name=account,
                    controller_id=request.controller_id,
                )

                formatted = f"Position Details\n\n"
                formatted += f"Connector: {request.connector_name}\n"
                formatted += f"Trading Pair: {request.trading_pair}\n"
                formatted += f"Account: {account}\n\n"

                if result:
                    positions = [result] if not isinstance(result, list) else result
                    formatted += format_positions_held_table(positions)
                else:
                    formatted += "No position found for this connector/pair combination."

                return {
                    "action": "positions_summary",
                    "connector_name": request.connector_name,
                    "trading_pair": request.trading_pair,
                    "account": account,
                    "position": result,
                    "formatted_output": formatted,
                }

            result = await client.executors.get_positions_summary(
                controller_id=request.controller_id,
            )

            positions = result.get("positions", result) if isinstance(result, dict) else result
            if not isinstance(positions, list):
                positions = [positions] if positions else []

            formatted = f"Positions Held Summary\n\n"

            if isinstance(result, dict) and any(k in result for k in ["total_positions", "total_value", "by_connector"]):
                formatted += format_positions_summary(result)
                if positions:
                    formatted += "\n\nPositions Detail:\n"
                    formatted += format_positions_held_table(positions)
            else:
                formatted += format_positions_held_table(positions)

            return {
                "action": "positions_summary",
                "positions": positions,
                "summary": result if isinstance(result, dict) else {"positions": positions},
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "positions_summary",
                "error": str(e),
                "formatted_output": f"Error getting positions: {e}",
            }

    elif flow_stage == "clear_position":
        # Clear a position that was closed manually
        account = request.account_name or "master_account"
        try:
            result = await client.executors.clear_position_held(
                connector_name=request.connector_name,
                trading_pair=request.trading_pair,
                account_name=account,
                controller_id=request.controller_id,
            )

            formatted = f"Position cleared successfully!\n\n"
            formatted += f"Connector: {request.connector_name}\n"
            formatted += f"Trading Pair: {request.trading_pair}\n"
            formatted += f"Account: {account}\n"

            return {
                "action": "clear_position",
                "connector_name": request.connector_name,
                "trading_pair": request.trading_pair,
                "account": account,
                "result": result,
                "formatted_output": formatted,
            }

        except Exception as e:
            return {
                "action": "clear_position",
                "error": str(e),
                "formatted_output": f"Error clearing position: {e}",
            }

    elif flow_stage == "performance_report":
        try:
            result = await client.executors.get_performance_report(
                controller_id=request.controller_id,
            )
            formatted = "Executor Performance Report\n\n"
            if request.controller_id:
                formatted += f"Controller: {request.controller_id}\n\n"
            if isinstance(result, dict):
                for key, value in result.items():
                    formatted += f"{key}: {value}\n"
            else:
                formatted += str(result)
            return {
                "action": "performance_report",
                "result": result,
                "formatted_output": formatted,
            }
        except Exception as e:
            return {
                "action": "performance_report",
                "error": str(e),
                "formatted_output": f"Error getting performance report: {e}",
            }

    else:
        return {
            "action": "unknown",
            "error": f"Unknown flow stage: {flow_stage}",
            "formatted_output": f"Error: Unknown flow stage: {flow_stage}",
        }
