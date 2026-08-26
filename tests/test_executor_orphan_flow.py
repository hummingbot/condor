"""Regression tests for the orphaned-position recovery actions.

An orphan recovery request that goes unanswered is worse than one that errors:
the agent is told about a stranded on-chain position, asks to resolve it, and
`resolve_orphan` without an executor_id used to fall through get_flow_stage() to
show_schema/list_types — so the caller got a list of executor types back and the
position stayed flagged. These tests pin the routing and the required-input error.

The repo has no async test setup, so the coroutine is driven with asyncio.run()
instead of a pytest-asyncio marker.
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
from mcp_servers.hummingbot_api.tools.executors import manage_executors


def test_resolve_orphan_without_id_routes_to_resolve_orphan():
    """A missing executor_id must not silently reroute to executor discovery."""
    request = ManageExecutorsRequest(action="resolve_orphan")

    assert request.get_flow_stage() == "resolve_orphan"


def test_resolve_orphan_without_id_reports_the_missing_input():
    """The caller gets an actionable error, not unrelated informational output."""
    request = ManageExecutorsRequest(action="resolve_orphan")

    result = asyncio.run(manage_executors(client=None, request=request))

    assert result["action"] == "resolve_orphan"
    assert "executor_id" in result["error"]
    assert "orphaned" in result["formatted_output"]


def test_resolve_orphan_without_id_ignores_executor_type():
    """executor_type present must not divert the recovery request to show_schema."""
    request = ManageExecutorsRequest(
        action="resolve_orphan", executor_type="lp_executor"
    )

    assert request.get_flow_stage() == "resolve_orphan"


def test_resolve_orphan_with_id_still_routes_to_the_api_call():
    request = ManageExecutorsRequest(action="resolve_orphan", executor_id="abc123")

    assert request.get_flow_stage() == "resolve_orphan"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
