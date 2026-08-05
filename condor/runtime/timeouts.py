"""Every deadline in the runtime, in one place.

The constants used to be scattered: the prompt lock and overall budget in the
session module, the confirmation TTL in the chat WebSocket route, tick budgets
in the engine, call timeouts in the MCP client. Changing "how long may a prompt
run" meant finding all of them and hoping you had.

Each field can be overridden per deployment with ``CONDOR_TIMEOUT_<FIELD>``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields

log = logging.getLogger(__name__)

ENV_PREFIX = "CONDOR_TIMEOUT_"


@dataclass(frozen=True)
class TimeoutPolicy:
    """Deadlines, in seconds."""

    # How long to wait for a session's prompt lock before giving up. Guards
    # against a stuck previous prompt blocking the chat forever.
    prompt_lock: int = 30
    # How long a deliberately queued message waits for its turn. Longer than
    # prompt_lock, which guards against a *stuck* prompt rather than a busy
    # one: a message waiting behind a five-minute answer is not a fault.
    prompt_queue: int = 900
    # Wall-clock budget for a single prompt. Kills runaway agent turns.
    prompt_overall: int = 1800
    # How long to wait for an agent to confirm session/cancel before falling
    # back to cancelling the request locally.
    prompt_cancel: int = 2
    # How long a human has to answer a dangerous-tool confirmation.
    confirmation: int = 120
    # Keepalive cadence on an SSE stream, so proxies and clients see traffic.
    sse_heartbeat: float = 10.0
    # Upper bound on a single SSE prompt stream.
    sse_stream: int = 1800
    # Budget for one MCP tool call.
    mcp_call: float = 15.0
    # Default budget for one strategy tick.
    tick_default: int = 600

    @classmethod
    def load(cls) -> "TimeoutPolicy":
        """Build the policy, applying CONDOR_TIMEOUT_* overrides.

        An unparseable override is logged and ignored rather than crashing the
        process at import time — a typo in an env var must not stop the bot.
        """
        overrides: dict[str, float] = {}
        for f in fields(cls):
            raw = os.environ.get(f"{ENV_PREFIX}{f.name.upper()}")
            if raw is None:
                continue
            try:
                overrides[f.name] = int(raw) if f.type == "int" else float(raw)
            except ValueError:
                log.warning(
                    "Ignoring %s%s=%r: not a number",
                    ENV_PREFIX,
                    f.name.upper(),
                    raw,
                )
        return cls(**overrides)


TIMEOUTS = TimeoutPolicy.load()


def resolve_tick_timeout(
    execution_mode: str = "loop",
    caller: int | None = None,
    strategy: int | None = None,
) -> int:
    """Pick a tick budget: explicit caller override > strategy config > default.

    A single-tick run (dry run / run once) is a human waiting on a result, so it
    gets the shorter default; a loop tick is unattended and may take longer.
    """
    if caller:
        return int(caller)
    if strategy:
        return int(strategy)
    if execution_mode in ("dry_run", "run_once"):
        return TIMEOUTS.tick_default
    return TIMEOUTS.tick_default
