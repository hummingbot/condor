"""The agent routes take the caller from the JWT, never from the body (READ-695).

``AskRequest``, ``StartStrategyRequest``, ``DelegateRequest`` and
``NotifyRequest`` used to declare a ``user_id`` that no handler read (SEC-035 and
SEC-051 made every route use ``user.id``). The field only documented an override
path that did not exist, so it is gone. Old clients that still post it keep
working because pydantic ignores unknown keys, and the condor MCP server no
longer sends it: its identity already travels in the JWT it mints from
``settings.user_id``.
"""

import asyncio

import pytest

from condor.web.routes.agents import (
    AskRequest,
    DelegateRequest,
    NotifyRequest,
    StartStrategyRequest,
)

_MODELS = [
    (AskRequest, {"task": "t"}),
    (StartStrategyRequest, {}),
    (DelegateRequest, {"task": "t"}),
    (NotifyRequest, {"text": "t"}),
]


@pytest.mark.parametrize(
    "model,required", _MODELS, ids=lambda m: getattr(m, "__name__", "")
)
def test_the_request_model_has_no_user_id_but_still_accepts_one(model, required):
    assert "user_id" not in model.model_fields
    req = model(**required, user_id=999)
    assert not hasattr(req, "user_id")
    assert "user_id" not in req.model_dump()


def _stub_settings(monkeypatch):
    from mcp_servers.condor.settings import settings

    for key, value in {
        "agent_slug": "scout",
        "ask_target": False,
        "delegate_worker": False,
        "chat_id": 42,
        "user_id": 7,
        "active_server": "",
        "session_key": "",
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _capture(monkeypatch, module, reply):
    called: list = []

    async def fake_api(method, path, body=None, timeout=None):
        called.append((method, path, body))
        return reply

    monkeypatch.setattr(module, "call_main_api", fake_api)
    return called


def test_the_mcp_delegate_tool_posts_no_user_id(monkeypatch):
    from mcp_servers.condor.tools import delegate as delegate_tool

    _stub_settings(monkeypatch)
    called = _capture(
        monkeypatch, delegate_tool, {"task_id": "x", "status": "running", "answer": ""}
    )

    asyncio.run(delegate_tool.delegate(action="ask", agent="lp", task="q"))
    asyncio.run(delegate_tool.delegate(action="start", agent="lp", task="q"))

    posts = [(path, body) for method, path, body in called if method == "POST"]
    assert {path for path, _ in posts} >= {"/agents/lp/ask", "/agents/lp/delegate"}
    for _, body in posts:
        assert "user_id" not in body


def test_the_mcp_notification_tool_posts_no_user_id(monkeypatch):
    from mcp_servers.condor.tools import notification as notification_tool

    _stub_settings(monkeypatch)
    called = _capture(monkeypatch, notification_tool, {"sent": True})

    asyncio.run(notification_tool.send_notification("hello"))

    method, path, body = called[0]
    assert (method, path) == ("POST", "/agents/notify")
    assert "user_id" not in body
