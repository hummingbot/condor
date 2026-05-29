"""Cursor model catalog fetcher.

Runs `node list_models.mjs --json` under condor/acp/cursor_bridge/ using
CURSOR_API_KEY. Results are cached for an hour so paginating the picker
doesn't refetch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600
_BRIDGE_DIR = Path(__file__).resolve().parent.parent.parent / "condor" / "acp" / "cursor_bridge"
_LIST_SCRIPT = _BRIDGE_DIR / "list_models.mjs"


@dataclass(frozen=True)
class CursorModel:
    id: str
    name: str
    description: str = ""


_cache: tuple[float, list[CursorModel]] | None = None


def _parse_models(raw: object) -> list[CursorModel]:
    if not isinstance(raw, list):
        return []

    seen: set[str] = set()
    models: list[CursorModel] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            CursorModel(
                id=model_id,
                name=str(entry.get("displayName") or model_id),
                description=str(entry.get("description") or ""),
            )
        )

    models.sort(key=lambda m: (m.name.lower(), m.id.lower()))
    return models


async def fetch_models(force_refresh: bool = False) -> list[CursorModel]:
    """Return the list of Cursor models from the SDK catalog."""
    global _cache

    if not os.environ.get("CURSOR_API_KEY"):
        return []

    if not force_refresh and _cache is not None:
        cached_at, models = _cache
        if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
            return models

    if not _LIST_SCRIPT.is_file():
        log.warning("Cursor list_models script missing at %s", _LIST_SCRIPT)
        if _cache is not None:
            return _cache[1]
        return []

    node_bin = os.environ.get("CONDOR_NODE_BIN", "node")
    env = dict(os.environ)

    try:
        proc = await asyncio.create_subprocess_exec(
            node_bin,
            str(_LIST_SCRIPT),
            "--json",
            cwd=str(_BRIDGE_DIR),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        log.warning("Failed to fetch Cursor models: %s", e)
        if _cache is not None:
            return _cache[1]
        return []

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        log.warning("Cursor list_models failed (rc=%s): %s", proc.returncode, err)
        if _cache is not None:
            return _cache[1]
        return []

    try:
        payload = json.loads(stdout.decode())
    except json.JSONDecodeError as e:
        log.warning("Invalid JSON from Cursor list_models: %s", e)
        if _cache is not None:
            return _cache[1]
        return []

    models = _parse_models(payload)
    _cache = (time.monotonic(), models)
    log.info("Fetched %d Cursor models", len(models))
    return models


def format_button_label(model: CursorModel) -> str:
    """Short label for inline keyboard buttons."""
    label = model.name or model.id
    if len(label) > 38:
        label = label[:35] + "..."
    return label


def find_model_by_id(models: list[CursorModel], model_id: str) -> CursorModel | None:
    """Case-insensitive exact-match lookup by model id."""
    target = model_id.strip().lower()
    if not target:
        return None
    for m in models:
        if m.id.lower() == target:
            return m
    return None
