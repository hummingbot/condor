"""Typed Aomi executor requests; signing decisions are always explicit."""

import math
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .executor_create import create_executor


class SvmSpendingPolicy(BaseModel):
    """Limits independently enforced by the API against complete simulation evidence."""

    model_config = ConfigDict(extra="forbid")
    wallet: str = Field(min_length=1)
    market: str = Field(min_length=1)
    protocol_program: str = Field(min_length=1)
    allowed_programs: list[str] = Field(min_length=1)
    max_debits_raw: dict[str, str] = Field(min_length=1)


async def prepare_onchain(
    client: Any, operation: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Use the server-owned wallet and registered Aomi recipe; never sign or stage."""
    return await client.executors._post(
        "/executors/onchain/prepare",
        json={"operation": operation, "arguments": arguments},
    )


@dataclass
class PreparedAction:
    client: Any
    instructions: list[dict[str, Any]]
    wallet: str
    cluster: str
    expires_at: float

    def resolve(
        self, client: Any, cluster: str | None, policy: SvmSpendingPolicy | None
    ):
        if self.client is not client or time.time() >= self.expires_at:
            raise ValueError(
                "Prepared action unavailable or expired; prepare again and review the new plan"
            )
        if cluster != self.cluster or (
            policy is not None and policy.wallet != self.wallet
        ):
            raise ValueError(
                "Prepared action wallet or cluster does not match the request"
            )
        return deepcopy(self.instructions)


_prepared_actions: OrderedDict[str, PreparedAction] = OrderedDict()


async def prepare_onchain_action(
    client: Any, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Keep exact unsigned batches in this process; expose a short review reference."""
    response = await prepare_onchain(client, "prepare", arguments)
    result = response["result"]
    batches = result.get("instructions")
    expiry = result.get("expires_at")
    now = time.time()
    if (
        not isinstance(batches, list)
        or not batches
        or type(expiry) not in (int, float)
        or not math.isfinite(expiry)
        or expiry <= now
    ):
        raise ValueError(
            "Prepared action is missing valid instructions or expiry; prepare again and review"
        )
    wallet, cluster = response.get("wallet"), response.get("cluster")
    if (
        not wallet
        or not cluster
        or result.get("wallet") != wallet
        or result.get("cluster") != cluster
    ):
        raise ValueError("Prepared action wallet or cluster mismatch")
    programs = sorted(
        {
            instruction["program_id"]
            for batch in batches
            for instruction in batch["instructions"]
        }
    )
    for key in list(_prepared_actions):
        if _prepared_actions[key].expires_at <= now:
            del _prepared_actions[key]
    while len(_prepared_actions) >= 64:
        _prepared_actions.popitem(last=False)
    action_id = str(uuid4())
    _prepared_actions[action_id] = PreparedAction(
        client, deepcopy(batches), wallet, cluster, min(expiry, now + 120)
    )
    public = deepcopy(response)
    del public["result"]["instructions"]
    public["prepared_action_id"] = action_id
    public["result"]["allowed_programs"] = programs
    public["result"]["expires_at"] = _prepared_actions[action_id].expires_at
    return public


class EvmCalldata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signature: str = ""
    args: list[Any] = Field(default_factory=list)
    raw: str = ""


class EvmCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str
    value: str
    data: EvmCalldata
    description: str = ""
    chain_id: int | None = None


async def create_lending_executor(
    client: Any,
    *,
    chain_id: int,
    wallet: str,
    pool: str,
    asset: str,
    amount: str,
    action: Literal["supply", "withdraw"],
    commit: bool,
    require_lending_policy: bool | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    max_gas_quote: str | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Create an exact Aave supply or withdrawal through the existing executor owner."""
    config = {
        "chain_id": chain_id,
        "chain": "evm",
        "mode": "lending",
        "commit": commit,
        "lending": {
            "chain_id": chain_id,
            "wallet": wallet,
            "pool": pool,
            "asset": asset,
            "amount": amount,
            "action": action,
        },
        "calls": None,
        "instructions": None,
        "operation": None,
        "arguments": None,
        "require_lending_policy": (
            require_lending_policy if require_lending_policy is not None else False
        ),
        "max_gas_quote": max_gas_quote,
        "max_svm_network_fee_lamports": None,
        "reviewed_svm_plan_hash": None,
        "svm_spending_policy": None,
    }
    if timeout_sec is not None:
        config["timeout_sec"] = timeout_sec
    return await create_executor(
        client,
        "onchain_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
    )


async def create_onchain_executor(
    client: Any,
    *,
    chain_id: int,
    mode: Literal["calls", "operation", "instructions"],
    commit: bool,
    calls: list[EvmCall] | None = None,
    instructions: list[dict[str, Any]] | None = None,
    prepared_action_id: str | None = None,
    operation: str | None = None,
    arguments: dict[str, Any] | None = None,
    app: str | None = None,
    skills: list[str] | None = None,
    chain: Literal["evm", "svm"] | None = None,
    cluster: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    max_gas_quote: str | None = None,
    max_svm_network_fee_lamports: int | None = None,
    reviewed_svm_plan_hash: str | None = None,
    svm_spending_policy: SvmSpendingPolicy | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Create a catalog operation, EVM calls or Solana instructions; commits require confirmation."""
    if max_svm_network_fee_lamports is not None and (
        chain != "svm"
        or type(max_svm_network_fee_lamports) is not int
        or max_svm_network_fee_lamports < 0
    ):
        raise ValueError(
            "Solana network fee limit requires svm and non-negative integer lamports"
        )
    if svm_spending_policy is not None and (chain != "svm" or mode != "instructions"):
        raise ValueError("Solana spending policy requires svm instructions mode")
    if prepared_action_id is not None:
        if (
            mode != "instructions"
            or chain != "svm"
            or any(
                value is not None
                for value in (instructions, calls, operation, arguments)
            )
        ):
            raise ValueError(
                "Prepared action requires svm instructions mode without raw instructions or operation"
            )
        prepared = _prepared_actions.get(prepared_action_id)
        if prepared is None:
            raise ValueError(
                "Prepared action unavailable or expired; prepare again and review the new plan"
            )
        instructions = prepared.resolve(client, cluster, svm_spending_policy)
    if mode == "instructions":
        if chain != "svm" or not instructions:
            raise ValueError("Instructions mode requires non-empty instructions on svm")
        if any(value is not None for value in (calls, operation, arguments)):
            raise ValueError("Instructions mode takes only instruction batches")
    elif instructions is not None:
        raise ValueError("Instruction batches require instructions mode")
    if mode == "calls" and (
        not calls or operation is not None or arguments is not None
    ):
        raise ValueError("Calls mode requires calls and no catalog operation")
    if mode == "operation" and (not operation or calls is not None):
        raise ValueError("Operation mode requires an operation and no raw calls")
    config = {
        "chain_id": chain_id,
        "mode": mode,
        "commit": commit,
        "lending": None,
        "instructions": instructions,
        "calls": (
            [call.model_dump(exclude_none=True) for call in calls] if calls else None
        ),
        "operation": operation,
        "arguments": arguments,
        "max_gas_quote": max_gas_quote,
        "max_svm_network_fee_lamports": max_svm_network_fee_lamports,
        "reviewed_svm_plan_hash": reviewed_svm_plan_hash,
        "svm_spending_policy": (
            svm_spending_policy.model_dump() if svm_spending_policy else None
        ),
    }
    config.update(
        {
            key: value
            for key, value in {
                "app": app,
                "skills": skills,
                "chain": chain,
                "cluster": cluster,
                "timeout_sec": timeout_sec,
            }.items()
            if value is not None
        }
    )
    return await create_executor(
        client,
        "onchain_executor",
        config,
        account_name=account_name,
        controller_id=controller_id,
    )
