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

# WEB_URL is the public URL used in links sent to users.
# WEB_PORT is the local bind port for the web server.
#
# Resolution rules:
# 1) If WEB_PORT is set, always use it for binding.
# 2) Else if WEB_URL has an explicit port, use that port.
# 3) Else infer 443 for https:// WEB_URL and 80 for http:// WEB_URL.
# 4) If WEB_URL is not set, default WEB_PORT to 8088 and WEB_URL to localhost.
_web_url_raw = os.environ.get("WEB_URL", "").strip()
_web_port_raw = os.environ.get("WEB_PORT", "").strip()

if _web_url_raw:
    WEB_URL = _web_url_raw.rstrip("/")
    _parsed = urlparse(WEB_URL)
   if _web_port_raw:
       WEB_PORT = int(_web_port_raw)
   else:
       WEB_PORT = _parsed.port or (443 if _parsed.scheme == "https" else 80)
else:
    WEB_PORT = int(_web_port_raw) if _web_port_raw else 8088
    WEB_URL = f"http://localhost:{WEB_PORT}"
