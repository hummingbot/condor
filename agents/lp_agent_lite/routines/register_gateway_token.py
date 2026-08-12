"""Preview or unconditionally add-and-verify one exact Gateway token tuple."""

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_agent_lite.routines._reporting import DiagnosticTrace, report_result

CATEGORY = "Gateway Token Registry"
_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class Config(BaseModel):
    """Preview or reconcile one selected Orca token in Gateway's registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    network: Literal["solana-mainnet-beta"] = "solana-mainnet-beta"
    mint: StrictStr = Field(min_length=32, max_length=44)
    symbol: StrictStr = Field(min_length=1, max_length=32)
    decimals: StrictInt = Field(ge=0, le=255)
    preview: bool = True
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def token_identity(self):
        if not _ADDRESS.fullmatch(self.mint):
            raise ValueError("mint must be a Solana base58 address")
        if not _SYMBOL.fullmatch(self.symbol):
            raise ValueError("symbol contains unsupported characters")
        return self


def _dump(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(raw) >= 1_900:
        return json.dumps(
            {
                "schema": "lp_agent_lite.gateway_token.v1",
                "status": "unavailable",
                "mutation": payload.get("mutation", False),
                "reason": "bounded result exceeded transport limit",
            },
            separators=(",", ":"),
        )
    return raw


def _safe_error(error: BaseException) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    text = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|authorization)=([^\s,&]+)",
        r"\1=***",
        text,
    )
    text = re.sub(r"(?i)(https?://[^?\s]+)\?[^\s]+", r"\1?[redacted]", text)
    return text[:180]


async def _get_client(context: Any) -> Any:
    from config_manager import get_client

    chat_id = getattr(context, "_chat_id", 0) or 0
    return await get_client(chat_id, context=context)


def _token_rows(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        wrappers = [key for key in ("tokens", "data", "result") if key in response]
        if len(wrappers) != 1:
            raise ValueError("Gateway token registry wrapper is ambiguous")
        response = response[wrappers[0]]
    if not isinstance(response, list) or not all(
        isinstance(row, dict) for row in response
    ):
        raise ValueError("Gateway token registry must be an object list")
    return response


def _normalize(row: dict[str, Any]) -> tuple[str, str, int]:
    address = row.get("address") or row.get("mint")
    symbol = row.get("symbol")
    decimals = row.get("decimals")
    if (
        not isinstance(address, str)
        or not isinstance(symbol, str)
        or isinstance(decimals, bool)
    ):
        raise ValueError("Gateway token registry contains invalid metadata")
    try:
        parsed_decimals = int(decimals)
    except (TypeError, ValueError) as exc:
        raise ValueError("Gateway token registry contains invalid decimals") from exc
    if str(parsed_decimals) != str(decimals) and not isinstance(decimals, int):
        raise ValueError("Gateway token registry contains non-integral decimals")
    return address, symbol, parsed_decimals


def _classify(rows: list[dict[str, Any]], config: Config) -> tuple[
    Literal["exact", "case_alias", "absent", "conflict", "ambiguous"],
    str | None,
    tuple[str, str, int] | None,
]:
    normalized = [_normalize(row) for row in rows]
    by_address = [row for row in normalized if row[0] == config.mint]
    by_symbol = [
        row for row in normalized if row[1].casefold() == config.symbol.casefold()
    ]
    if len(by_address) > 1 or len(by_symbol) > 1:
        return (
            "ambiguous",
            "duplicate address or case-insensitive symbol entries in Gateway registry",
            None,
        )
    if by_address:
        registered = by_address[0]
        if registered[2] != config.decimals:
            return "conflict", "mint is registered with different decimals", registered
        if registered[1] == config.symbol:
            return "exact", None, registered
        if registered[1].casefold() == config.symbol.casefold():
            return "case_alias", None, registered
        return "conflict", "mint is registered with a different symbol", registered
    if by_symbol:
        return "conflict", "symbol is registered to a different mint", by_symbol[0]
    return "absent", None, None


async def _read_registry(gateway: Any, config: Config) -> list[dict[str, Any]]:
    return _token_rows(
        await asyncio.wait_for(
            gateway.get_network_tokens(config.network),
            timeout=config.timeout_seconds,
        )
    )


def _base(config: Config) -> dict[str, Any]:
    return {
        "schema": "lp_agent_lite.gateway_token.v1",
        "network": config.network,
        "token": [config.mint, config.symbol, config.decimals],
    }


async def run(config: Config, context: Any) -> str:
    base = _base(config)
    trace = DiagnosticTrace()
    trace.record("input_validated", preview=config.preview, network=config.network)

    async def finish(payload: dict[str, Any]) -> str:
        return await report_result(
            _dump(payload),
            routine_name="register_gateway_token",
            title="LP Agent Lite — Gateway Token Registration Diagnostic",
            config=config,
            trace=trace,
            context=context,
        )

    try:
        client = await _get_client(context)
        gateway = getattr(client, "gateway", None)
        if gateway is None:
            raise ValueError("Gateway registry client is unavailable")
    except Exception as exc:
        trace.record("gateway_client", "error", error_type=type(exc).__name__)
        return await finish(
            {
                **base,
                "status": "unavailable",
                "mutation": False,
                "reason": _safe_error(exc),
            }
        )

    if config.preview:
        try:
            before = await _read_registry(gateway, config)
            state, reason, registered = _classify(before, config)
        except Exception as exc:
            trace.record(
                "registry_read_before",
                "error",
                error_type=type(exc).__name__,
            )
            return await finish(
                {
                    **base,
                    "status": "unavailable",
                    "mutation": False,
                    "reason": _safe_error(exc),
                }
            )
        trace.record("registry_read_before", state=state, row_count=len(before))

        if state in {"exact", "case_alias"}:
            return await finish(
                {
                    **base,
                    "status": "confirmed",
                    "mutation": False,
                    "present": True,
                    "registered_token": list(registered),
                    "canonical_symbol": registered[1],
                    "symbol_match": "exact" if state == "exact" else "case_alias",
                }
            )
        if state in {"conflict", "ambiguous"}:
            trace.record(
                "registration_decision", "rejected", state=state, reason=reason
            )
            return await finish(
                {
                    **base,
                    "status": (
                        "rejected_before_submit"
                        if state == "conflict"
                        else "ambiguous"
                    ),
                    "mutation": False,
                    "present": False,
                    "reason": reason,
                    "registered_token": list(registered) if registered else None,
                }
            )
        trace.record("registration_decision", "preview", state=state)
        return await finish(
            {
                **base,
                "status": "preview",
                "outcome": "rejected_before_submit",
                "mutation": False,
                "present": False,
                "reason": "token is absent; live registration was not authorized",
            }
        )

    add_error = None
    try:
        await asyncio.wait_for(
            gateway.add_token(
                network_id=config.network,
                address=config.mint,
                symbol=config.symbol,
                decimals=config.decimals,
                name=config.symbol,
            ),
            timeout=config.timeout_seconds,
        )
        trace.record("token_add_submitted", mutation_attempted=True)
    except Exception as exc:
        add_error = _safe_error(exc)
        trace.record(
            "token_add_submitted",
            "uncertain",
            mutation_attempted=True,
            error_type=type(exc).__name__,
        )

    try:
        after = await _read_registry(gateway, config)
        state, reason, registered = _classify(after, config)
    except Exception as exc:
        trace.record(
            "registry_read_after",
            "error",
            error_type=type(exc).__name__,
        )
        return await finish(
            {
                **base,
                "status": "uncertain",
                "mutation": True,
                "present": None,
                "reason": f"post-add registry unavailable: {_safe_error(exc)}",
            }
        )
    trace.record("registry_read_after", state=state, row_count=len(after))

    if state in {"exact", "case_alias"}:
        result = {
            **base,
            "status": "confirmed",
            "mutation": True,
            "present": True,
            "registered_token": list(registered),
            "canonical_symbol": registered[1],
            "symbol_match": "exact" if state == "exact" else "case_alias",
        }
        if add_error:
            result["warning"] = (
                "add response failed but exact registry truth confirms the token"
            )
        return await finish(result)
    if state in {"conflict", "ambiguous"}:
        return await finish(
            {
                **base,
                "status": "ambiguous",
                "mutation": True,
                "present": False,
                "reason": reason,
                "registered_token": list(registered) if registered else None,
            }
        )
    return await finish(
        {
            **base,
            "status": "uncertain",
            "mutation": True,
            "present": False,
            "reason": add_error
            or "add returned but exact tuple is absent after verification",
        }
    )
