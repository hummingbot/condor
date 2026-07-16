"""Persistent per-routine post-execution hook CONFIG.

Delivery was removed with the Telegram surface (§9.1): nothing dispatches
these hooks anymore. The config store (``data/routine_hooks.json``) and its
load/save API are kept only because the web routines API
(condor/web/routes/routines.py) still exposes GET/PUT endpoints for it;
both die together in the web/settings pass.

``trigger`` is "success" (default), "always", or "failure".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_HOOKS_FILE = Path("data") / "routine_hooks.json"

# Valid trigger conditions.
_TRIGGERS = ("success", "always", "failure")


# ── Persistence ──


def _read_all() -> dict[str, dict]:
    if not _HOOKS_FILE.exists():
        return {}
    try:
        data = json.loads(_HOOKS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read %s; treating as empty", _HOOKS_FILE)
        return {}


def _write_all(data: dict[str, dict]) -> None:
    _HOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(_HOOKS_FILE.parent), suffix=".tmp", delete=False
    )
    try:
        json.dump(data, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, str(_HOOKS_FILE))
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _default_config() -> dict:
    return {
        "telegram": {"enabled": False, "chat_ids": []},
        "trigger": "success",
    }


def load_hooks(routine_name: str) -> dict | None:
    """Return the hook config for a routine, or None if none set."""
    return _read_all().get(routine_name)


def save_hooks(routine_name: str, cfg: dict) -> dict:
    """Validate and persist the hook config for a routine. Returns the stored config."""
    clean = _default_config()

    tg = cfg.get("telegram") or {}
    chat_ids = [str(c).strip() for c in (tg.get("chat_ids") or []) if str(c).strip()]
    # Light validation: chat ids are integers (may be negative for groups).
    chat_ids = [c for c in chat_ids if c.lstrip("-").isdigit()]
    clean["telegram"] = {"enabled": bool(tg.get("enabled")), "chat_ids": chat_ids}

    trigger = cfg.get("trigger")
    clean["trigger"] = trigger if trigger in _TRIGGERS else "success"

    data = _read_all()
    # If nothing is enabled/configured, drop the entry to keep the file tidy.
    if not clean["telegram"]["enabled"] and not chat_ids:
        data.pop(routine_name, None)
    else:
        data[routine_name] = clean
    _write_all(data)
    return clean
