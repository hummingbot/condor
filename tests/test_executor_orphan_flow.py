"""Regression tests for the orphaned-position recovery tools.

An orphan recovery request that goes unanswered is worse than one that errors:
the agent is told about a stranded on-chain position, asks to resolve it, and the
request is quietly answered with something else. That is what happened under the
old mega-tool — ``resolve_orphan`` without an ``executor_id`` fell through
``get_flow_stage()`` to show_schema/list_types, so the caller got a list of
executor types back and the position stayed flagged.

The typed split (FEAT-062) removes the failure mode rather than handling it:
``resolve_orphaned_position`` takes ``executor_id`` as a REQUIRED parameter, so a
call missing it is rejected by the MCP host before the server is reached. There is
no other branch for it to land on, because there is no dispatch left. These tests
pin that: the id is structurally required, and the two recovery tools are separate
names that cannot be confused for each other.

The repo has no async test setup, so the coroutine is driven with asyncio.run()
instead of a pytest-asyncio marker.
"""

import asyncio
import inspect

import pytest

from mcp_servers.hummingbot_api import server
from mcp_servers.hummingbot_api.tools.executors import (
    list_orphaned_positions,
    resolve_orphaned_position,
)


def test_resolving_an_orphan_requires_the_executor_id():
    """The id has no default, so the host rejects a call without one."""
    param = inspect.signature(server.resolve_orphaned_position).parameters[
        "executor_id"
    ]

    assert param.default is inspect.Parameter.empty
    assert param.annotation is str


def test_listing_orphans_takes_no_arguments():
    """Nothing to get wrong: the listing cannot be diverted by a stray argument."""
    assert not inspect.signature(server.list_orphaned_positions).parameters


def test_the_two_recovery_steps_are_separate_tools():
    """Listing candidates and marking one recovered can no longer share a name."""
    assert list_orphaned_positions is not resolve_orphaned_position
    assert not hasattr(server, "manage_executors")


def test_resolve_reports_the_executor_it_could_not_resolve():
    """A transport failure names the executor, so the agent can retry the right one."""

    class _Boom:
        class executors:
            base_url = "http://api"

            class session:
                @staticmethod
                async def post(url):
                    raise RuntimeError("connection refused")

    result = asyncio.run(resolve_orphaned_position(_Boom(), executor_id="abc123"))

    assert result["action"] == "resolve_orphan"
    assert "abc123" in result["error"] or "abc123" in result["formatted_output"]
    assert "connection refused" in result["formatted_output"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
