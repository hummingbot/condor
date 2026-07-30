"""Minimal current-session receipt for one Gateway mutation."""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_SLUG = re.compile(r"^[a-z][a-z0-9_-]*$")
SAFE_OPERATION = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
VERSION = 2
LEGACY_VERSION = 1
LEGACY_IDENTITY_KEYS = {
    "controller_id",
    "strategy_slug",
    "operation_id",
    "account_name",
    "network",
    "wallet_address",
    "trading_pair",
    "side",
}
PHASES = {
    "submitting",
    "rejected",
    "confirmed",
    "submitted",
    "uncertain",
    "manual_review",
}


def _identity_matches(
    value: dict[str, Any], identity: dict[str, Any], keys: set[str] | None = None
) -> bool:
    selected = keys or set(identity)
    return all(value.get(key) == identity.get(key) for key in selected)


def _is_legacy(value: Any, identity: dict[str, Any]) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == LEGACY_VERSION
        and value.get("phase") in PHASES
        and _identity_matches(value, identity, LEGACY_IDENTITY_KEYS)
        and all(
            key not in value or value.get(key) == expected
            for key, expected in identity.items()
        )
    )


def _path(
    strategies_dir: Path,
    controller_id: str,
    strategy_slug: str,
    operation_id: str,
    *,
    create: bool,
) -> Path:
    if not SAFE_SLUG.fullmatch(strategy_slug) or not SAFE_OPERATION.fullmatch(
        operation_id
    ):
        raise ValueError("operation receipt identity is invalid")
    match = re.fullmatch(
        rf"lp_expert\.{re.escape(strategy_slug)}_([1-9]\d*)", controller_id
    )
    if match is None:
        raise ValueError("operation receipts require the exact live loop controller")
    root = strategies_dir.resolve()
    session = strategies_dir / strategy_slug / "sessions" / f"session_{match.group(1)}"
    if not session.is_dir():
        raise ValueError("current loop session directory is unavailable")
    session = session.resolve()
    if not session.is_relative_to(root):
        raise ValueError("current loop session escapes the strategy root")
    receipts = session / "gateway_operations"
    if receipts.exists() and receipts.is_symlink():
        raise ValueError("operation receipt directory must not be a symlink")
    if create:
        receipts.mkdir(mode=0o700, exist_ok=True)
    if receipts.exists() and not receipts.resolve().is_relative_to(session):
        raise ValueError("operation receipt directory escapes the current session")
    return receipts / f"{operation_id}.json"


def write(
    strategies_dir: Path,
    identity: dict[str, Any],
    *,
    phase: str,
    mutation_possible: bool,
    receipt: dict[str, Any] | None = None,
    reason: str | None = None,
    create_only: bool = False,
    upgrade_legacy: bool = False,
) -> None:
    if phase not in PHASES:
        raise ValueError("operation receipt phase is invalid")
    path = _path(
        strategies_dir,
        str(identity["controller_id"]),
        str(identity["strategy_slug"]),
        str(identity["operation_id"]),
        create=True,
    )
    now = datetime.now(timezone.utc).isoformat()
    created_at = now
    if path.exists():
        if path.is_symlink():
            raise ValueError("operation receipt must not be a symlink")
        existing = json.loads(path.read_text())
        if not isinstance(existing, dict) or not (
            _identity_matches(existing, identity)
            or (upgrade_legacy and _is_legacy(existing, identity))
        ):
            raise ValueError("operation receipt identity or content is invalid")
        if isinstance(existing.get("created_at"), str):
            created_at = existing["created_at"]
        elif isinstance(existing.get("updated_at"), str):
            created_at = existing["updated_at"]
    value = {
        "version": VERSION,
        "created_at": created_at,
        "updated_at": now,
        **identity,
        "phase": phase,
        "mutation_possible": mutation_possible,
        "receipt": receipt,
        "reason": reason,
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)
        )
        if create_only:
            os.link(temporary, path)
        else:
            temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read(
    strategies_dir: Path,
    identity: dict[str, Any],
    *,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    path = _path(
        strategies_dir,
        str(identity["controller_id"]),
        str(identity["strategy_slug"]),
        str(identity["operation_id"]),
        create=False,
    )
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("operation receipt must not be a symlink")
    value = json.loads(path.read_text())
    valid = (
        isinstance(value, dict)
        and value.get("version") == VERSION
        and value.get("phase") in PHASES
        and _identity_matches(value, identity)
    )
    legacy = allow_legacy and _is_legacy(value, identity)
    if not valid and not legacy:
        raise ValueError("operation receipt identity or content is invalid")
    if legacy:
        value["_legacy_identity"] = True
    return value


def public(value: dict[str, Any]) -> dict[str, Any]:
    """Return only reconciliation fields suitable for a routine result/report."""
    return {
        key: value.get(key)
        for key in (
            "created_at",
            "updated_at",
            "phase",
            "mutation_possible",
            "transaction_hash",
            "receipt",
            "reason",
        )
        if value.get(key) is not None
    }
