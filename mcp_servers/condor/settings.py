"""CLI args + env vars singleton for the Condor MCP server."""

import argparse
import os
from dataclasses import dataclass


@dataclass
class Settings:
    agent_slug: str
    # Session id of the owning run ("{agent_slug}_{N}") — the attribution
    # key for journal/executor accounting. Empty for chat sessions.
    agent_id: str
    # Opaque run-capability id (§6.2) injected by the main process at run();
    # the sole execution authority for agent-run creates. Empty for chat
    # sessions (which register condor-direct instead).
    capability: str


def _parse_settings() -> Settings:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--agent-slug", default=None)
    parser.add_argument("--agent-id", default=None)
    parser.add_argument("--capability", default=None)
    args, _ = parser.parse_known_args()

    return Settings(
        agent_slug=args.agent_slug or os.environ.get("CONDOR_AGENT_SLUG", ""),
        agent_id=args.agent_id or os.environ.get("CONDOR_AGENT_ID", ""),
        capability=args.capability or os.environ.get("CONDOR_RUN_CAPABILITY", ""),
    )


settings = _parse_settings()
