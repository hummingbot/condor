"""A delegated task can be given a longer budget than the default (ARCH-310).

``delegate(action="start")` used to run at whatever the route defaulted to --
900s -- because the MCP tool never sent a budget at all. The knob existed on the
wire (``timeout_s`` on the request body, threaded to ``start_delegation``'s
``asyncio.wait_for``) but nothing could reach it, so a background job bigger
than fifteen minutes was cut off mid-run with no recourse.

Pinned here: the tool declares the parameter and forwards it, omitting the key
entirely when the caller asked for nothing so the default lives in exactly one
place; the route honours a caller's budget, keeps 900s otherwise, and refuses a
budget that would kill the worker instantly or outlive the agent session's own
hard ceiling.

Sync tests driving coroutines with ``asyncio.run`` (pytest-asyncio is not
installed in this venv), fakes in the style of test_agents_chat_id_ownership.py.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import config_manager
from condor.agents import delegate as delegate_module
from condor.web.models import WebUser
from condor.web.routes import agents as agents_routes
from condor.web.routes.agents import (
    DEFAULT_DELEGATE_TIMEOUT_S,
    MAX_DELEGATE_TIMEOUT_S,
    DelegateRequest,
    delegate_agent,
)

CALLER = WebUser(id=1, role="user")


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return False


@pytest.fixture(autouse=True)
def cm(monkeypatch):
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)


def _delegate(monkeypatch, req: DelegateRequest):
    """Drive the route with the agent lookup and the runner stubbed out."""
    monkeypatch.setattr(agents_routes, "_get_agent", lambda slug: SimpleNamespace())

    async def _no_conversation(session_key: str) -> str:
        return ""

    monkeypatch.setattr(agents_routes, "_conversation_for_session", _no_conversation)
    started: list[dict] = []

    async def fake_start(**kw):
        started.append(kw)
        return SimpleNamespace(task_id="t-1", status="running")

    monkeypatch.setattr(delegate_module, "start_delegation", fake_start)
    result = asyncio.run(delegate_agent("scout", req, user=CALLER))
    return started, result


# -- The route: whose budget is it --


def test_a_caller_can_buy_more_than_the_default_fifteen_minutes(monkeypatch):
    started, result = _delegate(
        monkeypatch, DelegateRequest(task="build three routines", timeout_s=1800)
    )

    assert result["task_id"] == "t-1"
    assert started[0]["timeout_s"] == 1800


def test_asking_for_nothing_still_gets_todays_default(monkeypatch):
    """Backwards compatibility: an unchanged caller runs exactly as before."""
    started, _ = _delegate(monkeypatch, DelegateRequest(task="scan pools"))

    assert started[0]["timeout_s"] == 900
    assert DEFAULT_DELEGATE_TIMEOUT_S == 900


def test_the_routes_default_is_the_runners_default(monkeypatch):
    """Two copies of 900 exist (route body, runner signature) -- pin the drift."""
    assert DEFAULT_DELEGATE_TIMEOUT_S == delegate_module.DEFAULT_TIMEOUT_S


@pytest.mark.parametrize("budget", [0, -1])
def test_a_budget_that_would_kill_the_worker_instantly_is_refused(monkeypatch, budget):
    """``wait_for(timeout=0)`` cancels before the first tool call: not a task."""
    with pytest.raises(HTTPException) as exc:
        _delegate(monkeypatch, DelegateRequest(task="t", timeout_s=budget))

    assert exc.value.status_code == 400
    assert "timeout_s" in exc.value.detail


def test_a_budget_past_the_session_ceiling_is_refused_with_the_limit(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _delegate(
            monkeypatch,
            DelegateRequest(task="t", timeout_s=MAX_DELEGATE_TIMEOUT_S + 1),
        )

    assert exc.value.status_code == 400
    # The detail is what the model reads back, so it must name the ceiling.
    assert str(MAX_DELEGATE_TIMEOUT_S) in exc.value.detail


def test_the_ceiling_stays_under_the_acp_prompt_hard_stop():
    """A budget the agent session cannot honour would be a promise, not a knob.

    ``ACPClient.prompt_stream`` stops a prompt at its own hard ceiling, so an
    outer budget past that only delays the same cut-off. Read from the source
    rather than copied, so raising one and not the other fails here.
    """
    import re

    from condor.acp import client as acp_client

    src = Path(acp_client.__file__).read_text()
    ceiling = int(re.search(r"max_duration = \(?\s*(\d+)", src).group(1))

    assert MAX_DELEGATE_TIMEOUT_S <= ceiling


# -- The MCP tool: can a caller reach it at all --


def _start_body(monkeypatch, **kwargs) -> dict:
    """Call the tool's ``start`` action and return the body it POSTed."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import delegate as delegate_tool

    # Not the background seat: a worker is refused before it can post anything
    # (``specialist_slug`` is a read-only property derived from the slug).
    monkeypatch.setattr(settings, "delegate_worker", False)
    sent: dict = {}

    async def fake_call(method, path, body=None, timeout=None):
        sent["method"] = method
        sent["path"] = path
        sent["body"] = body
        return {"task_id": "t-1", "status": "running"}

    monkeypatch.setattr(delegate_tool, "call_main_api", fake_call)
    asyncio.run(
        delegate_tool.delegate(action="start", agent="scout", task="t", **kwargs)
    )
    return sent["body"]


def test_the_tool_forwards_the_budget_the_caller_asked_for(monkeypatch):
    body = _start_body(monkeypatch, timeout_sec=1800)

    assert body["timeout_s"] == 1800


def test_the_tool_sends_no_budget_when_none_was_asked_for(monkeypatch):
    """The default belongs to the route; a copy in every request would drift."""
    body = _start_body(monkeypatch)

    assert "timeout_s" not in body
    # And nothing else about the request changed.
    assert body["task"] == "t"
    assert body["on_complete"] == "notify"


def test_the_declared_tool_lets_a_model_pass_a_budget():
    """The parameter must be on the MCP schema or no caller can reach it."""
    from mcp_servers.condor import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    schema = tools["delegate"].inputSchema

    assert "timeout_sec" in schema["properties"]
    doc = server.delegate.__doc__ or ""
    # Acceptance criterion: the docstring states the default and the override.
    assert "timeout_sec" in doc
    assert "900" in doc
