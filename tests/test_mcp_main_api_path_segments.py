"""An id from a tool argument is one path segment, or the call is refused.

``manage_agents`` / ``manage_loops`` delete goes to the main process as
``DELETE /agents/{slug}``. The slug was interpolated raw, and the HTTP client
collapses dot segments before sending, so ``../settings/servers/x`` deleted a
server through what read as an agent delete (PR #344 review, finding 1).
"""

import asyncio

import pytest

from mcp_servers.condor import condor_client
from mcp_servers.condor.condor_client import call_main_api, path_segment
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.tools import trading_agent


@pytest.fixture
def sent(monkeypatch):
    """Every main-API call the tool module makes, instead of making it."""
    calls: list[tuple[str, str]] = []

    async def _record(method, path, *args, **kwargs):
        calls.append((method, path))
        return {"deleted": True}

    monkeypatch.setattr(trading_agent, "call_main_api", _record)
    return calls


@pytest.mark.parametrize(
    "slug",
    [
        "../settings/servers/victim",
        "..",
        "%2e%2e/settings/servers/victim",
        "%2e%2e",
        "a/b",
        "a\\b",
        "x?y",
        "x#y",
    ],
)
def test_an_agent_delete_refuses_an_id_that_is_not_one_segment(sent, slug):
    result = asyncio.run(trading_agent._delete_agent(slug))

    assert "Invalid agent id" in result["error"]
    assert sent == []


@pytest.mark.parametrize(
    "loop_id",
    [
        "a.b/../../../settings/servers/victim",
        "../x.loop",
        "agent.%2e%2e",
        "agent.loop/extra",
    ],
)
def test_a_loop_delete_refuses_an_id_that_is_not_two_segments(sent, loop_id):
    result = asyncio.run(trading_agent._delete_strategy(loop_id))

    assert "Invalid loop_id" in result["error"]
    assert sent == []


def test_an_ordinary_delete_still_reaches_its_route(sent, tmp_path, monkeypatch):
    assert asyncio.run(trading_agent._delete_strategy("acme.scalper")) == {
        "deleted": True
    }
    assert sent == [("DELETE", "/agents/acme/strategies/scalper")]


def test_a_unicode_slug_is_encoded_not_refused():
    assert path_segment("ñandú_bot") == "%C3%B1and%C3%BA_bot"


@pytest.mark.parametrize(
    "path",
    [
        "/agents/../settings/servers/victim",
        "/agents/%2e%2e/settings/servers/victim",
        "/agents/%2E%2E/settings/servers/victim",
        "/agents/./x",
        "/agents/..\\settings",
    ],
)
def test_the_client_itself_refuses_a_dot_segment(monkeypatch, path):
    """The backstop under every caller: nothing is sent for such a path."""

    def _no_session():
        raise AssertionError("a request was about to be sent")

    monkeypatch.setattr(condor_client, "_get_session", _no_session)

    with pytest.raises(APIError, match="dot segment"):
        asyncio.run(call_main_api("DELETE", path))
