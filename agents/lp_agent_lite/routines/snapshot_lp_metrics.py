"""Save one loop-tick metrics artifact or preview one experiment observation."""

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_agent_lite.routines._reporting import DiagnosticTrace, report_result

CATEGORY = "Orca LP Metrics"
_CONTROLLER = re.compile(r"^lp_agent_lite\.orca_(?P<suffix>e?[1-9]\d*)$")
_TARGET_CHARS = 1_700


def _finite(value: Decimal | None) -> bool:
    return value is None or value.is_finite()


class PositionMetric(BaseModel):
    """One current-tick position observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    executor_id: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    position_address: StrictStr = Field(min_length=1, max_length=64)
    pool_address: StrictStr = Field(min_length=1, max_length=64)
    position_mint: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    state: Literal["active", "closing", "closed", "untracked", "foreign", "ambiguous"]
    age_minutes: Decimal | None = Field(default=None, ge=0)
    base_amount: Decimal | None = Field(default=None, ge=0)
    quote_amount: Decimal | None = Field(default=None, ge=0)
    fees_quote: Decimal | None = None
    pnl_quote: Decimal | None = None
    pnl_ratio: Decimal | None = None

    @model_validator(mode="after")
    def finite_numbers(self):
        values = (
            self.age_minutes,
            self.base_amount,
            self.quote_amount,
            self.fees_quote,
            self.pnl_quote,
            self.pnl_ratio,
        )
        if not all(_finite(value) for value in values):
            raise ValueError("position metrics must be finite")
        if self.state in {"active", "closing", "closed"} and self.executor_id is None:
            raise ValueError("tracked position states require executor_id")
        return self


class ResidualMetric(BaseModel):
    """One current-tick residual-inventory observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mint: StrictStr = Field(min_length=1, max_length=64)
    amount: Decimal = Field(ge=0)
    value_quote: Decimal | None = Field(default=None, ge=0)
    status: Literal["clean", "cleanup", "unattributed"]

    @model_validator(mode="after")
    def finite_numbers(self):
        if not _finite(self.amount) or not _finite(self.value_quote):
            raise ValueError("residual metrics must be finite")
        return self


class LastMutation(BaseModel):
    """Exact native identity for the latest observed current-session mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["register", "prepare", "open", "close", "cleanup", "stop"]
    identity: StrictStr = Field(min_length=1, max_length=128)
    status: StrictStr = Field(min_length=1, max_length=64)
    transaction: StrictStr | None = Field(default=None, min_length=1, max_length=128)


class Config(BaseModel):
    """Save or preview facts already observed for the exact current Condor tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr
    tick: StrictInt = Field(gt=0)
    session_pnl_quote: Decimal
    quote_balance: Decimal = Field(ge=0)
    sol_balance: Decimal = Field(ge=0)
    positions: list[PositionMetric] = Field(default_factory=list, max_length=20)
    residuals: list[ResidualMetric] = Field(default_factory=list, max_length=20)
    last: LastMutation | None = None

    @model_validator(mode="after")
    def validate_snapshot(self):
        if not _CONTROLLER.fullmatch(self.controller_id):
            raise ValueError("controller_id must identify lp_agent_lite.orca")
        if not all(
            _finite(value)
            for value in (self.session_pnl_quote, self.quote_balance, self.sol_balance)
        ):
            raise ValueError("wallet and session metrics must be finite")
        position_keys = [
            (row.position_address, row.executor_id or "") for row in self.positions
        ]
        if len(position_keys) != len(set(position_keys)):
            raise ValueError("position metric identities must be unique")
        residual_mints = [row.mint for row in self.residuals]
        if len(residual_mints) != len(set(residual_mints)):
            raise ValueError("residual metric mints must be unique")
        return self


def _get_engine(controller_id: str) -> Any:
    from condor.agents.engine import get_engine

    return get_engine(controller_id)


def _resolve(config: Config) -> dict[str, Any]:
    match = _CONTROLLER.fullmatch(config.controller_id)
    engine = _get_engine(config.controller_id)
    if engine is None:
        raise ValueError("active Condor instance was not found")
    mode = (
        engine.config.get("execution_mode") if isinstance(engine.config, dict) else None
    )
    strategy = getattr(engine, "strategy", None)
    if (
        mode not in {"dry_run", "run_once", "loop"}
        or getattr(getattr(engine, "agent", None), "slug", None) != "lp_agent_lite"
        or getattr(engine, "agent_id", None) != config.controller_id
        or getattr(strategy, "slug", None) != "orca"
        or getattr(engine, "status", None) != "running"
    ):
        raise ValueError("active Condor identity, mode, or lifecycle is invalid")
    suffix = match.group("suffix")
    if mode == "loop":
        if suffix.startswith("e"):
            raise ValueError("loop mode requires a loop controller identity")
        journal = getattr(engine, "journal", None)
        if not isinstance(getattr(journal, "tick_count", None), int):
            raise ValueError("current loop tick authority is unavailable")
        current_tick = journal.tick_count + 1
        session_dir = getattr(engine, "session_dir", None)
        strategy_dir = Path(strategy.dir).resolve()
        expected = strategy_dir / "sessions" / f"session_{suffix}"
        if (
            not isinstance(session_dir, Path)
            or session_dir.is_symlink()
            or session_dir.resolve() != expected.resolve()
            or not session_dir.resolve().is_relative_to(strategy_dir)
        ):
            raise ValueError("current loop session directory is unsafe")
        config_path = session_dir / "config.yml"
        if not config_path.is_file() or config_path.is_symlink():
            raise ValueError("current loop session config is unavailable")
        before = config_path.stat()
        start_epoch = before.st_mtime
        after = config_path.stat()
        if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
            raise ValueError("current loop session config changed while inspected")
    else:
        if not suffix.startswith("e"):
            raise ValueError(
                "experiment mode requires an experiment controller identity"
            )
        current_tick = 1
        session_dir = None
        start_epoch = None
    if config.tick != current_tick:
        raise ValueError("requested tick is not the current engine tick")
    tick_epoch = getattr(engine, "_last_tick_at", None)
    if (
        isinstance(tick_epoch, bool)
        or not isinstance(tick_epoch, (int, float))
        or tick_epoch <= 0
    ):
        raise ValueError("current tick timestamp is unavailable")
    return {
        "mode": mode,
        "session_dir": session_dir,
        "tick_epoch": float(tick_epoch),
        "start_epoch": start_epoch,
    }


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _build(config: Config, scope: dict[str, Any]) -> dict[str, Any]:
    positions = sorted(
        config.positions,
        key=lambda row: (row.position_address, row.executor_id or ""),
    )
    residuals = sorted(config.residuals, key=lambda row: row.mint)
    payload: dict[str, Any] = {
        "schema": "lp_agent_lite.metrics.v1",
        "status": "complete" if scope["mode"] == "loop" else "preview",
        "mutation": False,
        "artifact_write": scope["mode"] == "loop",
        "controller": config.controller_id,
        "tick": config.tick,
        "ts": datetime.fromtimestamp(scope["tick_epoch"], timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "session": {
            "pnl_q": _decimal(config.session_pnl_quote),
        },
        "wallet": {
            "quote": _decimal(config.quote_balance),
            "sol": _decimal(config.sol_balance),
        },
        "p_cols": [
            "eid",
            "pos",
            "pool",
            "mint",
            "state",
            "age_min",
            "base",
            "quote",
            "fees_q",
            "pnl_q",
            "pnl_r",
        ],
        "p": [
            [
                row.executor_id,
                row.position_address,
                row.pool_address,
                row.position_mint,
                row.state,
                _decimal(row.age_minutes),
                _decimal(row.base_amount),
                _decimal(row.quote_amount),
                _decimal(row.fees_quote),
                _decimal(row.pnl_quote),
                _decimal(row.pnl_ratio),
            ]
            for row in positions
        ],
        "p_omit": 0,
        "r_cols": ["mint", "amount", "value_q", "status"],
        "r": [
            [row.mint, _decimal(row.amount), _decimal(row.value_quote), row.status]
            for row in residuals
        ],
        "r_omit": 0,
        "last": (
            None
            if config.last is None
            else [
                config.last.kind,
                config.last.identity,
                config.last.status,
                config.last.transaction,
            ]
        ),
    }
    if scope["mode"] == "loop":
        start = scope["start_epoch"]
        payload["session"]["start"] = (
            datetime.fromtimestamp(start, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        payload["session"]["age_min"] = _decimal(
            Decimal(str(max(0.0, scope["tick_epoch"] - start))) / Decimal("60")
        )
    else:
        payload["preview"] = True
    while len(json.dumps(payload, separators=(",", ":"))) > _TARGET_CHARS:
        if payload["r"]:
            payload["r"].pop()
            payload["r_omit"] += 1
        elif payload["p"]:
            payload["p"].pop()
            payload["p_omit"] += 1
        else:
            raise ValueError("fixed metrics schema exceeds transport limit")
    return payload


def _save_once(path: Path, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":"))
    if path.exists():
        if path.is_symlink():
            raise ValueError("current tick metrics target is unsafe")
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("current tick metrics target is unreadable") from exc
        if existing != payload:
            raise FileExistsError(
                "current tick metrics already contain different facts"
            )
        return
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise ValueError("current session metrics directory is unsafe")
    parent.mkdir(mode=0o700, exist_ok=True)
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("metrics temporary target already exists")
    try:
        temporary.write_text(raw)
        try:
            os.link(temporary, path)
        except FileExistsError:
            try:
                existing = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("current tick metrics target is unreadable") from exc
            if existing != payload:
                raise FileExistsError(
                    "current tick metrics already contain different facts"
                )
    finally:
        if temporary.exists():
            temporary.unlink()


def _error(reason: str) -> str:
    return json.dumps(
        {
            "schema": "lp_agent_lite.metrics.v1",
            "status": "unavailable",
            "mutation": False,
            "reason": " ".join(reason.split())[:180],
        },
        separators=(",", ":"),
    )


async def run(config: Config, context: Any) -> str:
    trace = DiagnosticTrace()
    trace.record("input_validated", controller=config.controller_id, tick=config.tick)
    try:
        scope = _resolve(config)
        trace.record("runtime_scope_resolved", mode=scope["mode"])
        payload = _build(config, scope)
        trace.record(
            "metrics_compacted",
            positions=len(payload["p"]),
            positions_omitted=payload["p_omit"],
            residuals=len(payload["r"]),
            residuals_omitted=payload["r_omit"],
        )
        if scope["mode"] == "loop":
            target = scope["session_dir"] / "metrics" / f"tick_{config.tick}.json"
            _save_once(target, payload)
            trace.record("metrics_artifact", path=target, outcome="saved")
        else:
            trace.record("metrics_artifact", outcome="preview", write=False)
        raw = json.dumps(payload, separators=(",", ":"))
    except Exception as exc:
        trace.record(
            "metrics_snapshot",
            "error",
            error_type=type(exc).__name__,
        )
        raw = _error(str(exc) or type(exc).__name__)
    return await report_result(
        raw,
        routine_name="snapshot_lp_metrics",
        title="LP Agent Lite — Metrics Snapshot Diagnostic",
        config=config,
        trace=trace,
        context=context,
    )
