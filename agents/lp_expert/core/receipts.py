"""Current-controller operation receipts and serialization for LP Expert."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from agents.lp_expert.core.runtime import RuntimeScope

OperationKind = Literal["swap", "create"]

_SAFE_OPERATION = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_PHASES = {
    "admitted",
    "rejected_before_submit",
    "submitting",
    "submitted",
    "confirmed",
    "uncertain",
    "ambiguous",
    "manual_review",
}
_TRANSITIONS = {
    "admitted": {
        "admitted",
        "rejected_before_submit",
        "submitting",
        "manual_review",
    },
    "rejected_before_submit": {
        "rejected_before_submit",
        "admitted",
        "submitting",
        "manual_review",
    },
    "submitting": {
        "submitting",
        "submitted",
        "confirmed",
        "uncertain",
        "manual_review",
    },
    "submitted": {"submitted", "confirmed", "uncertain", "manual_review"},
    "uncertain": {"uncertain", "submitted", "confirmed", "ambiguous", "manual_review"},
    "ambiguous": {"ambiguous", "manual_review"},
    "manual_review": {"manual_review"},
    "confirmed": {"confirmed"},
}
_UNRESOLVED = {"submitting", "submitted", "uncertain", "ambiguous", "manual_review"}
_CONTROLLER_LOCKS: dict[str, asyncio.Lock] = {}
_MEMORY_RECORDS: dict[tuple[str, str], dict[str, Any]] = {}
_MEMORY_ADMISSIONS: dict[tuple[str, str, int], str] = {}


def controller_mutation_lock(controller_id: str) -> asyncio.Lock:
    """Return the process-local serialization lock for one controller wallet."""

    if not isinstance(controller_id, str) or not controller_id:
        raise ValueError("controller identity is required for mutation serialization")
    return _CONTROLLER_LOCKS.setdefault(controller_id, asyncio.Lock())


@dataclass(frozen=True, slots=True)
class OperationIdentity:
    """Immutable operation identity stored with every receipt transition."""

    controller_id: str
    strategy_slug: str
    operation_id: str
    operation_kind: OperationKind
    tick: int
    account_name: str
    network: str
    wallet_address: str
    intent: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            not _SAFE_OPERATION.fullmatch(self.operation_id)
            or self.operation_kind not in {"swap", "create"}
            or not isinstance(self.tick, int)
            or isinstance(self.tick, bool)
            or self.tick < 1
            or not all(
                isinstance(value, str) and value.strip()
                for value in (
                    self.controller_id,
                    self.strategy_slug,
                    self.account_name,
                    self.network,
                    self.wallet_address,
                )
            )
            or not isinstance(self.intent, Mapping)
        ):
            raise ValueError("operation receipt identity is invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "controller_id": self.controller_id,
            "strategy_slug": self.strategy_slug,
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
            "tick": self.tick,
            "account_name": self.account_name,
            "network": self.network,
            "wallet_address": self.wallet_address,
            "intent": _json_tree(dict(self.intent)),
        }


def _json_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_tree(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _record_matches(record: dict[str, Any], identity: OperationIdentity) -> bool:
    expected = identity.as_dict()
    return all(record.get(key) == value for key, value in expected.items())


class ReceiptStore:
    """Atomic exact-session receipts shared by swap and create operations."""

    VERSION = 1

    def __init__(self, scope: RuntimeScope, *, read_only: bool = False):
        if not scope.wallet_address:
            raise ValueError("wallet-bound runtime scope is required for receipts")
        self.scope = scope
        self.read_only = read_only
        self._directory = self._resolve_directory(scope, read_only=read_only)

    @staticmethod
    def _resolve_directory(scope: RuntimeScope, *, read_only: bool) -> Path | None:
        if scope.execution_mode != "loop":
            return None
        if scope.session_dir is None:
            raise ValueError("current loop session directory is unavailable")
        session = scope.session_dir
        if not session.is_dir() or session.is_symlink():
            raise ValueError("current loop session directory is unsafe")
        resolved = session.resolve()
        directory = session / "lp_operations"
        if directory.exists() and directory.is_symlink():
            raise ValueError("operation receipt directory must not be a symlink")
        if not read_only:
            directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.resolve().is_relative_to(resolved):
            raise ValueError("operation receipt directory escapes the current session")
        return directory

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("read-only receipt store cannot mutate session state")

    def identity(
        self,
        *,
        operation_id: str,
        operation_kind: OperationKind,
        intent: Mapping[str, Any],
        tick: int | None = None,
    ) -> OperationIdentity:
        return OperationIdentity(
            controller_id=self.scope.controller_id,
            strategy_slug=self.scope.strategy_slug,
            operation_id=operation_id,
            operation_kind=operation_kind,
            tick=self.scope.current_tick if tick is None else tick,
            account_name=self.scope.account_name,
            network=self.scope.network,
            wallet_address=str(self.scope.wallet_address),
            intent=intent,
        )

    def _path(self, operation_id: str) -> Path:
        if not _SAFE_OPERATION.fullmatch(operation_id):
            raise ValueError("operation id is invalid")
        if self._directory is None:
            raise ValueError("experiment receipts are process-local")
        return self._directory / f"{operation_id}.json"

    def read(self, identity: OperationIdentity) -> dict[str, Any] | None:
        """Read one exact operation or fail on any identity conflict."""

        if self._directory is None:
            value = _MEMORY_RECORDS.get((identity.controller_id, identity.operation_id))
            value = copy.deepcopy(value) if value is not None else None
        else:
            path = self._path(identity.operation_id)
            if not path.exists():
                return None
            if path.is_symlink():
                raise ValueError("operation receipt must not be a symlink")
            value = json.loads(path.read_text())
        if not isinstance(value, dict) or not self._valid(value, identity):
            raise ValueError("operation receipt identity or content is invalid")
        return value

    def read_by_id(self, operation_id: str) -> dict[str, Any] | None:
        """Read an operation by ID while still enforcing this scope."""

        if not _SAFE_OPERATION.fullmatch(operation_id):
            raise ValueError("operation id is invalid")
        if self._directory is None:
            value = _MEMORY_RECORDS.get((self.scope.controller_id, operation_id))
            value = copy.deepcopy(value) if value is not None else None
        else:
            path = self._path(operation_id)
            if not path.exists():
                return None
            if path.is_symlink():
                raise ValueError("operation receipt must not be a symlink")
            value = json.loads(path.read_text())
        if (
            not isinstance(value, dict)
            or value.get("version") != self.VERSION
            or value.get("operation_id") != operation_id
            or value.get("operation_kind") not in {"swap", "create"}
            or not isinstance(value.get("tick"), int)
            or isinstance(value.get("tick"), bool)
            or value.get("tick") < 1
            or value.get("controller_id") != self.scope.controller_id
            or value.get("strategy_slug") != self.scope.strategy_slug
            or value.get("account_name") != self.scope.account_name
            or value.get("network") != self.scope.network
            or value.get("wallet_address") != self.scope.wallet_address
            or value.get("phase") not in _PHASES
            or not isinstance(value.get("mutation_possible"), bool)
            or not isinstance(value.get("intent"), dict)
            or (
                value.get("result") is not None
                and not isinstance(value.get("result"), dict)
            )
            or (
                value.get("reason") is not None
                and not isinstance(value.get("reason"), str)
            )
        ):
            raise ValueError("operation receipt scope or content is invalid")
        return value

    def read_confirmed_swap(self, operation_id: str) -> dict[str, Any]:
        """Return one exact confirmed swap for lp_create attribution."""

        value = self.read_by_id(operation_id)
        result = value.get("result") if isinstance(value, dict) else None
        receipt = result.get("receipt") if isinstance(result, dict) else None
        if (
            value is None
            or value.get("operation_kind") != "swap"
            or value.get("phase") != "confirmed"
            or not isinstance(receipt, dict)
            or not receipt.get("transaction_hash")
            or receipt.get("input_amount") in (None, "")
            or receipt.get("output_amount") in (None, "")
        ):
            raise ValueError("confirmed preparation swap receipt is unavailable")
        return copy.deepcopy(value)

    def write(
        self,
        identity: OperationIdentity,
        *,
        phase: str,
        mutation_possible: bool,
        result: dict[str, Any] | None = None,
        reason: str | None = None,
        create_only: bool = False,
    ) -> dict[str, Any]:
        """Atomically create or transition one exact operation receipt."""

        self._require_writable()
        if phase not in _PHASES or not isinstance(mutation_possible, bool):
            raise ValueError("operation receipt phase is invalid")
        existing = self.read(identity)
        if create_only and existing is not None:
            raise FileExistsError("operation receipt already exists")
        if existing is not None:
            if phase not in _TRANSITIONS[str(existing["phase"])]:
                raise ValueError("operation receipt phase transition is invalid")
            if existing.get("mutation_possible") is True and not mutation_possible:
                raise ValueError("mutation possibility cannot be downgraded")
        now = datetime.now(timezone.utc).isoformat()
        value = {
            "version": self.VERSION,
            "created_at": (existing.get("created_at") if existing is not None else now),
            "updated_at": now,
            **identity.as_dict(),
            "phase": phase,
            "mutation_possible": mutation_possible,
            "result": _json_tree(result) if result is not None else None,
            "reason": reason,
        }
        if self._directory is None:
            key = (identity.controller_id, identity.operation_id)
            if create_only and key in _MEMORY_RECORDS:
                raise FileExistsError("operation receipt already exists")
            _MEMORY_RECORDS[key] = copy.deepcopy(value)
        else:
            self._atomic_write(
                self._path(identity.operation_id), value, create_only=create_only
            )
        return value

    def list_records(
        self, operation_kind: OperationKind | None = None
    ) -> list[dict[str, Any]]:
        """List validated records in only the exact current scope."""

        if self._directory is None:
            values = [
                copy.deepcopy(value)
                for (controller, _), value in _MEMORY_RECORDS.items()
                if controller == self.scope.controller_id
            ]
        else:
            if not self._directory.exists():
                return []
            values = []
            for path in sorted(self._directory.glob("*.json")):
                if path.is_symlink():
                    raise ValueError("operation receipt must not be a symlink")
                value = self.read_by_id(path.stem)
                if value is None:
                    raise ValueError("operation receipt disappeared during inspection")
                values.append(value)
        result = []
        for value in values:
            checked = value
            if (
                operation_kind is None
                or checked.get("operation_kind") == operation_kind
            ):
                result.append(checked)
        return result

    def unresolved_wallet_operation(
        self, *, exclude_operation_id: str | None = None
    ) -> dict[str, Any] | None:
        matches = [
            value
            for value in self.list_records("swap")
            if value.get("operation_id") != exclude_operation_id
            and value.get("phase") in _UNRESOLVED
            and value.get("mutation_possible") is True
        ]
        if len(matches) > 1:
            raise ValueError(
                "multiple unresolved wallet mutations require manual review"
            )
        return matches[0] if matches else None

    def unresolved_operations(self) -> list[dict[str, Any]]:
        """Return all persisted operations that may still have mutated state."""

        return [
            value
            for value in self.list_records()
            if value.get("phase") in _UNRESOLVED
            and value.get("mutation_possible") is True
        ]

    def admit_create(self, identity: OperationIdentity) -> dict[str, Any]:
        """Atomically reserve the controller's one create admission for a tick."""

        self._require_writable()
        if identity.operation_kind != "create":
            raise ValueError("create admission requires a create identity")
        self._admit_once("create", identity.tick, identity.operation_id)
        existing = self.read(identity)
        if existing is not None:
            return existing
        return self.write(
            identity,
            phase="admitted",
            mutation_possible=False,
            create_only=True,
        )

    def admit_preparation(self, identity: OperationIdentity) -> None:
        """Atomically reserve the controller's one inventory preparation per tick."""

        self._require_writable()
        if identity.operation_kind != "swap":
            raise ValueError("preparation admission requires a swap identity")
        self._admit_once("preparation", identity.tick, identity.operation_id)

    def _admit_once(self, kind: str, tick: int, operation_id: str) -> None:
        self._require_writable()
        if self._directory is None:
            key = (self.scope.controller_id, kind, tick)
            existing = _MEMORY_ADMISSIONS.get(key)
            if existing not in (None, operation_id):
                raise ValueError(
                    f"another {kind} operation is already admitted this tick"
                )
            _MEMORY_ADMISSIONS[key] = operation_id
            return
        directory = self._directory / "admissions"
        if directory.exists() and directory.is_symlink():
            raise ValueError("operation admission directory must not be a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f"{kind}_tick_{tick}.json"
        value = {
            "controller_id": self.scope.controller_id,
            "tick": tick,
            "kind": kind,
            "operation_id": operation_id,
        }
        if path.exists():
            if path.is_symlink():
                raise ValueError("operation admission must not be a symlink")
            existing = json.loads(path.read_text())
            if existing != value:
                raise ValueError(
                    f"another {kind} operation is already admitted this tick"
                )
            return
        self._atomic_write(path, value, create_only=True)

    @classmethod
    def _valid(cls, value: dict[str, Any], identity: OperationIdentity) -> bool:
        return (
            value.get("version") == cls.VERSION
            and value.get("phase") in _PHASES
            and isinstance(value.get("mutation_possible"), bool)
            and _record_matches(value, identity)
        )

    @staticmethod
    def _atomic_write(path: Path, value: dict[str, Any], *, create_only: bool) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(value, separators=(",", ":"), sort_keys=True)
            )
            if create_only:
                os.link(temporary, path)
            else:
                temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()


_TOOL_INPUT = re.compile(
    r"###\s+[^\n]*manage_executors[^\n]*\n"
    r"(?:(?!^###\s).)*?"
    r"\*\*Input:\*\*\s*\n```json\s*\n(?P<input>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


def read_prior_tick_stop(
    scope: RuntimeScope, executor_id: str, closed_tick: int
) -> dict[str, Any]:
    """Prove that one earlier completed tick requested one exact stop.

    This reads Condor's platform-generated current-session snapshot, not model
    prose or an Agent report. It proves stop intent only; callers must separately
    prove exact terminal close effects from current executor evidence.
    """

    if (
        scope.execution_mode != "loop"
        or scope.session_dir is None
        or not isinstance(closed_tick, int)
        or isinstance(closed_tick, bool)
        or not 1 <= closed_tick < scope.current_tick
        or not isinstance(executor_id, str)
        or not executor_id.strip()
    ):
        raise ValueError("prior-tick close evidence is unavailable")
    path = scope.session_dir / "snapshots" / f"snapshot_{closed_tick}.md"
    session = scope.session_dir.resolve()
    if (
        not path.is_file()
        or path.is_symlink()
        or not path.resolve().is_relative_to(session)
    ):
        raise ValueError("prior-tick platform snapshot is unavailable or unsafe")
    text = path.read_text()
    matches = []
    for found in _TOOL_INPUT.finditer(text):
        try:
            value = json.loads(found.group("input"))
        except (TypeError, ValueError):
            continue
        if (
            isinstance(value, dict)
            and value.get("action") == "stop"
            and value.get("executor_id") == executor_id
            and value.get("keep_position") is False
            and value.get("controller_id") == scope.controller_id
        ):
            matches.append(value)
    if len(matches) != 1:
        raise ValueError("one exact prior-tick native stop intent was not proven")
    return {
        "tick": closed_tick,
        "source": f"snapshot_{closed_tick}",
        "controller_id": scope.controller_id,
        "executor_id": executor_id,
        "keep_position": False,
    }
