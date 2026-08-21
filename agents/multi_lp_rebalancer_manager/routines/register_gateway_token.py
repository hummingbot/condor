"""Register one selected non-native Solana token under its uppercase execution symbol."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

CATEGORY = "Gateway Token Registry"
SCHEMA = "multi_lp_rebalancer_manager.gateway_token.v1"
MAX_RESULT_CHARS = 1_899
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,31}$")


class Config(BaseModel):
    """Preview or submit one bounded Gateway token-metadata registration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    network: Literal["solana-mainnet-beta"]
    mint: StrictStr = Field(min_length=32, max_length=44)
    symbol: StrictStr = Field(min_length=1, max_length=32)
    decimals: StrictInt = Field(ge=0, le=18)
    preview: bool
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=30)

    @field_validator("symbol", mode="before")
    @classmethod
    def canonical_execution_symbol(cls, value: Any) -> Any:
        """Match the uppercase token keys used by controller trading pairs."""
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not _ADDRESS.fullmatch(self.mint):
            raise ValueError("mint must be a Solana base58 address")
        if not _SYMBOL.fullmatch(self.symbol):
            raise ValueError("symbol contains unsupported characters")
        return self


Config.model_rebuild(
    _types_namespace={
        "Literal": Literal,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
    }
)


def _safe_error(error: BaseException) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    text = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|authorization)=([^\s,&]+)",
        r"\1=***",
        text,
    )
    text = re.sub(r"(?i)(https?://[^?\s]+)\?[^\s]+", r"\1?[redacted]", text)
    return text[:180]


def _encode(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) <= MAX_RESULT_CHARS:
        return encoded
    return json.dumps(
        {
            "schema": SCHEMA,
            "status": "uncertain" if payload.get("mutation") else "unavailable",
            "reason": "response_limit",
            "mutation": bool(payload.get("mutation")),
        },
        separators=(",", ":"),
    )


async def _get_client(context: Any) -> Any:
    from config_manager import get_client

    return await get_client(getattr(context, "_chat_id", 0) or 0, context=context)


async def run(config: Config, context: Any) -> str:
    """Submit one idempotent metadata add; no registry pre-check or retry."""

    base = {
        "schema": SCHEMA,
        "network": config.network,
        "token": {
            "mint": config.mint,
            "symbol": config.symbol,
            "decimals": config.decimals,
        },
    }
    if config.mint in {USDC_MINT, WRAPPED_SOL_MINT} or config.symbol.upper() in {
        "USDC",
        "SOL",
    }:
        return _encode(
            {
                **base,
                "status": "rejected_before_submit",
                "reason": "SOL and USDC are built-in protected tokens and must not be registered",
                "mutation": False,
            },
        )
    if config.preview:
        return _encode(
            {
                **base,
                "status": "preview",
                "outcome": "rejected_before_submit",
                "reason": "dry-run preview; no token metadata was submitted",
                "mutation": False,
            },
        )

    try:
        client = await asyncio.wait_for(
            _get_client(context), timeout=config.timeout_seconds
        )
        gateway = getattr(client, "gateway", None)
        if gateway is None:
            raise ValueError("Gateway client is unavailable")
    except Exception as exc:
        return _encode(
            {
                **base,
                "status": "unavailable",
                "reason": _safe_error(exc),
                "mutation": False,
            },
        )

    try:
        response = await asyncio.wait_for(
            gateway.add_token(
                network_id=config.network,
                address=config.mint,
                symbol=config.symbol,
                decimals=config.decimals,
                name=config.symbol,
            ),
            timeout=config.timeout_seconds,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _encode(
            {
                **base,
                "status": "uncertain",
                "reason": _safe_error(exc),
                "mutation": True,
            },
        )

    return _encode(
        {
            **base,
            "status": "confirmed",
            "mutation": True,
            "receipt_type": type(response).__name__,
        },
    )
