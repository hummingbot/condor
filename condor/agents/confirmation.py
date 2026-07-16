"""Confirmation registry + tool-call summary formatting.

Relocated from ``handlers/agents/confirmation.py`` (simplification plan §5.1):
the transport-agnostic half of the human-approval flow — an in-process
registry of pending confirmation futures plus the human-readable summary a
confirmation prompt renders. A transport (the web chat's WS renderer)
registers itself against this registry and imports the formatter directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Pending confirmation futures: request_id -> Future
_pending: dict[str, asyncio.Future] = {}

CONFIRMATION_TIMEOUT = 120  # seconds

# Injected human-confirmation transport: async (chat_id, tool_call, options) ->
# permission outcome dict. A transport (e.g. the web chat) registers its
# renderer at startup; without a registered transport, human_gate FAILS CLOSED
# (deny_gate) — never auto-approve.
_transport = None


def set_confirmation_transport(fn) -> None:
    """Register the transport ``human_gate`` routes confirmations through."""
    global _transport
    _transport = fn


def get_confirmation_transport():
    return _transport


def register_confirmation(request_id: str) -> asyncio.Future:
    """Create and register the Future a confirmation transport will await."""
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending[request_id] = future
    return future


def discard_confirmation(request_id: str) -> None:
    """Drop a pending confirmation (timeout / cleanup)."""
    _pending.pop(request_id, None)


def resolve_confirmation(request_id: str, approved: bool) -> bool:
    """Called from a UI callback when the user approves/rejects.

    Returns True if the request was found and resolved.
    """
    future = _pending.get(request_id)
    if future and not future.done():
        future.set_result(approved)
        return True
    return False


def _format_tool_summary(tool_call: dict[str, Any]) -> str:
    """Format a tool call into a human-readable summary for the confirmation message."""
    tool_name = tool_call.get("tool", "") or tool_call.get("title", "Unknown")
    input_data = tool_call.get("input", {})

    if tool_name == "manage_executors":
        action = input_data.get("action", "?")
        exec_type = input_data.get("executor_type", "")
        exec_id = input_data.get("executor_id", "")
        if action == "create" and exec_type:
            # Condor-native create: the instrument lives in the type's config
            # (trading_pair for spot, coin for perp, market for pred).
            config = input_data.get("config", {}) or {}
            pair = (
                config.get("trading_pair")
                or config.get("coin")
                or config.get("market")
                or config.get("base_token")
                or "?"
            )
            return f"Create {exec_type} on {pair}"
        if action == "stop" and exec_id:
            return f"Stop executor {exec_id[:12]}..."
        return f"Executor: {action}"

    # Generic fallback
    return tool_name
