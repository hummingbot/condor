"""Typed Aomi executor requests; signing decisions are always explicit."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .executor_create import create_executor


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
