"""List what the Aomi Pipeline can execute on-chain: app operations and protocol skills."""

CATEGORY = "DeFi"

import logging
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

TABLE_COLUMNS = ["source", "operation", "kind", "args"]

FOOTER = (
    "Reads return state or a quote and stage nothing; run them with the `aomi_read` "
    "routine or as `mode='operation'` dry runs. Execute an operation with "
    "`manage_executors(action='create', executor_type='onchain_executor')`: "
    "`mode='operation'` with `app`, `operation` and `arguments` for a listed builder; "
    "or `mode='calls'` with raw EVM calls built from a skill's instructions "
    "(`aomi_skill`) for every other protocol. Solana operations need "
    "`chain='svm'`. Aomi's own chat plumbing (authorization, scheduling, threads) and "
    "its stage/simulate/commit primitives are not listed: the executor owns that lifecycle."
)


class Config(BaseModel):
    """Browse the Aomi Pipeline catalog: executable operations, reads, and protocol skills."""

    app: str = Field(
        default="", description="One app to list (blank = every app in the catalog)"
    )
    skill: str = Field(
        default="",
        description="One protocol skill to expand (e.g. 'aave'); blank lists every skill",
    )
    describe: bool = Field(
        default=True,
        description="Fetch each operation's descriptor (description + argument schema)",
    )
    max_operations: int = Field(
        default=40, description="Cap on operations listed per app or skill"
    )
    include_skills: bool = Field(
        default=True, description="Also list protocol skills (Aave, Morpho, Lido, ...)"
    )


def _policy():
    """The shared classification from the `aomi` package, or a permissive stand-in."""
    try:
        from aomi.pipeline import policy

        return policy
    except (
        ImportError
    ):  # pragma: no cover - the client ships it; keep the routine usable

        class _Permissive:
            @staticmethod
            def is_visible(name: str) -> bool:
                return True

            @staticmethod
            def classify(name: str) -> str:
                return "executable"

            @staticmethod
            def group_of(name: str) -> str:
                return "Operations"

            @staticmethod
            def chain_family_of(name: str) -> str:
                return "evm"

        return _Permissive()


def _first_line(text: Any) -> str:
    return str(text or "").strip().split("\n")[0].strip()


def _args_label(descriptor: Any) -> str:
    """``a*, b`` — every argument, the required ones starred; `topic` is harness-internal."""
    required_list = list(getattr(descriptor, "required_args", None) or [])
    required = set(required_list)
    names = list((getattr(descriptor, "properties", None) or {}).keys())
    for name in required_list:  # schema order, not set order
        if name not in names:
            names.append(name)
    names = [n for n in names if n != "topic"]
    return ", ".join(f"{n}*" if n in required else n for n in names)


async def _describe(describe_fn, *key: str) -> tuple[str, str]:
    """``(description, args)`` for one operation; a failure is a labelled blank."""
    try:
        descriptor = await describe_fn(*key)
    except Exception as e:  # noqa: BLE001 - one bad descriptor must not sink the run
        logger.warning("Aomi describe %s failed: %s", key, e)
        return f"(describe failed: {e})", ""
    return _first_line(descriptor.description), _args_label(descriptor)


def _bullet(op: str, description: str, args: str, chain: str) -> str:
    bullet = f"- `{op}`"
    if chain == "svm":
        bullet += " (svm)"
    if description:
        bullet += f" — {description}"
    if args:
        bullet += f" (args: {args})"
    return bullet


async def _list_app(
    client: Any, app: str, config: Config, policy, rows: list[dict]
) -> list[str]:
    ops = [entry.name async for entry in client.list_operations(app)]
    visible = [op for op in ops if policy.is_visible(op)]
    hidden = len(ops) - len(visible)
    lines = [
        f"## app `{app}` ({len(visible)} operations"
        + (f", {hidden} harness-internal hidden)" if hidden else ")")
    ]
    grouped: dict[str, list[str]] = defaultdict(list)
    for op in visible[: max(config.max_operations, 0)]:
        grouped[policy.group_of(op)].append(op)
    for group in sorted(grouped, key=lambda g: (g == "Protocol operations", g)):
        lines.append(f"### {group}")
        for op in grouped[group]:
            kind = policy.classify(op)
            chain = policy.chain_family_of(op)
            description, args = "", ""
            if config.describe:
                description, args = await _describe(client.describe_operation, app, op)
            if kind == "read":
                description = (
                    ("read: " + description).strip() if description else "read"
                )
            lines.append(_bullet(op, description, args, chain))
            rows.append({"source": app, "operation": op, "kind": kind, "args": args})
    if len(visible) > config.max_operations:
        lines.append(f"- … {len(visible) - config.max_operations} more not shown")
    lines.append("")
    return lines


async def _list_skills(
    client: Any, config: Config, policy, rows: list[dict]
) -> list[str]:
    if config.skill:
        skills = [config.skill]
    else:
        skills = [entry.name async for entry in client.list_skills()]
    if not skills:
        return []
    lines = [f"## skills ({len(skills)})"]
    if not config.skill:
        lines.append(", ".join(f"`{s}`" for s in skills))
        lines.append(
            "Most skills are instructions (contracts, function signatures, rules) for "
            "`mode='calls'`: run `aomi_skill` (skill='<name>') to read them. "
            "Pass `skill='<name>'` here to check whether it also injects builders."
        )
        lines.append("")
        return lines
    for skill in skills:
        directory = await client.list_skill_operations(skill)
        ops = [op for op in directory.names() if policy.classify(op) == "executable"]
        if not ops:
            lines.append(
                f"### skill `{skill}` — instructions only: run `aomi_skill` (skill='{skill}') for "
                "its contracts and function signatures, then execute with `mode='calls'`"
            )
            lines.append("")
            continue
        lines.append(f"### skill `{skill}` ({len(ops)} builders)")
        for op in ops[: max(config.max_operations, 0)]:
            kind = policy.classify(op)
            chain = policy.chain_family_of(op)
            description, args = "", ""
            if config.describe:
                description, args = await _describe(
                    client.describe_skill_operation, skill, op
                )
            if kind == "read":
                description = (
                    ("read: " + description).strip() if description else "read"
                )
            lines.append(_bullet(op, description, args, chain))
            rows.append(
                {
                    "source": f"skill:{skill}",
                    "operation": op,
                    "kind": kind,
                    "args": args,
                }
            )
        lines.append("")
    return lines


async def _walk(client: Any, config: Config) -> tuple[str, list[dict]]:
    policy = _policy()
    rows: list[dict] = []
    lines = ["# Aomi Pipeline catalog", ""]
    if config.skill:
        apps: list[str] = []
    elif config.app:
        apps = [config.app]
    else:
        apps = [entry.name async for entry in client.list_apps()]
    for app in apps:
        lines += await _list_app(client, app, config, policy, rows)
    if config.include_skills or config.skill:
        lines += await _list_skills(client, config, policy, rows)
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
