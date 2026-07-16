import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

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
