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

# Hyperliquid account address for direct fill-history PnL/volume queries (see
# handlers/bots/hyperliquid_pnl.py) -- bypasses hummingbot-api's per-bot-instance
# performance bookkeeping, which resets on every redeploy and was found unreliable
# 2026-07-29 (see project memory). Single-account assumption for now.
HYPERLIQUID_ADDRESS = os.environ.get("HYPERLIQUID_ADDRESS", "").strip()

# WEB_URL is the public-facing URL used for generated links (e.g. Telegram login
# links) and CORS — it does NOT determine the local bind port, since behind a
# reverse proxy (e.g. https://example.com) the public port (443) and the local
# uvicorn bind port (e.g. 8088) are different. WEB_PORT controls the bind port
# explicitly; if unset, an explicit port embedded in WEB_URL is used for
# backward compat (e.g. WEB_URL=http://myserver.com:8088 with no proxy);
# otherwise it defaults to 8088.
_web_url_raw = os.environ.get("WEB_URL", "").strip()
_web_port_raw = os.environ.get("WEB_PORT", "").strip()
_web_url_port = urlparse(_web_url_raw).port if _web_url_raw else None

if _web_port_raw:
    WEB_PORT = int(_web_port_raw)
elif _web_url_port:
    WEB_PORT = _web_url_port
else:
    WEB_PORT = 8088

WEB_URL = _web_url_raw.rstrip("/") if _web_url_raw else f"http://localhost:{WEB_PORT}"
