"""Marking an orphan recovered must reach the API, and say so when it cannot.

``get_flow_stage()`` originally required ``executor_id`` for the resolve_orphan
action, so a call missing the id silently dispatched to show_schema/list_types:
the agent's recovery request was ignored and it received unrelated executor-type
listings instead of a required-input error. That dispatch is gone — the id is a
required parameter of ``resolve_orphaned_position`` now (see
tests/test_executor_orphan_flow.py) — so what is left to pin is the call itself:
it must POST to the resolve-orphan endpoint for the executor it was given, and
recovery must not be reported when the POST failed.

The repo has no async test setup, so the coroutine is driven with asyncio.run().
"""

import asyncio

from mcp_servers.hummingbot_api.tools.executors import resolve_orphaned_position


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class _Client:
    """Records the URL the tool posted to."""

    def __init__(self, payload=None):
        posted: list[str] = []
        self.posted = posted

        class _Session:
            @staticmethod
            async def post(url):
                posted.append(url)
                return _Response(payload or {"status": "resolved"})

        class _Executors:
            base_url = "http://api"
            session = _Session()

        self.executors = _Executors()


def test_resolve_posts_to_the_endpoint_for_that_executor():
    client = _Client()

    result = asyncio.run(resolve_orphaned_position(client, executor_id="abc123"))

    assert client.posted == ["http://api/executors/abc123/resolve-orphan"]
    assert result["action"] == "resolve_orphan"
    assert result["executor_id"] == "abc123"
    assert "marked recovered" in result["formatted_output"]


def test_a_failed_resolve_is_not_reported_as_recovered():
    """The orphan is still stranded; saying otherwise would end the recovery."""

    class _Failing(_Client):
        def __init__(self):
            super().__init__()

            class _Session:
                @staticmethod
                async def post(url):
                    raise RuntimeError("502 Bad Gateway")

            self.executors.session = _Session()

    result = asyncio.run(resolve_orphaned_position(_Failing(), executor_id="abc123"))

    assert "error" in result
    assert "marked recovered" not in result["formatted_output"]
    assert "502 Bad Gateway" in result["formatted_output"]
