"""resolve_orphan without executor_id must fail loudly, not fall through.

``get_flow_stage()`` originally required ``executor_id`` for the resolve_orphan
action, so a call missing the id silently dispatched to show_schema/list_types:
the agent's recovery request was ignored and it received unrelated executor-type
listings instead of a required-input error. The action now always routes to the
resolve_orphan stage, and the tool returns an explicit error when the id is
missing (the API client is never touched on that path).

The repo has no async test setup, so the coroutine is driven with asyncio.run().
"""
import asyncio

from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
from mcp_servers.hummingbot_api.tools.executors import manage_executors


def test_resolve_orphan_without_id_still_routes_to_resolve_orphan():
    request = ManageExecutorsRequest(action="resolve_orphan")
    assert request.get_flow_stage() == "resolve_orphan"


def test_resolve_orphan_without_id_returns_required_input_error():
    request = ManageExecutorsRequest(action="resolve_orphan")

    result = asyncio.run(manage_executors(client=None, request=request))

    assert result["action"] == "resolve_orphan"
    assert result["error"] == "executor_id is required"
    assert 'action="orphaned"' in result["formatted_output"]
