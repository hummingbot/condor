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


def _parse_settings() -> Settings:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--agent-slug", default=None)
    parser.add_argument("--bot-token", default=None)
    parser.add_argument("--server-name", default=None)
    args, _ = parser.parse_known_args()

    return Settings(
        chat_id=args.chat_id if args.chat_id is not None else int(os.environ.get("CONDOR_CHAT_ID", "0")),
        user_id=args.user_id if args.user_id is not None else int(os.environ.get("CONDOR_USER_ID", "0")),
        bot_token=args.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        agent_slug=args.agent_slug or os.environ.get("CONDOR_AGENT_SLUG", ""),
        active_server=args.server_name or os.environ.get("CONDOR_SERVER_NAME", ""),
    )


settings = _parse_settings()
