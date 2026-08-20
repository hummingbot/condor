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

# ── Run mode (FEAT-049) ──
# Condor runs either as a Telegram bot (the default) or as a local, Telegram-less
# dashboard. The mode is *explicit*: it is read from CONDOR_MODE and is never
# inferred from whether a token happens to be present. An install whose
# TELEGRAM_TOKEN went missing must fail loudly at boot, not quietly become an
# unauthenticated dashboard — local mode has no authentication at all.


class ConfigError(RuntimeError):
    """The process is configured in a way it must not start with."""


MODE_TELEGRAM = "telegram"
MODE_LOCAL = "local"


def resolve_mode(env=None) -> str:
    """The configured run mode: ``"telegram"`` (default) or ``"local"``.

    Only the exact value ``local`` (case- and space-insensitive) selects local
    mode. Anything else — unset, empty, or a typo — is ``telegram``, which is
    the fail-safe direction: telegram mode requires a token, so a mistyped mode
    surfaces as the hard error below instead of as an open dashboard.
    """
    env = os.environ if env is None else env
    value = (env.get("CONDOR_MODE") or "").strip().lower()
    return MODE_LOCAL if value == MODE_LOCAL else MODE_TELEGRAM


def resolve_web_host(env=None) -> str:
    """The address the dashboard binds to.

    Local mode has no login, so it binds loopback only: an unauthenticated
    dashboard with full trading control must not be one firewall rule away from
    the internet. ``WEB_HOST`` is the explicit, documented opt-out (set it to
    ``0.0.0.0`` and you have chosen to expose it). Telegram mode, which does
    authenticate, keeps binding all interfaces.
    """
    env = os.environ if env is None else env
    explicit = (env.get("WEB_HOST") or "").strip()
    if explicit:
        return explicit
    return "127.0.0.1" if resolve_mode(env) == MODE_LOCAL else "0.0.0.0"


def resolve_local_user_id(env=None) -> int:
    """The user id local mode logs in as. ``1`` unless told otherwise.

    Local mode deliberately introduces no new identity concept: ``config.yml``'s
    ``admin_id``, ``server_access.owner_id``, ``chat_defaults``, preferences,
    memory and session keys all key on an integer user id today and keep working
    untouched. ``1`` and not ``0`` because several call sites test a user or chat
    id for truthiness, where a falsy id reads as "absent".
    """
    env = os.environ if env is None else env
    raw = (env.get("CONDOR_LOCAL_USER_ID") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 1


def check_startup_config(env=None) -> None:
    """Refuse to start on a configuration that cannot mean what it says.

    The one case that matters: telegram mode (including the default, i.e. an
    unset ``CONDOR_MODE``) with no token. Today that surfaces deep inside PTB as
    an ``InvalidToken`` traceback; more importantly it must *never* be treated
    as "well, local mode then".
    """
    env = os.environ if env is None else env
    if (
        resolve_mode(env) != MODE_LOCAL
        and not (env.get("TELEGRAM_TOKEN") or "").strip()
    ):
        raise ConfigError(
            "TELEGRAM_TOKEN is not set and CONDOR_MODE is not 'local'.\n"
            "Run `make setup` to configure a Telegram bot, or choose Local mode "
            "there (CONDOR_MODE=local) to run the dashboard without Telegram."
        )


CONDOR_MODE = resolve_mode()
LOCAL_MODE = CONDOR_MODE == MODE_LOCAL
WEB_HOST = resolve_web_host()
LOCAL_USER_ID = resolve_local_user_id()
