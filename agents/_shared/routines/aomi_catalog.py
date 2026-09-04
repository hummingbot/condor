"""List the apps and operations the Aomi Pipeline can execute on-chain."""

CATEGORY = "DeFi"

import logging
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

TABLE_COLUMNS = ["app", "operation", "args"]

FOOTER = (
    "Read chain state with the `aomi_read` routine (account, contract, "
    "token-holdings, context). Execute an operation with "
    "`manage_executors(action='create', executor_type='onchain_executor')` — "
    "pass `mode='operation'` with `app`, `operation` and `arguments`, or "
    "`mode='calls'` with raw `evm_stage_tx` calls."
)


class Config(BaseModel):
    """Browse the Aomi Pipeline catalog: apps, their operations and argument schemas."""

    app: str = Field(
        default="", description="One app to list (blank = every app in the catalog)"
    )
    describe: bool = Field(
        default=True,
        description="Fetch each operation's descriptor (description + argument schema)",
    )
    max_operations: int = Field(
        default=40, description="Cap on operations listed per app"
    )


def _first_line(text: Any) -> str:
    return str(text or "").strip().split("\n")[0].strip()


def _args_label(descriptor: Any) -> str:
    """``a*, b`` — every argument, the required ones starred."""
    required = set(getattr(descriptor, "required_args", None) or [])
    names = list((getattr(descriptor, "properties", None) or {}).keys())
    for name in required:
        if name not in names:
            names.append(name)
    return ", ".join(f"{n}*" if n in required else n for n in names)


async def _describe(client: Any, app: str, op: str) -> tuple[str, str]:
    """``(description, args)`` for one operation; a failure is a labelled blank."""
    try:
        descriptor = await client.describe_operation(app, op)
    except Exception as e:  # noqa: BLE001 - one bad descriptor must not sink the run
        logger.warning("Aomi describe_operation(%s, %s) failed: %s", app, op, e)
        return f"(describe failed: {e})", ""
    return _first_line(descriptor.description), _args_label(descriptor)


async def _walk(client: Any, config: Config) -> tuple[str, list[dict]]:
    if config.app:
        apps = [config.app]
    else:
        apps = [entry.name async for entry in client.list_apps()]

    lines = ["# Aomi Pipeline catalog", ""]
    rows: list[dict] = []
    for app in apps:
        ops = [entry.name async for entry in client.list_operations(app)]
        lines.append(f"## {app} ({len(ops)} operations)")
        for op in ops[: max(config.max_operations, 0)]:
            description, args = "", ""
            if config.describe:
                description, args = await _describe(client, app, op)
            bullet = f"- `{op}`"
            if description:
                bullet += f" — {description}"
            if args:
                bullet += f" (args: {args})"
            lines.append(bullet)
            rows.append({"app": app, "operation": op, "args": args})
        if len(ops) > config.max_operations:
            lines.append(f"- … {len(ops) - config.max_operations} more not shown")
        lines.append("")

    lines.append(FOOTER)
    return "\n".join(lines), rows


async def run(config: Config, context: Any) -> str | RoutineResult:
    from condor.aomi_client import MISSING_TOKEN_MESSAGE, get_pipeline_client

    client = get_pipeline_client()
    if client is None:
        return MISSING_TOKEN_MESSAGE
    try:
        text, rows = await _walk(client, config)
    except Exception as e:  # noqa: BLE001 - a routine reports, it never raises
        logger.warning("Aomi catalog failed: %s", e)
        return f"Aomi catalog failed: {e}"
    finally:
        await client.close()
    return RoutineResult(text=text, table_data=rows, table_columns=TABLE_COLUMNS)
