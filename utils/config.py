import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

# Primary admin user ID - this user has full control over the bot
# Set via ADMIN_USER_ID environment variable
ADMIN_USER_ID = None
_admin_id_str = os.environ.get("ADMIN_USER_ID", "").strip()
if _admin_id_str:
    try:
        ADMIN_USER_ID = int(_admin_id_str)
    except ValueError:
        pass

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Single WEB_URL param: full URL including port if needed (e.g. http://myserver.com:8088)
# Falls back to WEB_PORT for backward compat, then default 8088
_web_url_raw = os.environ.get("WEB_URL", "").strip()
_web_port_raw = os.environ.get("WEB_PORT", "").strip()

if _web_url_raw:
    WEB_URL = _web_url_raw.rstrip("/")
    _parsed = urlparse(WEB_URL)
    WEB_PORT = _parsed.port or (443 if _parsed.scheme == "https" else 80)
else:
    WEB_PORT = int(_web_port_raw) if _web_port_raw else 8088
    WEB_URL = f"http://localhost:{WEB_PORT}"

# ── Telemetry (FEAT-023) ──
# Opt-in and OFF by default. Nothing is collected, buffered or sent unless the
# install's admin has explicitly consented, or an operator sets CONDOR_TELEMETRY
# here. An unset value is *not* "on": it means "no override", and the stored
# consent decides — whose default is `unknown`, which emits nothing.
#   off   - nothing, ever. emit() returns immediately.
#   ping  - install / heartbeat / version_change / shutdown only.
#   usage - the full allowlisted taxonomy in condor/telemetry/schema.py.
CONDOR_TELEMETRY = os.environ.get("CONDOR_TELEMETRY", "").strip().lower() or None

# Where a batch would be POSTed. Deliberately unset by default and NOT baked
# into the source: with no URL the send path is inert, and events can only ever
# accumulate in the local capped outbox. See PRIVACY.md.
CONDOR_TELEMETRY_URL = os.environ.get("CONDOR_TELEMETRY_URL", "").strip() or None
