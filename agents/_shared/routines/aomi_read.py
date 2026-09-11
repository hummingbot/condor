"""Read EVM or Solana accounts, interfaces, holdings and context through Aomi."""

CATEGORY = "DeFi"

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

READ_OPS = {
    "evm": ("account", "contract", "token-holdings", "context"),
    "svm": ("account", "program", "token-holdings", "context"),
}
MAX_JSON_CHARS = 6000


class Config(BaseModel):
    """Read EVM or Solana state via Aomi's shared Pipeline."""

    chain: str = Field(default="evm", description="evm or svm (Solana)")

    op: str = Field(
        default="context",
        description="account, token-holdings, context; contract for EVM or program for Solana",
    )
    chain_id: int = Field(default=8453, description="EVM chain id (8453 = Base)")
    address: str = Field(
        default="",
        description="Account/contract address, or the holder for token-holdings",
    )
    args_json: str = Field(
        default="{}",
        description="Extra arguments as JSON (EVM holdings needs token_address; Solana optionally takes mint)",
    )


def build_args(config: Config) -> dict[str, Any]:
    """The flat argument map ``/v1/pipeline/evm/{op}`` takes for this config.

    Raises ``ValueError`` on an unknown op or unreadable JSON so the caller can
    render one ``Invalid config`` line instead of a failed request.
    """
    if config.chain not in READ_OPS:
        raise ValueError("chain must be evm or svm")
    allowed = READ_OPS[config.chain]
    if config.op not in allowed:
        raise ValueError(f"op must be one of {', '.join(allowed)}, got {config.op!r}")
    try:
        extra = json.loads(config.args_json or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"args_json is not valid JSON: {e}") from e
    if not isinstance(extra, dict):
        raise ValueError("args_json must decode to a JSON object")

    if config.chain == "svm":
        if "chain_id" in extra or "cluster" in extra:
            raise ValueError(
                "Solana reads use the connected wallet's cluster; check context first"
            )
        args = dict(extra)
        address = config.address.strip()
        if address and config.op != "context":
            key = {
                "account": "pubkey",
                "program": "program_id",
                "token-holdings": "owner",
            }[config.op]
            args[key] = address
        if config.op == "account" and not args.get("pubkey"):
            raise ValueError("Solana account needs an address")
        return args

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


def render(op: str, args: dict[str, Any], result: Any, chain: str = "evm") -> str:
    body = json.dumps(result, indent=2, default=str)
    if len(body) > MAX_JSON_CHARS:
        body = body[:MAX_JSON_CHARS] + "\n… (truncated)"
    arg_lines = "\n".join(f"- {k}: {v}" for k, v in args.items())
    if op == "context":
        arg_lines = (
            "- note: context reports the wallet's active chain; every usable chain is "
            "listed under supported_chains (chain_id is not an input here)"
        )
        if chain == "svm":
            arg_lines = (
                "- note: Solana reads use the connected wallet's cluster shown below"
            )
    return f"# Aomi {chain}/{op}\n\n{arg_lines}\n\n```json\n{body}\n```"


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
        result = await client.read(config.chain, config.op, args)
    except Exception as e:  # noqa: BLE001 - a routine reports, it never raises
        logger.warning("Aomi read %s failed: %s", config.op, e)
        return f"Aomi read failed: {e}"
    finally:
        await client.close()
    return RoutineResult(text=render(config.op, args, result, config.chain))
