"""Read EVM chain state through the Aomi Pipeline (account, contract, holdings, context)."""

CATEGORY = "DeFi"

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

READ_OPS = ("account", "contract", "token-holdings", "context")
MAX_JSON_CHARS = 6000


class Config(BaseModel):
    """Read EVM state via Aomi: an account, a contract, token holdings or the chain context."""

    op: str = Field(
        default="context",
        description="One of: account, contract, token-holdings, context",
    )
    chain_id: int = Field(default=8453, description="EVM chain id (8453 = Base)")
    address: str = Field(
        default="",
        description="Account/contract address, or the holder for token-holdings",
    )
    args_json: str = Field(
        default="{}",
        description="Extra arguments as JSON (token-holdings needs token_address)",
    )


def build_args(config: Config) -> dict[str, Any]:
    """The flat argument map ``/v1/pipeline/evm/{op}`` takes for this config.

    Raises ``ValueError`` on an unknown op or unreadable JSON so the caller can
    render one ``Invalid config`` line instead of a failed request.
    """
    if config.op not in READ_OPS:
        raise ValueError(f"op must be one of {', '.join(READ_OPS)}, got {config.op!r}")
    try:
        extra = json.loads(config.args_json or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"args_json is not valid JSON: {e}") from e
    if not isinstance(extra, dict):
        raise ValueError("args_json must decode to a JSON object")

    if config.op == "context":
        # The context tool takes no arguments: it reports the wallet's active chain and
        # lists every supported chain, so a chain_id here would be silently ignored.
        return dict(extra)
    args: dict[str, Any] = {"chain_id": int(config.chain_id)}
    address = config.address.strip()
    if config.op == "token-holdings":
        if address:
            args["holder_address"] = address
        if not (extra.get("token_address") or "").strip():
            raise ValueError("token-holdings needs token_address in args_json")
    elif address:
        args["address"] = address
    args.update(extra)
    return args


def render(op: str, args: dict[str, Any], result: Any) -> str:
    body = json.dumps(result, indent=2, default=str)
    if len(body) > MAX_JSON_CHARS:
        body = body[:MAX_JSON_CHARS] + "\n… (truncated)"
    arg_lines = "\n".join(f"- {k}: {v}" for k, v in args.items())
    if op == "context":
        arg_lines = (
            "- note: context reports the wallet's active chain; every usable chain is "
            "listed under supported_chains (chain_id is not an input here)"
        )
    return f"# Aomi evm/{op}\n\n{arg_lines}\n\n```json\n{body}\n```"


async def run(config: Config, context: Any) -> str | RoutineResult:
    from condor.aomi_client import MISSING_TOKEN_MESSAGE, get_pipeline_client

    try:
        args = build_args(config)
    except ValueError as e:
        return f"Invalid config: {e}"

    client = get_pipeline_client()
    if client is None:
        return MISSING_TOKEN_MESSAGE
    try:
        result = await client.read("evm", config.op, args)
    except Exception as e:  # noqa: BLE001 - a routine reports, it never raises
        logger.warning("Aomi read %s failed: %s", config.op, e)
        return f"Aomi read failed: {e}"
    finally:
        await client.close()
    return RoutineResult(text=render(config.op, args, result))
