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
    parsed = urlparse(_web_url_raw)
    if parsed.scheme and parsed.netloc:
        WEB_URL = _web_url_raw.rstrip("/")
        if _web_port_raw:
            WEB_PORT = int(_web_port_raw)
        elif parsed.port:
            WEB_PORT = parsed.port
        elif parsed.scheme == "https":
            WEB_PORT = 443
        else:
            WEB_PORT = 80
    else:
        WEB_PORT = int(_web_port_raw) if _web_port_raw else 8088
        WEB_URL = f"http://{_web_url_raw.rstrip('/')}"
else:
    WEB_PORT = int(_web_port_raw) if _web_port_raw else 8088
    WEB_URL = f"http://localhost:{WEB_PORT}"
