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
    # Session id of the owning run ("{agent_slug}_{N}") — the attribution
    # key for journal/executor accounting. Empty for chat sessions.
    agent_id: str
    # Strategy slug the owning session loops — executor attribution only.
    # Empty for delegations/consults/chat (they run no playbook).
    strategy: str
    active_server: str


def _parse_settings() -> Settings:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--agent-slug", default=None)
    parser.add_argument("--agent-id", default=None)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--bot-token", default=None)
    parser.add_argument("--server-name", default=None)
    args, _ = parser.parse_known_args()

    return Settings(
        chat_id=args.chat_id if args.chat_id is not None else int(os.environ.get("CONDOR_CHAT_ID", "0")),
        user_id=args.user_id if args.user_id is not None else int(os.environ.get("CONDOR_USER_ID", "0")),
        bot_token=args.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        agent_slug=args.agent_slug or os.environ.get("CONDOR_AGENT_SLUG", ""),
        agent_id=args.agent_id or os.environ.get("CONDOR_AGENT_ID", ""),
        strategy=args.strategy or os.environ.get("CONDOR_STRATEGY", ""),
        active_server=args.server_name or os.environ.get("CONDOR_SERVER_NAME", ""),
    )


settings = _parse_settings()


def _config_present() -> bool:
    """ConfigManager creates a default config.yml when none exists — don't let
    an identity probe drop a stray config file in an arbitrary cwd."""
    from pathlib import Path

    return Path("config.yml").exists()


def ensure_identity() -> bool:
    """Tier A identity auto-bind: resolve a missing identity to the sole
    approved user in config.yml.

    Resolution order for identity is: CLI args > env vars > this auto-bind.
    On a single-user install the OS user launching the harness already holds
    every secret on disk (config.yml, JWT signing secret), so binding to the
    one approved user adds convenience without changing the trust model — the
    user id is an identifier here, not a credential. With zero or multiple
    approved users nothing is bound and callers fail fast with instructions
    a multi-user box must say who it is explicitly.

    Returns True when ``settings`` has an identity after the call.
    """
    if settings.user_id:
        return True
    if not _config_present():
        return False
    try:
        from config_manager import get_config_manager

        approved = get_config_manager().get_approved_users()
    except Exception:
        return False
    if len(approved) != 1:
        return False
    settings.user_id = approved[0]
    if not settings.chat_id:
        settings.chat_id = approved[0]
    import logging

    logging.getLogger(__name__).info(
        "No identity configured; auto-bound to sole approved user %s", approved[0]
    )
    return True
