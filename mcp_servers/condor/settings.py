"""CLI args + env vars singleton for the Condor MCP server."""

import argparse
import logging
import os
from dataclasses import dataclass

# Imported for its ``load_dotenv()`` side effect as much as for the helper:
# ``_parse_settings()`` runs at import, before anything else in the process
# pulls this module in, so without it ``.env`` is not yet in ``os.environ`` and
# ADMIN_USER_ID reads as unset (CORR-229).
from utils.config import resolve_admin_id

logger = logging.getLogger("condor.mcp")

#: Default tool profile (FEAT-066). ``full`` on purpose: a launch with no flag —
#: the checked-in ``.mcp.json``, an external MCP host — keeps the whole surface.
DEFAULT_TOOL_PROFILE = "full"


@dataclass
class Settings:
    chat_id: int
    user_id: int
    bot_token: str
    agent_slug: str
    active_server: str
    # Canonical key of the session that spawned this subprocess ("web:7:slot-1",
    # "tg:42", …). Empty when the server runs outside a session.
    session_key: str
    # True when this subprocess is a *background delegation worker* — a detached
    # session `delegate` started to carry one task unattended (FEAT-032). The
    # interactive session and the worker resolve the same agent record, so this
    # flag is the only thing that tells them apart: it selects the unattended
    # framing in ``_build_instructions`` and makes ``delegate(action="start")``
    # refuse to recurse. Every agent has this seat now, not just Condor, since an
    # agent can start a delegation of itself (FEAT-041).
    delegate_worker: bool = False
    # Which slice of the tool surface this process registers (FEAT-066). An ACP
    # bridge runs unrestricted, so for those seats the mounted surface IS the
    # permission model: see ``server.TOOL_PROFILES``. It is a separate flag from
    # the two above rather than derived from them, because the seat it narrows is
    # the *tick*, and neither ``agent_slug`` nor ``delegate_worker`` tells an
    # unattended loop apart from an attended consult of the same specialist.
    tool_profile: str = DEFAULT_TOOL_PROFILE

    @property
    def specialist_slug(self) -> str:
        """``agent_slug`` when a **specialist** is bound, else ``""``.

        Condor is an ordinary agent now (FEAT-033), so the chat's subprocess is
        launched with ``--agent-slug condor`` — correct for scoping its memory
        and skills, and wrong for everything that asks "am I a specialist?".
        Store resolution is unaffected either way (a falsy slug resolves Condor),
        but the isolation rules are not: a specialist sees only its own routines
        and reads its own identity, while the chat owns the general library and
        the coordinator framing. Ask this, not ``agent_slug``, for those.
        """
        from condor.memory.paths import CHAT_SLUG

        return "" if self.agent_slug == CHAT_SLUG else self.agent_slug


def _resolve_user_id(argv_user_id: int | None) -> int:
    """Who this subprocess acts as: argv → ``CONDOR_USER_ID`` → admin → ``0``.

    The spawner always supplies the first two (SEC-180 keeps argv and env
    agreeing on the owner), so the chain only bites for a launch with no
    spawner at all — the checked-in ``.mcp.json``, run from Claude Code or any
    other MCP host. That used to resolve to ``0``, mint a JWT for a user
    ``config.yml`` has never heard of, and turn every main-process tool into an
    opaque ``403 Access denied``. A stdio subprocess started by hand on the
    admin's own machine already has ``.env`` in reach, so treating it as the
    admin is the same trust level, not a wider one (CORR-229).

    A blank, junk or non-positive ``CONDOR_USER_ID`` counts as absent: several
    call sites read a falsy id as "nobody", so honouring a literal ``0`` would
    just reinstate the failure the fallback exists to remove.
    """
    if argv_user_id is not None:
        return argv_user_id

    raw = (os.environ.get("CONDOR_USER_ID") or "").strip()
    try:
        from_env = int(raw) if raw else 0
    except ValueError:
        from_env = 0
    if from_env > 0:
        return from_env

    admin_id = resolve_admin_id()
    if admin_id:
        return admin_id

    logger.warning(
        "Condor MCP server started with no user identity: neither --user-id nor "
        "CONDOR_USER_ID nor ADMIN_USER_ID is set. Every tool that reaches the "
        "main process will fail with 403 Access denied. Set CONDOR_USER_ID (or "
        "ADMIN_USER_ID in .env) to your numeric Telegram user id."
    )
    return 0


def _parse_settings() -> Settings:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--agent-slug", default=None)
    parser.add_argument("--server-name", default=None)
    parser.add_argument("--session-key", default=None)
    parser.add_argument("--delegate-worker", action="store_true", default=False)
    parser.add_argument("--profile", default=DEFAULT_TOOL_PROFILE)
    args, _ = parser.parse_known_args()

    return Settings(
        chat_id=(
            args.chat_id
            if args.chat_id is not None
            else int(os.environ.get("CONDOR_CHAT_ID", "0"))
        ),
        user_id=_resolve_user_id(args.user_id),
        # Env only, never argv: a token on the command line is readable by every
        # local process through `ps` (SEC-095). The spawner injects
        # TELEGRAM_BOT_TOKEN into this subprocess's environment; TELEGRAM_TOKEN
        # is the inherited name, and covers a run started outside a session.
        bot_token=(
            os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
        ),
        agent_slug=args.agent_slug or os.environ.get("CONDOR_AGENT_SLUG", ""),
        active_server=args.server_name or os.environ.get("CONDOR_SERVER_NAME", ""),
        session_key=args.session_key or os.environ.get("CONDOR_SESSION_KEY", ""),
        delegate_worker=(
            args.delegate_worker or os.environ.get("CONDOR_DELEGATE_WORKER", "") == "1"
        ),
        tool_profile=args.profile,
    )


settings = _parse_settings()


def caller_slug(owner: str | None = None) -> str:
    """Who a run started from this subprocess is *for*, as the store stamps it.

    One rule in one place: an explicit ``owner`` — the agent that owns a
    routine the caller targeted — wins, else the specialist bound to this
    subprocess, else the chat. Every ``attribute_to`` the tools put on the wire
    comes from here. ARCH-217 single-sourced the store side that consumes the
    value (``reports.run_scope``); this is the MCP side that supplies it, so a
    change to the rule cannot land in one tool and miss another (ARCH-218).
    """
    from condor.memory.paths import CHAT_SLUG

    return owner or settings.specialist_slug or CHAT_SLUG
