"""Regression tests for the hummingbot MCP tool wrappers.

The case here is a shape mismatch against the Hummingbot API that failed silently
rather than loudly, so it survived manual use for a long time: every controller
upload was rejected with 422, because the source string was sent where the API
expects a Controller object.

This file also pinned the backtesting tools, whose bug was the same in kind (a
finished async task rendering as a bare "status=completed"). Those tools are gone
— FEAT-039 made the ``backtest_chart`` routine the only backtesting surface — and
the coverage moved with them, to test_backtest_one_surface.py, which asserts the
stored envelope's shape rather than a formatter's output.

The repo has no async test setup, so the coroutines are driven with
asyncio.run() instead of a pytest-asyncio marker.
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.tools.controllers import modify_controllers


class FakeControllers:
    def __init__(self):
        self.uploaded = None

    async def list_controllers(self):
        return {"directional_trading": []}

    async def create_or_update_controller(
        self, controller_type, controller_name, controller_data
    ):
        self.uploaded = (controller_type, controller_name, controller_data)
        return {"message": "saved"}


class FakeControllerClient:
    def __init__(self):
        self.controllers = FakeControllers()


def test_controller_upload_sends_a_controller_object_not_a_bare_string():
    """POST /controllers/{type}/{name} takes {"content": ...}; a raw string is a 422."""
    client = FakeControllerClient()
    asyncio.run(
        modify_controllers(
            client,
            action="upsert",
            target="controller",
            controller_type="directional_trading",
            controller_name="ema_trend_v1",
            controller_code="class EmaTrendV1Config: pass",
        )
    )

    _, _, body = client.controllers.uploaded
    assert isinstance(body, dict), "the API rejects a bare source string with 422"
    assert body["content"] == "class EmaTrendV1Config: pass"
    assert body["type"] == "directional_trading"


def test_the_backtesting_tools_are_gone():
    """One surface: an agent backtests through the routine, not a second tool."""
    import mcp_servers.hummingbot_api.server as server

    assert not hasattr(server, "run_backtest")
    assert not hasattr(server, "manage_backtest_tasks")

    with pytest.raises(ImportError):
        import mcp_servers.hummingbot_api.tools.backtesting  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
