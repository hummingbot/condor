"""CLI args + env vars singleton for the Condor MCP server."""

import argparse
import os
from dataclasses import dataclass


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


def _parse_settings() -> Settings:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--agent-slug", default=None)
    parser.add_argument("--server-name", default=None)
    parser.add_argument("--session-key", default=None)
    parser.add_argument("--delegate-worker", action="store_true", default=False)
    args, _ = parser.parse_known_args()

    return Settings(
        chat_id=(
            args.chat_id
            if args.chat_id is not None
            else int(os.environ.get("CONDOR_CHAT_ID", "0"))
        ),
        user_id=(
            args.user_id
            if args.user_id is not None
            else int(os.environ.get("CONDOR_USER_ID", "0"))
        ),
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
    )


settings = _parse_settings()
