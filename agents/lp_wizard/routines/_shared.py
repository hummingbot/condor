from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Iterator, Literal, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 3
RUNTIME_VERSION = 1
NETWORK = "solana-mainnet-beta"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
_CONTROLLER_RE = re.compile(r"^lp_wizard\.orca_(?P<experiment>e)?(?P<number>[1-9]\d*)$")
_CONDOR_CONFIG_KEYS = {
    "server_name",
    "agent_key",
    "model_base_url",
    "frequency_sec",
    "trading_context",
    "execution_mode",
    "max_ticks",
    "bot_name",
    "risk_limits",
}
_SENSITIVE = {
    "authorization",
    "cookie",
    "credentials",
    "mnemonic",
    "password",
    "passphrase",
    "privatekey",
    "secret",
    "seedphrase",
}
_SENSITIVE_MARKERS = (
    "apikey",
    "accesstoken",
    "authtoken",
    "clientsecret",
    "refreshtoken",
)
_T = TypeVar("_T")


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def wait_bounded(awaitable: Awaitable[_T], timeout: float) -> _T:
    """Stop waiting at the deadline even if the child delays cancellation."""
    task = asyncio.create_task(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        raise asyncio.TimeoutError
    return task.result()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_name: StrictStr
    server_name: StrictStr | None = None
    risk_profile: Literal[
        "yield_focused",
        "yield_high_risk",
        "yield_extreme_risk",
        "yield_no_limit",
    ] = "yield_focused"
    total_amount_quote: Decimal
    max_slot_count: StrictInt = Field(gt=0)
    min_capital_per_slot_quote: Decimal
    max_capital_per_slot_quote: Decimal
    max_slots_per_pool: Literal[1]
    max_slippage_pct: Decimal
    min_sol_reserve: Decimal
    inventory_dust_quote: Decimal
    session_max_age_minutes: Decimal
    session_take_profit_net_pnl_ratio: Decimal
    session_stop_loss_net_pnl_ratio: Decimal

    @field_validator("account_name")
    @classmethod
    def _account_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("account_name must not be empty")
        return value

    @field_validator("server_name", mode="before")
    @classmethod
    def _server_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("server_name must be a string")
        return value.strip() or None

    @field_validator(
        "total_amount_quote",
        "min_capital_per_slot_quote",
        "max_capital_per_slot_quote",
        "max_slippage_pct",
        "min_sol_reserve",
        "inventory_dust_quote",
        "session_max_age_minutes",
        "session_take_profit_net_pnl_ratio",
        "session_stop_loss_net_pnl_ratio",
        mode="before",
    )
    @classmethod
    def _finite_decimal(cls, value: Any) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("must be a finite number")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("must be a finite number") from exc
        if not number.is_finite():
            raise ValueError("must be a finite number")
        return number

    @model_validator(mode="after")
    def _bounds(self) -> Config:
        if self.total_amount_quote <= 0:
            raise ValueError("total_amount_quote must be positive")
        if self.min_capital_per_slot_quote <= 0:
            raise ValueError("min_capital_per_slot_quote must be positive")
        if self.max_capital_per_slot_quote <= 0:
            raise ValueError("max_capital_per_slot_quote must be positive")
        if not (
            self.min_capital_per_slot_quote
            <= self.max_capital_per_slot_quote
            <= self.total_amount_quote
        ):
            raise ValueError(
                "capital bounds must satisfy min_capital_per_slot_quote <= "
                "max_capital_per_slot_quote <= total_amount_quote"
            )
        if not 0 < self.max_slippage_pct <= 100:
            raise ValueError("max_slippage_pct must be greater than 0 and at most 100")
        if self.min_sol_reserve < 0:
            raise ValueError("min_sol_reserve must be non-negative")
        if self.inventory_dust_quote < 0:
            raise ValueError("inventory_dust_quote must be non-negative")
        if self.session_max_age_minutes <= 0:
            raise ValueError("session_max_age_minutes must be positive")
        if self.session_take_profit_net_pnl_ratio <= 0:
            raise ValueError("session_take_profit_net_pnl_ratio must be positive")
        if self.session_stop_loss_net_pnl_ratio <= 0:
            raise ValueError("session_stop_loss_net_pnl_ratio must be positive")
        return self


@dataclass(frozen=True)
class Session:
    controller_id: str
    number: int
    live: bool
    strategy_dir: Path
    path: Path

    @property
    def state_path(self) -> Path:
        if not self.live:
            raise ValueError("experiment runs have no durable state")
        return self.path / "state.json"

    @property
    def state_lock_path(self) -> Path:
        if not self.live:
            raise ValueError("experiment runs have no state lock")
        return self.path / "state.lock"

    @property
    def wallet_lock_path(self) -> Path:
        return self.strategy_dir / "sessions" / "wallet.lock"

    @property
    def config_path(self) -> Path:
        return (self.path if self.live else self.strategy_dir) / "config.yml"


@dataclass(frozen=True)
class BoundClient:
    server_name: str
    client: Any


def _contained(path: Path, root: Path) -> Path:
    root = root.resolve()
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "resolved strategy path escapes agents/lp_wizard/strategies"
        ) from exc
    return path


def validate_controller_id(controller_id: str) -> str:
    if not isinstance(controller_id, str):
        raise ValueError("controller_id must be a string")
    if _CONTROLLER_RE.fullmatch(controller_id) is None:
        raise ValueError(
            "controller_id must be exactly lp_wizard.orca_N or lp_wizard.orca_eN"
        )
    return controller_id


def resolve_session(controller_id: str, strategies_dir: Path | None = None) -> Session:
    controller_id = validate_controller_id(controller_id)
    match = _CONTROLLER_RE.fullmatch(controller_id)
    assert match is not None
    root = (strategies_dir or STRATEGIES_DIR).resolve()
    strategy_dir = _contained(root / "orca", root)
    number = int(match.group("number"))
    live = match.group("experiment") is None
    path = (
        strategy_dir / "sessions" / f"session_{number}"
        if live
        else strategy_dir / "dry_runs" / f"experiment_{number}.md"
    )
    path = _contained(path, root)
    if live and not path.is_dir():
        raise ValueError(f"live session directory does not exist for '{controller_id}'")
    return Session(controller_id, number, live, strategy_dir, path)


def require_live(session: Session) -> None:
    if not session.live:
        raise ValueError("open, close, and recover require a live loop session")


def _root(session: Session) -> Path:
    return session.strategy_dir.parent


def load_config(session: Session) -> Config:
    path = _contained(session.config_path, _root(session))
    if not path.is_file():
        source = "session" if session.live else "strategy-root"
        raise ValueError(f"{source} config.yml does not exist")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load config.yml: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("config.yml must contain a YAML object")
    unknown = set(value) - set(Config.model_fields) - _CONDOR_CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"unknown strategy config fields: {', '.join(sorted(unknown))}"
        )
    config = Config.model_validate(
        {key: value[key] for key in Config.model_fields if key in value}
    )
    if session.live and config.server_name is None:
        raise ValueError("live session config requires a non-empty server_name")
    return config


def initial_state(config: Config) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "binding": None,
        "slots": {
            f"slot-{number:02d}": {"position": None, "pending_mutation": None}
            for number in range(1, config.max_slot_count + 1)
        },
    }


def _keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} must contain exactly {', '.join(sorted(expected))}")


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _iso_timestamp(value: Any, label: str) -> str:
    value = _nonempty(value, label)
    _parse_timestamp(value, label)
    return value


def _parse_timestamp(value: Any, label: str) -> datetime:
    value = _nonempty(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def decimal(value: Any, label: str = "value", *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "positive " if positive else ""
        raise ValueError(f"{label} must be a finite {qualifier}number")
    return result


def decimal_matches(left: Any, right: Any, label: str = "value") -> bool:
    first = decimal(left, label)
    second = decimal(right, label)
    tolerance = max(abs(first), abs(second)) * Decimal("1e-12")
    return abs(first - second) <= tolerance


def validate_state(state: Any, session: Session, config: Config) -> dict[str, Any]:
    require_live(session)
    if not isinstance(state, dict):
        raise ValueError("state.json must contain a JSON object")
    _keys(state, {"schema_version", "binding", "slots"}, "state")
    if state["schema_version"] != SCHEMA_VERSION:
        raise ValueError("state.json schema_version must be 3")
    slots = state["slots"]
    expected_slots = [
        f"slot-{number:02d}" for number in range(1, config.max_slot_count + 1)
    ]
    if not isinstance(slots, dict) or set(slots) != set(expected_slots):
        raise ValueError("state slots do not match configured stable slot IDs")

    occupied_pools: set[str] = set()
    unresolved = False
    committed = Decimal(0)
    for slot_id, slot in slots.items():
        if not isinstance(slot, dict):
            raise ValueError(f"{slot_id} must be an object")
        _keys(slot, {"position", "pending_mutation"}, slot_id)
        position = slot["position"]
        pending = slot["pending_mutation"]
        if position is not None:
            _validate_position(position, session, slot_id)
            pool = position["pool_address"]
            if pool in occupied_pools:
                raise ValueError("more than one slot owns the same pool")
            occupied_pools.add(pool)
            amount = decimal(
                position["amount_quote"],
                f"{slot_id} position amount_quote",
                positive=True,
            )
            if not (
                config.min_capital_per_slot_quote
                <= amount
                <= config.max_capital_per_slot_quote
            ):
                raise ValueError(
                    f"{slot_id} position amount_quote is outside configured bounds"
                )
            committed += amount
            unresolved = True
        if pending is not None:
            _validate_pending(pending, position, slot_id)
            unresolved = True
            request = pending["request"]
            if pending["type"] == "open" and position is None:
                amount = decimal(
                    request.get("amount_quote"),
                    f"{slot_id} pending amount_quote",
                    positive=True,
                )
                if not (
                    config.min_capital_per_slot_quote
                    <= amount
                    <= config.max_capital_per_slot_quote
                ):
                    raise ValueError(
                        f"{slot_id} pending amount_quote is outside configured bounds"
                    )
                committed += amount
                pool = _nonempty(
                    request.get("pool_address"), f"{slot_id} pending pool_address"
                )
                if pool in occupied_pools:
                    raise ValueError(
                        "more than one slot owns or is opening the same pool"
                    )
                occupied_pools.add(pool)
    if committed > config.total_amount_quote:
        raise ValueError("persisted state exceeds total_amount_quote")
    _validate_binding(state["binding"], session, config, unresolved)
    return state


def _validate_position(position: Any, session: Session, slot_id: str) -> None:
    fields = {
        "controller_id",
        "executor_id",
        "position_address",
        "pool_address",
        "base_mint",
        "quote_mint",
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
        "amount_quote",
        "opened_at",
    }
    if not isinstance(position, dict):
        raise ValueError(f"{slot_id} position must be an object or null")
    _keys(position, fields, f"{slot_id} position")
    for field in fields - {
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
        "amount_quote",
    }:
        _nonempty(position[field], f"{slot_id} position {field}")
    if position["controller_id"] != session.controller_id:
        raise ValueError(f"{slot_id} position controller mismatch")
    if position["quote_mint"] != USDC_MINT:
        raise ValueError(f"{slot_id} position quote_mint must be canonical USDC")
    if position["base_mint"] == position["quote_mint"]:
        raise ValueError(f"{slot_id} position base and quote mints must differ")
    _iso_timestamp(position["opened_at"], f"{slot_id} position opened_at")
    lower_limit = decimal(position["lower_limit_price"], positive=True)
    lower = decimal(position["lower_price"], positive=True)
    upper = decimal(position["upper_price"], positive=True)
    upper_limit = decimal(position["upper_limit_price"], positive=True)
    if not lower_limit < lower < upper < upper_limit:
        raise ValueError(f"{slot_id} position price bounds are invalid")
    decimal(position["amount_quote"], f"{slot_id} position amount_quote", positive=True)


def _validate_pending(pending: Any, position: Any, slot_id: str) -> None:
    fields = {
        "operation_id",
        "type",
        "step",
        "status",
        "request",
        "attempted_at",
        "external_id",
        "confirmed",
    }
    if not isinstance(pending, dict):
        raise ValueError(f"{slot_id} pending_mutation must be an object or null")
    _keys(pending, fields, f"{slot_id} pending_mutation")
    _nonempty(pending["operation_id"], f"{slot_id} operation_id")
    operation = pending["type"]
    step = pending["step"]
    status = pending["status"]
    if operation not in {"open", "close"}:
        raise ValueError(f"{slot_id} mutation type is invalid")
    valid_steps = (
        {"rebalance", "create", "restore"}
        if operation == "open"
        else {"stop", "restore"}
    )
    if step not in valid_steps:
        raise ValueError(f"{slot_id} mutation step is invalid for {operation}")
    if status not in {"intent", "submitted", "uncertain"}:
        raise ValueError(f"{slot_id} mutation status is invalid")
    if not isinstance(pending["request"], dict) or not isinstance(
        pending["confirmed"], dict
    ):
        raise ValueError(f"{slot_id} mutation request and confirmed must be objects")
    if pending["attempted_at"] is not None:
        _iso_timestamp(pending["attempted_at"], f"{slot_id} attempted_at")
    if pending["external_id"] is not None:
        _nonempty(pending["external_id"], f"{slot_id} external_id")
    if status != "intent" and pending["attempted_at"] is None:
        raise ValueError(f"{slot_id} submitted mutation requires attempted_at")
    if status == "intent" and pending["attempted_at"] is not None:
        raise ValueError(f"{slot_id} intent mutation cannot already have attempted_at")
    if operation == "close" and position is None:
        raise ValueError(f"{slot_id} close mutation requires its persisted position")


def _validate_binding(
    binding: Any, session: Session, config: Config, unresolved: bool
) -> None:
    if binding is None:
        if unresolved:
            raise ValueError("binding is required while ownership is unresolved")
        return
    if not unresolved:
        raise ValueError("binding must be null when no ownership is unresolved")
    fields = {
        "session_number",
        "server_name",
        "account_name",
        "network",
        "observed_default_wallet",
        "controller_id",
    }
    if not isinstance(binding, dict):
        raise ValueError("binding must be an object or null")
    _keys(binding, fields, "binding")
    for field in (
        "server_name",
        "account_name",
        "network",
        "observed_default_wallet",
        "controller_id",
    ):
        _nonempty(binding[field], f"binding {field}")
    if (
        type(binding["session_number"]) is not int
        or binding["session_number"] != session.number
    ):
        raise ValueError("binding session_number mismatch")
    if binding["controller_id"] != session.controller_id:
        raise ValueError("binding controller_id mismatch")
    if binding["account_name"] != config.account_name:
        raise ValueError("binding account_name mismatch")
    if binding["server_name"] != config.server_name:
        raise ValueError("binding server_name mismatch")
    if binding["network"] != NETWORK:
        raise ValueError("binding network mismatch")


def read_state(session: Session, config: Config) -> dict[str, Any] | None:
    require_live(session)
    if not session.state_path.exists():
        return None
    try:
        value = json.loads(session.state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load state.json: {exc}") from exc
    return validate_state(value, session, config)


def ensure_state(session: Session, config: Config) -> dict[str, Any]:
    state = read_state(session, config)
    if state is not None:
        return state
    state = initial_state(config)
    write_state(session, config, state)
    return state


def write_state(session: Session, config: Config, state: dict[str, Any]) -> None:
    validate_state(state, session, config)
    atomic_json(session.state_path, state)


def atomic_json(path: Path, value: Any) -> None:
    parent = path.parent.resolve()
    path = _contained(path, parent)
    if not parent.is_dir():
        raise ValueError(f"state directory does not exist: {parent}")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(json_value(value, redact=False), output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    if not path.parent.is_dir():
        raise ValueError(f"lock directory does not exist: {path.parent}")
    with path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def state_lock(session: Session) -> Iterator[None]:
    require_live(session)
    return file_lock(session.state_lock_path)


def wallet_lock(session: Session) -> Iterator[None]:
    require_live(session)
    return file_lock(session.wallet_lock_path)


def pool_address(slot: dict[str, Any]) -> str | None:
    position = slot.get("position") or {}
    pending = slot.get("pending_mutation") or {}
    request = pending.get("request") or {}
    value = position.get("pool_address") or request.get("pool_address")
    return str(value).strip() or None if value is not None else None


def capital(state: dict[str, Any], config: Config) -> dict[str, Any]:
    committed = Decimal(0)
    pools: dict[str, list[str]] = {}
    occupied = 0
    for slot_id, slot in state["slots"].items():
        position = slot.get("position")
        pending = slot.get("pending_mutation")
        if position is not None:
            committed += decimal(position["amount_quote"], positive=True)
            occupied += 1
        elif pending is not None and pending.get("type") == "open":
            committed += decimal(
                (pending.get("request") or {}).get("amount_quote"), positive=True
            )
            occupied += 1
        pool = pool_address(slot)
        if pool:
            pools.setdefault(pool, []).append(slot_id)
    return {
        "committed_quote": committed,
        "free_quote": max(Decimal(0), config.total_amount_quote - committed),
        "occupied_slot_count": occupied,
        "free_slot_count": config.max_slot_count - occupied,
        "pool_usage": pools,
    }


def _sensitive_key(key: Any) -> bool:
    normalized = "".join(
        character for character in str(key).lower() if character.isalnum()
    )
    return normalized in _SENSITIVE or any(
        marker in normalized for marker in _SENSITIVE_MARKERS
    )


def json_value(value: Any, *, redact: bool = True) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if redact and _sensitive_key(key)
                else json_value(item, redact=redact)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [json_value(item, redact=redact) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump(mode="python"), redact=redact)
    return str(value)


def text(value: Any) -> str:
    return str(value or "").strip()


def read(value: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None:
                return current
    return default


def rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        result = value
    elif isinstance(value, dict):
        result = next(
            (value[key] for key in keys if isinstance(value.get(key), list)), None
        )
    else:
        result = None
    if result is None or not all(isinstance(item, dict) for item in result):
        raise ValueError("response is not a recognized object list")
    return result


def executor_id(value: Any) -> str | None:
    config = (
        value.get("config")
        if isinstance(value, dict) and isinstance(value.get("config"), dict)
        else {}
    )
    found = read(value, "executor_id", "id") if isinstance(value, dict) else None
    return text(found or config.get("id")) or None


def _unique_text(
    value: dict[str, Any], paths: tuple[str, ...], label: str
) -> str | None:
    found = {
        item.strip()
        for path in paths
        if isinstance((item := read(value, path)), str) and item.strip()
    }
    if len(found) > 1:
        raise ValueError(f"conflicting {label} values")
    return next(iter(found), None)


def _preferred_optional_text(
    value: dict[str, Any], paths: tuple[str, ...], label: str
) -> tuple[bool, str | None]:
    for path in paths:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            return True, None if current is None else _nonempty(current, label)
    return False, None


def _optional_decimal(
    value: dict[str, Any], paths: tuple[str, ...], label: str, *, positive: bool = False
) -> Decimal | None:
    raw = [read(value, path) for path in paths if read(value, path) not in (None, "")]
    if not raw:
        return None
    parsed = {decimal(item, label, positive=positive) for item in raw}
    if len(parsed) != 1:
        raise ValueError(f"conflicting {label} values")
    return next(iter(parsed))


def _preferred_decimal(
    value: dict[str, Any], paths: tuple[str, ...], label: str, *, positive: bool = False
) -> Decimal | None:
    for path in paths:
        raw = read(value, path)
        if raw not in (None, ""):
            return decimal(raw, label, positive=positive)
    return None


def _optional_bool(
    value: dict[str, Any], paths: tuple[str, ...], label: str
) -> bool | None:
    raw = [read(value, path) for path in paths if read(value, path) is not None]
    if not raw:
        return None
    if not all(isinstance(item, bool) for item in raw) or len(set(raw)) != 1:
        raise ValueError(f"conflicting or invalid {label} values")
    return raw[0]


def _executor_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("executor response must be an object")
    if any(key in value for key in ("executor_id", "id", "controller_id", "config")):
        return value
    wrapped = [
        value[key]
        for key in ("executor", "data", "result", "item")
        if isinstance(value.get(key), dict)
    ]
    if len(wrapped) != 1:
        raise ValueError("executor response has no single recognized executor object")
    return wrapped[0]


def normalize_executor_response(
    value: Any,
    *,
    expected_executor_id: str | None = None,
    expected_controller_id: str | None = None,
) -> dict[str, Any]:
    executor = _executor_object(value)
    config = executor.get("config")
    if not isinstance(config, dict):
        raise ValueError("executor config must be an object")
    custom_info = executor.get("custom_info", {})
    if not isinstance(custom_info, dict):
        raise ValueError("executor custom_info must be an object")
    close_types = set()
    for source in (value, executor, config, custom_info):
        if not isinstance(source, dict):
            continue
        for field in ("close_type", "terminal_close_type"):
            if field in source and source[field] is not None:
                close_types.add(_nonempty(source[field], "executor close_type").upper())
    if len(close_types) > 1:
        raise ValueError("conflicting executor close_type values")
    close_type = next(iter(close_types), None)
    if close_type is not None and "close_type" not in custom_info:
        custom_info = {**custom_info, "close_type": close_type}
    actual_id = _unique_text(
        executor, ("executor_id", "id", "config.id"), "executor id"
    )
    owner = _unique_text(executor, ("controller_id",), "executor controller id")
    if owner is None:
        owner = _unique_text(config, ("controller_id",), "executor controller id")
    if actual_id is None or owner is None:
        raise ValueError("executor id and controller id are required")
    if expected_executor_id is not None and actual_id != expected_executor_id:
        raise ValueError("executor id does not match requested executor")
    if expected_controller_id is not None and owner != expected_controller_id:
        raise ValueError("executor is not owned by the requested controller")
    status = text(executor.get("status")).upper()
    if not status:
        raise ValueError("executor status is required")
    activity = executor.get("is_active")
    if activity is not None and not isinstance(activity, bool):
        raise ValueError("executor is_active must be boolean")
    if activity is None:
        active_states = {"RUNNING", "OPENING", "CLOSING", "SHUTTING_DOWN"}
        terminal_states = {
            "COMPLETE",
            "COMPLETED",
            "TERMINATED",
            "CANCELED",
            "CANCELLED",
            "CLOSED",
            "ERROR",
            "FAILED",
            "STOPPED",
        }
        if status not in active_states | terminal_states:
            raise ValueError(f"executor status '{status}' has unknown activity")
        activity = status in active_states
    pool = _unique_text(
        executor, ("pool_address", "config.pool_address"), "executor pool address"
    )
    position_present, position = _preferred_optional_text(
        executor,
        ("custom_info.position_address", "position_address"),
        "executor position address",
    )
    net_pnl_quote = _preferred_decimal(
        executor,
        ("net_pnl_quote", "custom_info.net_pnl_quote"),
        "executor net_pnl_quote",
    )
    net_pnl_pct = _preferred_decimal(
        executor,
        ("net_pnl_pct", "custom_info.net_pnl_pct"),
        "executor net_pnl_pct",
    )
    cumulative_fees = _preferred_decimal(
        executor,
        ("cum_fees_quote", "custom_info.cum_fees_quote"),
        "executor cumulative fees quote",
    )
    fees_earned = _preferred_decimal(
        executor,
        (
            "custom_info.fees_earned_quote",
            "fees_earned_quote",
        ),
        "executor fees earned quote",
    )
    filled_amount_quote = _preferred_decimal(
        executor,
        ("filled_amount_quote",),
        "executor filled_amount_quote",
    )
    current_price = _optional_decimal(
        custom_info, ("current_price",), "executor current_price", positive=True
    )
    if any(
        value is not None and value < 0
        for value in (cumulative_fees, fees_earned, filled_amount_quote)
    ):
        raise ValueError("executor fees and filled amount must be non-negative")
    return {
        "executor_id": actual_id,
        "controller_id": owner,
        "status": status,
        "is_active": activity,
        "config": json_value(config, redact=False),
        "custom_info": json_value(custom_info, redact=False),
        "pool_address": pool,
        "position_address": position,
        "position_address_present": position_present,
        "connector_name": _unique_text(
            executor,
            ("connector_name", "config.connector_name"),
            "executor connector name",
        ),
        "lp_provider": _unique_text(
            executor, ("lp_provider", "config.lp_provider"), "executor LP provider"
        ),
        "trading_pair": _unique_text(
            executor,
            ("trading_pair", "config.trading_pair"),
            "executor trading pair",
        ),
        "base_mint": _unique_text(
            executor,
            ("base_mint", "config.base_mint", "custom_info.base_mint"),
            "executor base mint",
        ),
        "quote_mint": _unique_text(
            executor,
            ("quote_mint", "config.quote_mint", "custom_info.quote_mint"),
            "executor quote mint",
        ),
        "net_pnl_quote": net_pnl_quote,
        "net_pnl_pct": net_pnl_pct,
        "cum_fees_quote": cumulative_fees,
        "fees_earned_quote": fees_earned,
        "filled_amount_quote": filled_amount_quote,
        "is_trading": _optional_bool(
            executor,
            ("is_trading", "custom_info.is_trading"),
            "executor is_trading",
        ),
        "current_price": current_price,
        "close_type": close_type,
        "custom_state": _unique_text(custom_info, ("state",), "executor custom state"),
        "range_state": _unique_text(
            custom_info, ("state", "range_state"), "executor range state"
        ),
    }


def _executor_page(value: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(value, list):
        page = value
        cursor = None
    elif isinstance(value, dict):
        page_keys = [
            key for key in ("executors", "data", "results", "items") if key in value
        ]
        if len(page_keys) != 1 or not isinstance(value[page_keys[0]], list):
            raise ValueError(
                "executor search response has no single recognized row list"
            )
        page = value[page_keys[0]]
        cursor_values = {
            text(item)
            for item in (
                value.get("next_cursor"),
                value.get("cursor"),
                read(value, "pagination.next_cursor"),
                read(value, "pagination.cursor"),
            )
            if text(item)
        }
        if len(cursor_values) > 1:
            raise ValueError("executor search response has conflicting cursors")
        cursor = next(iter(cursor_values), None)
    else:
        raise ValueError("executor search response must be an object or list")
    if not all(isinstance(item, dict) for item in page):
        raise ValueError("executor search returned an unrecognized row")
    return page, cursor


async def search_controller_executors(
    client: Any, controller_id: str
) -> list[dict[str, Any]]:
    controller_id = _nonempty(controller_id, "controller_id")
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(200):
        params: dict[str, Any] = {"controller_ids": [controller_id], "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client.executors.search_executors(**params)
        page, next_cursor = _executor_page(response)
        result.extend(
            normalize_executor_response(item, expected_controller_id=controller_id)
            for item in page
        )
        if next_cursor is None:
            return result
        if next_cursor in seen or next_cursor == cursor:
            raise ValueError("executor pagination cursor repeated")
        seen.add(next_cursor)
        cursor = next_cursor
    raise ValueError("executor pagination exceeded 200 pages")


async def get_executor_evidence(
    client: Any, executor_id: str, controller_id: str
) -> dict[str, Any]:
    executor_id = _nonempty(executor_id, "executor_id")
    return normalize_executor_response(
        await client.executors.get_executor(executor_id=executor_id),
        expected_executor_id=executor_id,
        expected_controller_id=_nonempty(controller_id, "controller_id"),
    )


def normalize_pool_info(value: Any, pool_address: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Gateway pool info must be an object")
    if "result" in value:
        if not isinstance(value["result"], dict):
            raise ValueError("Gateway pool result must be an object")
        value = value["result"]
    expected = _nonempty(pool_address, "pool_address")
    actual = _unique_text(
        value, ("pool_address", "poolAddress", "address"), "pool address"
    )
    base_mint = _unique_text(
        value,
        (
            "base_mint",
            "baseMint",
            "base_token_address",
            "token_a.mint",
            "token_a.address",
            "tokenA.mint",
            "tokenA.address",
            "tokenMintA.mint",
            "tokenMintA.address",
            "tokenMintA",
        ),
        "pool base mint",
    )
    quote_mint = _unique_text(
        value,
        (
            "quote_mint",
            "quoteMint",
            "quote_token_address",
            "token_b.mint",
            "token_b.address",
            "tokenB.mint",
            "tokenB.address",
            "tokenMintB.mint",
            "tokenMintB.address",
            "tokenMintB",
        ),
        "pool quote mint",
    )
    price = _optional_decimal(
        value,
        ("current_price", "currentPrice", "price"),
        "pool current price",
        positive=True,
    )
    if actual != expected or base_mint is None or quote_mint is None or price is None:
        raise ValueError("Gateway pool identity or current price is invalid")
    return {
        "pool_address": actual,
        "base_mint": base_mint,
        "quote_mint": quote_mint,
        "current_price": price,
    }


async def get_gateway_pool(client: Any, pool_address: str) -> dict[str, Any]:
    return normalize_pool_info(
        await client.gateway_clmm.get_pool_info(
            connector="orca", network=NETWORK, pool_address=pool_address
        ),
        pool_address,
    )


async def get_owned_positions(
    client: Any, pool_address: str, wallet_address: str | None = None
) -> list[dict[str, Any]]:
    pool_address = _nonempty(pool_address, "pool_address")
    params: dict[str, Any] = {
        "connector": "orca",
        "network": NETWORK,
        "pool_address": pool_address,
    }
    if wallet_address is not None:
        params["wallet_address"] = _nonempty(wallet_address, "wallet_address")
    response = await client.gateway_clmm.get_positions_owned(**params)
    if isinstance(response, dict):
        wrapper_keys = [key for key in ("result", "positions") if key in response]
        if len(wrapper_keys) != 1 or not isinstance(response[wrapper_keys[0]], list):
            raise ValueError("Gateway owned positions response wrapper is ambiguous")
        response = response[wrapper_keys[0]]
    if not isinstance(response, list) or not all(
        isinstance(item, dict) for item in response
    ):
        raise ValueError("Gateway owned positions response must be an object list")
    normalized = []
    addresses = set()
    for item in response:
        address = _unique_text(
            item, ("position_address", "positionAddress"), "position address"
        )
        observed_pool = _unique_text(
            item, ("pool_address", "poolAddress"), "position pool address"
        )
        if address is None or observed_pool not in (None, pool_address):
            raise ValueError("owned position identity does not match requested pool")
        if address in addresses:
            raise ValueError("Gateway returned a duplicate owned position")
        addresses.add(address)
        normalized.append(
            {
                "position_address": address,
                "pool_address": pool_address,
                "status": text(item.get("status")).upper() or None,
                "raw": json_value(item, redact=False),
            }
        )
    return normalized


def owned_position_addresses(positions: list[dict[str, Any]]) -> set[str]:
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    addresses = set()
    for position in positions:
        if not isinstance(position, dict):
            raise ValueError("position must be an object")
        addresses.add(_nonempty(position.get("position_address"), "position_address"))
    return addresses


async def read_token_metadata(
    client: Any, required: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    response = await client.gateway.get_network_tokens(NETWORK)
    token_rows = rows(response, "tokens")
    normalized = []
    for row in token_rows:
        mint = _unique_text(row, ("address", "token_address", "mint"), "token mint")
        symbol = _nonempty(row.get("symbol"), "token symbol")
        decimals = row.get("decimals")
        if mint is None or type(decimals) is not int or not 0 <= decimals <= 18:
            raise ValueError("Gateway token metadata row is invalid")
        normalized.append({"mint": mint, "symbol": symbol, "decimals": decimals})
    evidence = []
    for token in required:
        if not isinstance(token, dict):
            raise ValueError("required token metadata must be an object")
        mint = _nonempty(token.get("mint"), "required token mint")
        symbol = _nonempty(token.get("symbol"), "required token symbol")
        decimals = token.get("decimals")
        if type(decimals) is not int or not 0 <= decimals <= 18:
            raise ValueError("required token decimals are invalid")
        matches = [item for item in normalized if item["mint"] == mint]
        if len(matches) != 1 or matches[0] != {
            "mint": mint,
            "symbol": symbol,
            "decimals": decimals,
        }:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}")
        evidence.append(matches[0])
    return evidence


async def read_balances(
    client: Any, account_name: str, network: str = NETWORK
) -> list[dict[str, Any]]:
    account_name = _nonempty(account_name, "account_name")
    network = _nonempty(network, "network")
    response = await client.portfolio.get_state(
        account_names=[account_name], connector_names=[network], refresh=True
    )
    if not isinstance(response, dict) or not isinstance(
        response.get(account_name), dict
    ):
        raise ValueError("scoped portfolio account was not returned")
    balance_rows = response[account_name].get(network)
    if not isinstance(balance_rows, list) or not all(
        isinstance(item, dict) for item in balance_rows
    ):
        raise ValueError("scoped portfolio balances were not returned")
    normalized = []
    for item in balance_rows:
        symbol = _unique_text(item, ("token", "symbol"), "balance symbol")
        mint = _unique_text(item, ("mint", "token_address", "address"), "balance mint")
        available = _optional_decimal(
            item,
            ("available_units", "available", "available_balance", "units"),
            "available balance",
        )
        if symbol is None or available is None or available < 0:
            raise ValueError("portfolio balance row is invalid")
        normalized.append({"symbol": symbol, "mint": mint, "available": available})
    return normalized


def balance_for_token(
    balances: list[dict[str, Any]], mint: str, symbol: str
) -> Decimal:
    mint = _nonempty(mint, "token mint")
    symbol = _nonempty(symbol, "token symbol")
    mint_matches = [item for item in balances if text(item.get("mint")) == mint]
    if len(mint_matches) == 1:
        return decimal(mint_matches[0].get("available"), "available balance")
    if len(mint_matches) > 1:
        raise ValueError("multiple portfolio rows matched token mint")
    if any(text(item.get("mint")) for item in balances):
        raise ValueError("token mint was not present in mint-aware portfolio balances")
    symbol_matches = [
        item for item in balances if text(item.get("symbol")).upper() == symbol.upper()
    ]
    if len(symbol_matches) != 1:
        raise ValueError("symbol-only portfolio balance match is not unique")
    return decimal(symbol_matches[0].get("available"), "available balance")


def _envelope_text(
    outer: dict[str, Any],
    result: dict[str, Any],
    paths: tuple[str, ...],
    label: str,
    *,
    upper: bool = False,
) -> str | None:
    values = {
        value.upper() if upper else value
        for source in (outer, result)
        if (value := _unique_text(source, paths, label)) is not None
    }
    if len(values) > 1:
        raise ValueError(f"conflicting {label} values")
    return next(iter(values), None)


def _envelope_decimal(
    outer: dict[str, Any],
    result: dict[str, Any],
    paths: tuple[str, ...],
    label: str,
) -> Decimal | None:
    values = {
        value
        for source in (outer, result)
        if (value := _optional_decimal(source, paths, label)) is not None
    }
    if len(values) > 1:
        raise ValueError(f"conflicting {label} values")
    return next(iter(values), None)


def _envelope_timestamp(outer: dict[str, Any], result: dict[str, Any]) -> str | None:
    values = {
        _parse_timestamp(raw, "transaction timestamp")
        for source in (outer, result)
        for path in ("timestamp", "created_at", "submitted_at")
        if (raw := read(source, path)) is not None
    }
    if len(values) > 1:
        raise ValueError("conflicting transaction timestamp values")
    timestamp = next(iter(values), None)
    return timestamp.isoformat() if timestamp is not None else None


def normalize_transaction(
    value: Any, expected_transaction_hash: str | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("transaction response must be an object")
    if "result" in value:
        if not isinstance(value["result"], dict):
            raise ValueError("transaction result must be an object")
        result = value["result"]
    else:
        result = value
    transaction_hash = _envelope_text(
        value,
        result,
        ("transaction_hash", "tx_hash", "signature", "hash"),
        "transaction hash",
    )
    if transaction_hash is None:
        raise ValueError("transaction hash is required")
    if (
        expected_transaction_hash is not None
        and transaction_hash != expected_transaction_hash
    ):
        raise ValueError("transaction hash does not match requested transaction")
    input_amount = _envelope_decimal(
        value,
        result,
        ("input_amount", "amount_in"),
        "transaction input amount",
    )
    output_amount = _envelope_decimal(
        value,
        result,
        ("output_amount", "amount_out"),
        "transaction output amount",
    )
    base_amount = _envelope_decimal(
        value, result, ("base_amount",), "transaction base amount"
    )
    quote_amount = _envelope_decimal(
        value, result, ("quote_amount",), "transaction quote amount"
    )
    slippage_pct = _envelope_decimal(
        value, result, ("slippage_pct",), "transaction slippage_pct"
    )
    if any(
        amount is not None and amount < 0
        for amount in (
            input_amount,
            output_amount,
            base_amount,
            quote_amount,
            slippage_pct,
        )
    ):
        raise ValueError("transaction amounts and slippage must be non-negative")
    return {
        "transaction_hash": transaction_hash,
        "status": _envelope_text(
            value, result, ("status",), "transaction status", upper=True
        ),
        "result_id": _envelope_text(value, result, ("result_id", "id"), "result id"),
        "position_address": _envelope_text(
            value,
            result,
            ("position_address", "positionAddress"),
            "result position address",
        ),
        "input_amount": input_amount,
        "output_amount": output_amount,
        "base_amount": base_amount,
        "quote_amount": quote_amount,
        "slippage_pct": slippage_pct,
        "timestamp": _envelope_timestamp(value, result),
        "connector": _envelope_text(
            value, result, ("connector",), "transaction connector"
        ),
        "network": _envelope_text(value, result, ("network",), "transaction network"),
        "wallet_address": _envelope_text(
            value,
            result,
            ("wallet_address", "walletAddress"),
            "transaction wallet address",
        ),
        "trading_pair": _envelope_text(
            value,
            result,
            ("trading_pair", "tradingPair"),
            "transaction trading pair",
        ),
        "side": _envelope_text(
            value, result, ("side",), "transaction side", upper=True
        ),
        "base_token": _envelope_text(
            value, result, ("base_token", "baseToken"), "base token"
        ),
        "quote_token": _envelope_text(
            value, result, ("quote_token", "quoteToken"), "quote token"
        ),
        "base_mint": _envelope_text(
            value, result, ("base_mint", "baseMint"), "base mint"
        ),
        "quote_mint": _envelope_text(
            value, result, ("quote_mint", "quoteMint"), "quote mint"
        ),
    }


async def get_swap_evidence(client: Any, transaction_hash: str) -> dict[str, Any]:
    transaction_hash = _nonempty(transaction_hash, "transaction_hash")
    return normalize_transaction(
        await client.gateway_swap.get_swap_status(transaction_hash), transaction_hash
    )


def _submitted_swap_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("swap request must be an object")
    nested_keys = [key for key in ("rebalance_swap", "restore_swap") if key in request]
    direct = "connector" in request
    if direct and nested_keys:
        raise ValueError("swap request is ambiguous")
    if nested_keys:
        if len(nested_keys) != 1 or not isinstance(request[nested_keys[0]], dict):
            raise ValueError("nested swap request is ambiguous")
        request = request[nested_keys[0]]
    connector = _nonempty(request.get("connector"), "swap connector")
    network = _nonempty(request.get("network"), "swap network")
    if connector != "jupiter" or network != NETWORK:
        raise ValueError("submitted swap must use Jupiter on Solana mainnet")
    side = _nonempty(request.get("side"), "swap side").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("swap side must be BUY or SELL")
    amount = decimal(request.get("amount"), "swap amount", positive=True)
    slippage = decimal(request.get("slippage_pct"), "swap slippage_pct")
    if slippage < 0:
        raise ValueError("swap slippage_pct must be non-negative")
    return {
        "connector": connector,
        "network": network,
        "wallet_address": _nonempty(
            request.get("wallet_address"), "swap wallet_address"
        ),
        "trading_pair": _nonempty(request.get("trading_pair"), "swap trading_pair"),
        "side": side,
        "amount": amount,
        "slippage_pct": slippage,
        "base_mint": _nonempty(request.get("base_mint"), "swap base_mint"),
        "quote_mint": _nonempty(request.get("quote_mint"), "swap quote_mint"),
        "base_token": text(request.get("base_token") or request.get("base_symbol"))
        or None,
        "quote_token": text(request.get("quote_token") or request.get("quote_symbol"))
        or None,
    }


def _swap_search_page(
    response: Any, requested_offset: int, requested_limit: int
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise ValueError("swap search response must contain a data list")
    page = response["data"]
    if not all(isinstance(row, dict) for row in page):
        raise ValueError("swap search returned an unrecognized row")
    pagination = response.get("pagination")
    if not isinstance(pagination, dict):
        raise ValueError("swap search pagination is missing")
    offset = pagination.get("offset")
    limit = pagination.get("limit")
    if type(offset) is not int or offset != requested_offset:
        raise ValueError("swap search pagination offset is invalid")
    if type(limit) is not int or not 0 < limit <= requested_limit:
        raise ValueError("swap search pagination limit is invalid")
    totals = {
        total
        for key in ("total", "total_count")
        if (total := pagination.get(key)) is not None
    }
    if any(type(total) is not int or total < 0 for total in totals) or len(totals) > 1:
        raise ValueError("swap search pagination total is invalid")
    total = next(iter(totals), None)
    has_more = pagination.get("has_more")
    if has_more is not None and not isinstance(has_more, bool):
        raise ValueError("swap search pagination has_more is invalid")
    next_offset = requested_offset + len(page)
    if total is not None:
        if next_offset > total:
            raise ValueError("swap search pagination exceeded total")
        more = next_offset < total
        if has_more is not None and has_more != more:
            raise ValueError("swap search pagination is contradictory")
    elif has_more is not None:
        more = has_more
    else:
        raise ValueError("swap search pagination has no bound")
    if more and not page:
        raise ValueError("swap search pagination did not advance")
    return page, more


def _matches_swap(
    evidence: dict[str, Any], request: dict[str, Any], *, require_slippage: bool
) -> bool:
    for key in ("connector", "network", "wallet_address", "trading_pair", "side"):
        if evidence.get(key) is None or evidence[key] != request[key]:
            return False
    if require_slippage and evidence.get("slippage_pct") is None:
        return False
    if (
        evidence.get("slippage_pct") is not None
        and evidence["slippage_pct"] != request["slippage_pct"]
    ):
        return False
    for key in ("base_mint", "quote_mint", "base_token", "quote_token"):
        if evidence.get(key) is not None and (
            request.get(key) is None or evidence[key] != request[key]
        ):
            return False
    amount_key = "output_amount" if request["side"] == "BUY" else "input_amount"
    return evidence.get(amount_key) == request["amount"]


async def find_submitted_swap(
    client: Any, request: dict[str, Any], attempted_at: str
) -> dict[str, Any] | None:
    request = _submitted_swap_request(request)
    attempted = _parse_timestamp(attempted_at, "attempted_at")
    start = attempted - timedelta(seconds=60)
    end = attempted + timedelta(seconds=60)
    limit = 100
    offset = 0
    seen_hashes: set[str] = set()
    matches: list[dict[str, Any]] = []
    for _ in range(100):
        params = {
            "network": request["network"],
            "connector": request["connector"],
            "wallet_address": request["wallet_address"],
            "trading_pair": request["trading_pair"],
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "limit": limit,
            "offset": offset,
        }
        # Pinned client 1.5.3's public search_swaps sends a JSON body; live API 1.0.1 defines and uses query parameters.
        response = await client.gateway_swap._post(
            "/gateway/swaps/search", params=params
        )
        page, more = _swap_search_page(response, offset, limit)
        for row in page:
            evidence = normalize_transaction(row)
            transaction_hash = evidence["transaction_hash"]
            if transaction_hash in seen_hashes:
                raise ValueError("swap search returned a repeated transaction")
            seen_hashes.add(transaction_hash)
            if evidence["timestamp"] is None:
                raise ValueError("swap search row is missing timestamp")
            observed = _parse_timestamp(evidence["timestamp"], "swap timestamp")
            if start <= observed <= end and _matches_swap(
                evidence, request, require_slippage=True
            ):
                matches.append(evidence)
        if not more:
            break
        offset += len(page)
    else:
        raise ValueError("swap search pagination exceeded 100 pages")
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("submitted swap evidence is ambiguous")
    searched = matches[0]
    status = normalize_transaction(
        await client.gateway_swap.get_swap_status(searched["transaction_hash"]),
        searched["transaction_hash"],
    )
    if status["status"] is None or not _matches_swap(
        status, request, require_slippage=False
    ):
        raise ValueError("swap status evidence contradicts submitted request")
    for key, value in searched.items():
        if status.get(key) is None:
            status[key] = value
    return status


def executor_plan_mismatches(
    executor: dict[str, Any], expected: dict[str, Any]
) -> list[str]:
    if not isinstance(expected, dict):
        return ["executor_plan"]
    outer_expected = expected
    if isinstance(expected.get("executor_config"), dict):
        expected = dict(expected["executor_config"])
        for key in ("controller_id", "swap_provider"):
            if key not in expected and key in outer_expected:
                expected[key] = outer_expected[key]
    owner = text(expected.get("controller_id"))
    try:
        actual = normalize_executor_response(
            executor, expected_controller_id=owner or None
        )
    except ValueError:
        return ["executor"]
    config = actual["config"]
    mismatches = []
    identities = {
        "controller_id": actual["controller_id"],
        "pool_address": actual["pool_address"],
        "connector_name": actual["connector_name"],
        "lp_provider": actual["lp_provider"],
        "trading_pair": actual["trading_pair"],
    }
    for key, value in identities.items():
        if not text(expected.get(key)) or text(value) != text(expected.get(key)):
            mismatches.append(key)
    if "swap_provider" in expected and config.get("swap_provider") is not None:
        if text(config.get("swap_provider")) != text(expected.get("swap_provider")):
            mismatches.append("swap_provider")
    for key in (
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
        "base_amount",
        "quote_amount",
    ):
        try:
            if not decimal_matches(config.get(key), expected.get(key), key):
                mismatches.append(key)
        except ValueError:
            mismatches.append(key)
    if "total_amount_quote" in expected:
        try:
            if not decimal_matches(
                config.get("total_amount_quote"),
                expected["total_amount_quote"],
                "total_amount_quote",
            ):
                mismatches.append("total_amount_quote")
        except ValueError:
            mismatches.append("total_amount_quote")
    if text(config.get("side")).upper() not in {"3", "RANGE"} or text(
        expected.get("side")
    ).upper() not in {"3", "RANGE"}:
        mismatches.append("side")
    if (
        config.get("keep_position") is not True
        or expected.get("keep_position") is not True
    ):
        mismatches.append("keep_position")
    return mismatches


async def bound_client(context: Any, expected_server: str | None = None) -> BoundClient:
    from config_manager import get_config_manager, get_effective_server

    manager = get_config_manager()
    if expected_server is not None:
        server_name = _nonempty(expected_server, "expected_server")
        return BoundClient(server_name, await manager.get_client(server_name))
    chat_id = getattr(context, "_chat_id", 0) or 0
    user_data = getattr(context, "user_data", None)
    if user_data is None:
        user_data = getattr(context, "_user_data", None)
    server_name = get_effective_server(chat_id, user_data)
    if not server_name:
        server_name = manager.get_chat_default_server(chat_id)
    if not server_name:
        raise ValueError("no current Hummingbot API server is configured")
    return BoundClient(server_name, await manager.get_client(server_name))


async def default_solana_wallet(client: Any) -> str:
    response = await client.gateway.get_network_config(NETWORK)
    if not isinstance(response, dict):
        raise ValueError("Gateway network config must be an object")
    return _nonempty(response.get("default_wallet"), "Gateway default_wallet")
