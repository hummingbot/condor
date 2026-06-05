"""Persistent per-routine post-execution hooks.

After a routine finishes, its report can be distributed to extra destinations:
    - Email (SMTP) — the full HTML report inline + attached.
    - Telegram — the .html report as a document to arbitrary chat ids / groups.

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
        "email": {"enabled": False, "recipients": []},
        "telegram": {"enabled": False, "chat_ids": []},
        "trigger": "success",
    }


def load_hooks(routine_name: str) -> dict | None:
    """Return the hook config for a routine, or None if none set."""
    return _read_all().get(routine_name)


def save_hooks(routine_name: str, cfg: dict) -> dict:
    """Validate and persist the hook config for a routine. Returns the stored config."""
    clean = _default_config()

    email = cfg.get("email") or {}
    recipients = [
        str(e).strip() for e in (email.get("recipients") or []) if str(e).strip()
    ]
    # Light validation: keep entries that look like an email address.
    recipients = [e for e in recipients if "@" in e and "." in e.split("@")[-1]]
    clean["email"] = {"enabled": bool(email.get("enabled")), "recipients": recipients}

    tg = cfg.get("telegram") or {}
    chat_ids = [str(c).strip() for c in (tg.get("chat_ids") or []) if str(c).strip()]
    # Light validation: chat ids are integers (may be negative for groups).
    chat_ids = [c for c in chat_ids if c.lstrip("-").isdigit()]
    clean["telegram"] = {"enabled": bool(tg.get("enabled")), "chat_ids": chat_ids}

    trigger = cfg.get("trigger")
    clean["trigger"] = trigger if trigger in _TRIGGERS else "success"

    data = _read_all()
    # If everything is empty/disabled, drop the entry to keep the file tidy.
    if (
        not clean["email"]["enabled"]
        and not clean["telegram"]["enabled"]
        and not recipients
        and not chat_ids
    ):
        data.pop(routine_name, None)
    else:
        data[routine_name] = clean
    _write_all(data)
    return clean


def smtp_configured() -> bool:
    from utils.email_sender import smtp_configured as _sc

    return _sc()


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

    Falls back to a minimal HTML document built from the result text when the
    routine produced no report.
    """
    if report_id:
        try:
            from condor.reports import get_report_html_for_email

            found = get_report_html_for_email(report_id)
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
    """Fire the configured post-execution hooks for a routine.

    Never raises — each channel is isolated so one failure can't break the
    other channel or the routine run itself.
    """
    cfg = load_hooks(routine_name)
    if not cfg:
        return

    trigger = cfg.get("trigger", "success")
    if not _should_fire(trigger, failed):
        return

    html_content, filename = _resolve_report_html(report_id, result)
    summary = (getattr(result, "text", "") or "Completed")[:300]
    status = "❌ Failed" if failed else "✅ Completed"
    caption = f"{status} — {routine_name}\n\n{summary}"

    # ── Email ──
    # The full report is sent inline in the body; charts travel as inline PNG
    # images (CID), so they render directly in the email client.
    email_cfg = cfg.get("email") or {}
    if email_cfg.get("enabled") and email_cfg.get("recipients"):
        try:
            from utils.email_sender import send_report

            subject = f"[Condor] {routine_name} — {'failed' if failed else 'report'}"
            # Body renders inline (charts as CID images); the same report is also
            # attached as a self-contained .html (charts as data: URIs) so it can
            # be saved/opened standalone in a browser.
            await send_report(
                recipients=list(email_cfg["recipients"]),
                subject=subject,
                html_body=html_content,
                attachment_name=filename,
                attachment_bytes=html_content.encode("utf-8"),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Email hook failed for %s: %s", routine_name, e)

    # ── Telegram ──
    tg_cfg = cfg.get("telegram") or {}
    if tg_cfg.get("enabled") and tg_cfg.get("chat_ids") and bot is not None:
        doc_bytes = html_content.encode("utf-8")
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
