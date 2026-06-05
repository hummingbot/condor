"""SMTP email sender for routine report notifications.

Credentials come from environment variables (no UI editing):
    SMTP_HOST       - SMTP server host (required to enable email)
    SMTP_PORT       - SMTP server port (default 587)
    SMTP_USER       - SMTP username / login
    SMTP_PASSWORD   - SMTP password
    SMTP_FROM       - From address (default: SMTP_USER)
    SMTP_USE_TLS    - "true"/"false", STARTTLS (default true)

Uses the stdlib smtplib in a worker thread so it doesn't block the event loop.
No new dependencies.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

_DATA_URI_RE = re.compile(
    r'src="data:image/(?P<subtype>[a-zA-Z0-9.+-]+);base64,(?P<data>[A-Za-z0-9+/=]+)"'
)


def smtp_configured() -> bool:
    """True if the minimum SMTP env vars are present to send mail."""
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER"))


def _extract_inline_images(html: str) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Replace base64 `data:` image URIs with `cid:` references.

    Returns the rewritten HTML and a list of (cid, raw_bytes, subtype) so the
    caller can attach each image as an inline related part.
    """
    images: list[tuple[str, bytes, str]] = []

    def _repl(m: re.Match) -> str:
        try:
            data = base64.b64decode(m.group("data"))
        except (binascii.Error, ValueError):
            return m.group(0)  # leave malformed data URI untouched
        cid = f"img{len(images)}@condor"
        images.append((cid, data, m.group("subtype")))
        return f'src="cid:{cid}"'

    return _DATA_URI_RE.sub(_repl, html), images


def _send_blocking(
    recipients: list[str],
    subject: str,
    html_body: str,
    attachment_name: str | None,
    attachment_bytes: bytes | None,
) -> None:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM") or user
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() not in ("false", "0", "no")

    # Pull base64 chart images out of the HTML and reference them as inline
    # CID attachments — email clients (Gmail/Outlook/Apple Mail) render these
    # reliably, whereas inline `data:` URIs are frequently stripped.
    html_for_body, images = _extract_inline_images(html_body)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("This report is best viewed as HTML.")
    msg.add_alternative(html_for_body, subtype="html")

    html_part = msg.get_payload()[-1]
    for cid, data, subtype in images:
        html_part.add_related(data, maintype="image", subtype=subtype, cid=f"<{cid}>")

    if attachment_bytes and attachment_name:
        msg.add_attachment(
            attachment_bytes,
            maintype="text",
            subtype="html",
            filename=attachment_name,
        )

    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)


async def send_report(
    recipients: list[str],
    subject: str,
    html_body: str,
    attachment_name: str | None = None,
    attachment_bytes: bytes | None = None,
) -> bool:
    """Send an HTML report email. Returns True on success, False otherwise.

    Never raises — failures are logged so a broken email channel can't break
    routine execution.
    """
    if not smtp_configured():
        logger.warning("SMTP not configured; skipping email to %s", recipients)
        return False
    if not recipients:
        return False
    try:
        await asyncio.to_thread(
            _send_blocking,
            recipients,
            subject,
            html_body,
            attachment_name,
            attachment_bytes,
        )
        logger.info("Sent report email to %s", recipients)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to send report email to %s: %s", recipients, e)
        return False
