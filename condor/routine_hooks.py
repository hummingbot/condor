"""Persistent per-routine post-execution hooks.

After a routine finishes, its report can be delivered to extra destinations:
    - Telegram — the raw interactive .html report as a document to arbitrary
      chat ids / groups.

Config is a persistent preset per routine name, stored in
``data/routine_hooks.json``, keyed by the routine name as known to each engine
(global base name, or ``slug/name`` for agent routines). It applies to every
execution of that routine (manual, scheduled, or after restart).

``trigger`` controls when to fire: "success" (default), "always", or "failure".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

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


# ── Dispatch ──


def _should_fire(trigger: str, failed: bool) -> bool:
    if trigger == "always":
        return True
    if trigger == "failure":
        return failed
    # default: success
    return not failed


def _resolve_report_html(report_id: str | None, result) -> tuple[str, str]:
    """Return (html_content, filename) for the report.

    Uses the raw interactive report HTML, falling back to a minimal HTML
    document built from the result text when the routine produced no report.
    """
    if report_id:
        try:
            from condor.reports import get_report_raw_html

            found = get_report_raw_html(report_id)
            if found:
                return found
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not load report %s: %s", report_id, e)

    text = getattr(result, "text", "") or "Completed"
    import html as _html

    body = _html.escape(text)
    minimal = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:-apple-system,sans-serif;background:#0d1117;"
        "color:#e6edf3;padding:24px;}pre{white-space:pre-wrap;}</style></head>"
        f"<body><pre>{body}</pre></body></html>"
    )
    return minimal, "report.html"


async def dispatch(
    routine_name: str,
    result,
    report_id: str | None,
    *,
    failed: bool,
    bot: Any | None,
) -> None:
    """Send the configured Telegram notification for a routine.

    Never raises — failures are logged so a broken notification can't break the
    routine run itself.
    """
    cfg = load_hooks(routine_name)
    if not cfg:
        return

    trigger = cfg.get("trigger", "success")
    if not _should_fire(trigger, failed):
        return

    tg_cfg = cfg.get("telegram") or {}
    if not (tg_cfg.get("enabled") and tg_cfg.get("chat_ids") and bot is not None):
        return

    # Send the raw interactive report HTML (live Plotly charts), or a minimal
    # fallback when the routine produced no report.
    html, filename = _resolve_report_html(report_id, result)

    # Caption = the report's title (falls back to the routine name).
    report_title = None
    if report_id:
        try:
            from condor.reports import get_report

            entry = get_report(report_id)
            if entry:
                report_title = entry.get("title")
        except Exception:  # noqa: BLE001
            pass
    status = "❌ Failed" if failed else "✅ Completed"
    caption = f"{status} — {report_title or routine_name}"

    doc_bytes = html.encode("utf-8")
    for chat_id in tg_cfg["chat_ids"]:
        try:
            import io

            buf = io.BytesIO(doc_bytes)
            buf.name = filename
            await bot.send_document(
                chat_id=int(chat_id),
                document=buf,
                caption=caption[:1024],
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Telegram hook failed for %s -> %s: %s", routine_name, chat_id, e
            )
