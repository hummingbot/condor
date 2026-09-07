import os
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """The process is configured in a way it must not start with."""


def resolve_admin_id(env=None) -> Optional[int]:
    """The primary admin's user id, or ``None`` when it is unset or unusable.

    Junk resolves to ``None`` rather than raising here, because this runs at
    import time and every module reads this file; the refusal happens once, at
    boot, in :func:`check_startup_config`. Non-positive ids count as unusable:
    several call sites read a falsy user or chat id as "absent", so ``0`` would
    silently address nobody.
    """
    env = os.environ if env is None else env
    raw = (env.get("ADMIN_USER_ID") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# Primary admin user ID - this user has full control over the bot
# Set via ADMIN_USER_ID environment variable
ADMIN_USER_ID = resolve_admin_id()

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
# An operator override, in both directions. An unset value means "no override",
# and the stored consent decides — whose default, with no answer given, is
# `ping`: the four adoption events and nothing else, from the first boot. The
# admin prompt chooses between `ping` and `usage`. An install whose admin
# *refused* (stored consent `denied`, from Settings -> Privacy or from an older
# build's "No thanks") stays at `off` across upgrades; setting this to
# `ping`/`usage` overrides that refusal too.
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


def resolve_use_tailscale(env=None) -> bool:
    """Whether the dashboard is meant to be reached over a Tailscale tailnet.

    Set by ``setup-environment.sh`` when the user opts in. Another reason (next
    to local mode) :func:`resolve_web_host` binds loopback: ``tailscale serve``
    (``utils/tailscale.py``) is what actually exposes the dashboard when this is
    on, only on the tailnet.
    """
    env = os.environ if env is None else env
    return (env.get("USE_TAILSCALE") or "").strip().lower() in ("true", "1", "yes")


def resolve_web_host(env=None) -> str:
    """The address the dashboard binds to.

    Three deployments, three answers:

    * **Local mode** — ``127.0.0.1``, always, Tailscale or not. This dashboard
      has no login at all, so it stays on the loopback interface and you browse
      it at ``localhost:8088``. Tailscale does not change that: a tailnet is a
      smaller audience than the internet, not an authenticated one, and
      "everyone on the tailnet" is still more than "nobody".
    * **Telegram mode, no Tailscale** — ``0.0.0.0``, so ``VPS_IP:8088`` works.
      This mode authenticates, which is what makes a public bind defensible.
    * **Telegram mode, Tailscale on** — this node's own tailnet address. The
      dashboard is reachable at ``<tailnet-ip>:8088`` from anywhere on the
      tailnet and nowhere else, because a public interface is never bound.

    Binding the tailnet address directly replaced a ``127.0.0.1`` bind plus
    ``tailscale serve``. Serve needs the daemon socket, which refuses
    unprivileged callers, so the proxy silently never came up and the
    dashboard was reachable from nowhere. A bind needs no privilege and no
    daemon round-trip.

    Fails **closed**: if the tailnet address cannot be determined (daemon not
    up yet, node not registered) this returns loopback, never ``0.0.0.0``.
    Falling back to a public bind would invert the guarantee the setting
    exists to provide.

    ``WEB_HOST`` remains the explicit, documented opt-out and wins over all of
    it -- set it to ``0.0.0.0`` and you have chosen to expose the dashboard.
    """
    env = os.environ if env is None else env
    explicit = (env.get("WEB_HOST") or "").strip()
    if explicit:
        return explicit
    # Local mode is checked BEFORE Tailscale, deliberately: an unauthenticated
    # dashboard does not go on the tailnet just because a tailnet exists.
    if resolve_mode(env) == MODE_LOCAL:
        return "127.0.0.1"
    if resolve_use_tailscale(env):
        from utils.tailscale import tailnet_ip

        return tailnet_ip() or "127.0.0.1"
    return "0.0.0.0"


def resolve_local_user_id(env=None) -> int:
    """The user id local mode logs in as: the admin, or ``1`` when there is none.

    Local mode deliberately introduces no new identity concept — not even its
    own. It logs in as ``ADMIN_USER_ID``, the same id ``config.yml``'s
    ``admin_id``, ``server_access.owner_id``, ``chat_defaults``, preferences,
    memory and session keys already key on, and the one ``ConfigManager``
    materialises a user record for. Two knobs for one identity is what broke:
    a Telegram install that flipped ``CONDOR_MODE=local`` kept its Telegram
    ``ADMIN_USER_ID`` while the dashboard tried to log in as a hardcoded ``1``
    that ``config.yml`` had never heard of.

    ``1`` remains the answer for an install that never had a Telegram id — it is
    what ``make setup`` writes as ``ADMIN_USER_ID`` in local mode — and ``1``
    rather than ``0`` because several call sites read a falsy id as "absent".
    """
    return resolve_admin_id(env) or 1


def check_startup_config(env=None) -> None:
    """Refuse to start on a configuration that cannot mean what it says.

    Two cases, both of which used to surface far from their cause:

    * Telegram mode (including the default, i.e. an unset ``CONDOR_MODE``) with
      no token. That surfaced deep inside PTB as an ``InvalidToken`` traceback;
      more importantly it must *never* be treated as "well, local mode then".
    * An ``ADMIN_USER_ID`` that is not a positive integer. That used to be
      swallowed silently, leaving an install with **no admin at all**: no admin
      panel, no approvals, no boot notification, and in local mode no user to
      log in as — all of it looking like unrelated breakage later.
    """
    env = os.environ if env is None else env

    raw_admin = (env.get("ADMIN_USER_ID") or "").strip()
    if raw_admin and resolve_admin_id(env) is None:
        raise ConfigError(
            f"ADMIN_USER_ID is not a valid user id: {raw_admin!r}.\n"
            "It must be a positive integer — your numeric Telegram user id "
            "(get it from https://t.me/userinfobot), or 1 in local mode.\n"
            "Fix it in .env, or run `make setup`."
        )

    if (
        resolve_mode(env) != MODE_LOCAL
        and not (env.get("TELEGRAM_TOKEN") or "").strip()
    ):
        raise ConfigError(
            "TELEGRAM_TOKEN is not set and CONDOR_MODE is not 'local'.\n"
            "Run `make setup` to configure a Telegram bot, or choose Local mode "
            "there (CONDOR_MODE=local) to run the dashboard without Telegram."
        )


def check_local_user(env=None, get_role=None) -> None:
    """In local mode, refuse to start unless the user it logs in as exists.

    Local mode logs in without a password, so the *only* thing that decides who
    that is happens before any request: ``ADMIN_USER_ID`` must resolve to a
    ``config.yml`` user with a role that can use the dashboard. When it does
    not, the failure belongs here — at boot, in ``make run``, next to the file
    you have to edit — and not as a 500 from ``/auth/local-login`` once the
    browser is already open.

    ``get_role`` is injectable so this is testable without a config file; it
    defaults to the real :class:`ConfigManager`, whose load also materialises
    the admin's user record from the environment.
    """
    env = os.environ if env is None else env
    if resolve_mode(env) != MODE_LOCAL:
        return

    user_id = resolve_local_user_id(env)
    if get_role is None:
        from config_manager import get_config_manager

        get_role = get_config_manager().get_user_role

    from config_manager import UserRole

    if get_role(user_id) not in (UserRole.USER, UserRole.ADMIN):
        raw_admin = (env.get("ADMIN_USER_ID") or "").strip()
        source = (
            f"ADMIN_USER_ID={raw_admin} in .env"
            if raw_admin
            else "the local-mode default (ADMIN_USER_ID is not set in .env)"
        )
        raise ConfigError(
            f"Local mode logs in as user {user_id} — {source} — but that user "
            f"is not an approved user in config.yml.\n"
            "Run `make setup` and choose Local mode, or set ADMIN_USER_ID in "
            ".env to a user that exists in config.yml.\n"
            "Switching an existing Telegram install to local mode: keep your "
            "Telegram ADMIN_USER_ID and the dashboard logs in as you, with all "
            "your servers and preferences intact."
        )


CONDOR_MODE = resolve_mode()
LOCAL_MODE = CONDOR_MODE == MODE_LOCAL
USE_TAILSCALE = resolve_use_tailscale()
WEB_HOST = resolve_web_host()
LOCAL_USER_ID = resolve_local_user_id()
