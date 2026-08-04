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
    parser.add_argument("--bot-token", default=None)
    parser.add_argument("--server-name", default=None)
    parser.add_argument("--session-key", default=None)
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
        bot_token=args.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        agent_slug=args.agent_slug or os.environ.get("CONDOR_AGENT_SLUG", ""),
        active_server=args.server_name or os.environ.get("CONDOR_SERVER_NAME", ""),
        session_key=args.session_key or os.environ.get("CONDOR_SESSION_KEY", ""),
    )


settings = _parse_settings()
