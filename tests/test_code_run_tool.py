"""``run_code`` — the crossing, the clipping, and the guidance (FEAT-047).

Execution belongs to the main process, so what this file pins on the tool side
is the *delegation*: the right provenance leaves the subprocess, the HTTP
deadline outlives the snippet's own budget (a timeout must come back as a
recorded run, never as an unreachable-API error), and a flood of output is
clipped for the model while the store keeps it whole.

The route half pins the two things a shared execution endpoint must not get
wrong: it is authenticated, and it runs the snippet as the caller.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio

import pytest
from fastapi import HTTPException

from condor.code_runs import CodeRun, CodeRunStore
from condor.web.models import WebUser
from condor.web.routes import code as code_routes
from condor.web.routes.code import RunCodeRequest
from condor.web.routes.code import run_code as run_code_route
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import code as code_tool

CALLER = WebUser(id=7, role="user")


@pytest.fixture
def api(monkeypatch):
    """Capture what the tool sends across to the main process."""
    calls: list[dict] = []

    async def _call(method, path, body=None, timeout=None):
        calls.append({"method": method, "path": path, "body": body, "timeout": timeout})
        return {
            "id": "code_1_aaaa",
            "status": "ok",
            "stdout": "hi\n",
            "result": "2",
            "error": "",
            "traceback": "",
            "report_id": "rep_1",
            "duration_ms": 12,
        }

    monkeypatch.setattr(code_tool, "call_main_api", _call)
    return calls


@pytest.fixture
def seat(monkeypatch):
    """A session with a server and a session key behind it."""
    monkeypatch.setattr(settings, "agent_slug", "quant")
    monkeypatch.setattr(settings, "active_server", "prod")
    monkeypatch.setattr(settings, "session_key", "web:7:slot-1")


@pytest.fixture
def store(tmp_path, monkeypatch):
    written = CodeRunStore(tmp_path / "code_runs")
    monkeypatch.setattr(code_tool, "_store", lambda: written)
    return written


# ── run: the crossing ──


def test_run_delegates_to_the_main_process_with_its_provenance(api, seat):
    out = asyncio.run(code_tool.run_code(code="result = 2", label="probe"))

    assert len(api) == 1
    call = api[0]
    assert (call["method"], call["path"]) == ("POST", "/code/run")
    assert call["body"] == {
        "code": "result = 2",
        "server_name": "prod",
        "label": "probe",
        "timeout": 60,
        "attribute_to": "quant",
        "session_key": "web:7:slot-1",
    }
    assert out["run_id"] == "code_1_aaaa"
    assert out["result"] == "2"
    assert out["report_id"] == "rep_1"


def test_the_http_deadline_outlives_the_snippets_own_budget(api, seat):
    asyncio.run(code_tool.run_code(code="pass", timeout=90))

    assert api[0]["body"]["timeout"] == 90
    assert api[0]["timeout"] == 90 + code_tool._HTTP_MARGIN


def test_a_timeout_above_the_cap_is_clamped(api, seat):
    asyncio.run(code_tool.run_code(code="pass", timeout=9999))

    assert api[0]["body"]["timeout"] == code_tool.MAX_TIMEOUT


def test_the_chat_seat_attributes_runs_to_condor(api, monkeypatch):
    monkeypatch.setattr(settings, "agent_slug", "condor")
    monkeypatch.setattr(settings, "active_server", "prod")
    monkeypatch.setattr(settings, "session_key", "")

    asyncio.run(code_tool.run_code(code="pass"))

    assert api[0]["body"]["attribute_to"] == "condor"


def test_a_missing_server_is_passed_through_not_refused(api, monkeypatch):
    """A pure-pandas snippet needs no server, so a missing one cannot block it."""
    monkeypatch.setattr(settings, "agent_slug", "quant")
    monkeypatch.setattr(settings, "active_server", "")
    monkeypatch.setattr(settings, "session_key", "")

    out = asyncio.run(code_tool.run_code(code="result = 1"))

    assert api[0]["body"]["server_name"] == ""
    assert not out["error"]


def test_run_without_code_never_crosses(api, seat):
    out = asyncio.run(code_tool.run_code(action="run", code="   "))

    assert "error" in out and api == []


def test_an_unknown_action_is_an_error(api, seat):
    assert "error" in asyncio.run(code_tool.run_code(action="explode"))


# ── clipping ──


def test_long_output_is_clipped_for_the_model_with_a_pointer_to_the_full_run(
    monkeypatch, seat
):
    async def _call(method, path, body=None, timeout=None):
        return {
            "id": "code_1_aaaa",
            "status": "ok",
            "stdout": "x" * 9000,
            "result": "y" * 9000,
            "duration_ms": 1,
        }

    monkeypatch.setattr(code_tool, "call_main_api", _call)
    out = asyncio.run(code_tool.run_code(code="pass"))

    assert len(out["stdout"]) == code_tool._CLIP
    assert len(out["result"]) == code_tool._CLIP
    assert out["truncated"] is True
    assert "action='get'" in out["note"] and "code_1_aaaa" in out["note"]


def test_short_output_carries_no_truncation_flag(api, seat):
    assert "truncated" not in asyncio.run(code_tool.run_code(code="pass"))


# ── history and get ──


def _save(store, run_id, agent="quant", **kw):
    store.save(CodeRun(id=run_id, created=1.0, agent=agent, **kw))


def test_history_defaults_to_the_callers_own_runs(store, seat):
    _save(store, "code_1_a", agent="quant", label="mine")
    _save(store, "code_2_a", agent="condor", label="the chat's")

    out = asyncio.run(code_tool.run_code(action="history"))

    assert out["agent"] == "quant"
    assert [r["label"] for r in out["runs"]] == ["mine"]


def test_history_can_ask_for_every_assistants_runs(store, seat):
    _save(store, "code_1_a", agent="quant")
    _save(store, "code_2_a", agent="condor")

    out = asyncio.run(code_tool.run_code(action="history", agent="all"))

    assert len(out["runs"]) == 2 and out["agent"] == "all"


def test_history_is_newest_first_and_honors_limit(store, seat):
    _save(store, "code_1_a", label="older")
    _save(store, "code_2_a", label="newer")

    out = asyncio.run(code_tool.run_code(action="history", limit=1))

    assert [r["label"] for r in out["runs"]] == ["newer"]


def test_get_returns_the_full_record_unclipped(store, seat):
    _save(
        store,
        "code_1_a",
        code="print('x')",
        stdout="z" * 9000,
        status="error",
        traceback='File "<code_run>", line 1',
    )

    out = asyncio.run(code_tool.run_code(action="get", run_id="code_1_a"))

    assert out["code"] == "print('x')"
    assert len(out["stdout"]) == 9000, "get is how a failed snippet is read back"
    assert out["traceback"] == 'File "<code_run>", line 1'


def test_get_without_an_id_or_for_an_unknown_run_is_an_error(store, seat):
    assert "error" in asyncio.run(code_tool.run_code(action="get"))
    assert "error" in asyncio.run(code_tool.run_code(action="get", run_id="code_x"))


def test_history_rereads_the_index_the_main_process_writes(tmp_path, seat, monkeypatch):
    """A cached store would answer with whatever existed at subprocess start."""
    directory = tmp_path / "code_runs"
    monkeypatch.setattr("condor.code_runs._DATA_DIR", directory)
    CodeRunStore(directory)  # the reader's view at startup: empty

    asyncio.run(code_tool.run_code(action="history"))  # warms nothing
    CodeRunStore(directory).save(CodeRun(id="code_9_a", created=1.0, agent="quant"))

    out = asyncio.run(code_tool.run_code(action="history"))
    assert [r["id"] for r in out["runs"]] == ["code_9_a"]


# ── the route ──


def test_the_route_runs_the_snippet_as_the_authenticated_caller(monkeypatch):
    seen: dict = {}

    async def _execute(code, **kw):
        seen.update({"code": code, **kw})
        return {"id": "code_1_aaaa", "status": "ok"}

    monkeypatch.setattr(code_routes, "execute_code", _execute)
    monkeypatch.setattr(
        code_routes, "get_config_manager", lambda: _FakeConfigManager(True)
    )

    body = RunCodeRequest(
        code="result = 1",
        server_name="prod",
        label="probe",
        timeout=30,
        attribute_to="quant",
        session_key="web:7:slot-1",
    )
    out = asyncio.run(run_code_route(body, user=CALLER))

    assert out["id"] == "code_1_aaaa"
    assert seen["chat_id"] == CALLER.id, "never a chat_id taken from the body"
    assert seen["agent"] == "quant"
    assert seen["server_name"] == "prod"
    assert seen["label"] == "probe"
    assert seen["timeout"] == 30
    assert seen["session_key"] == "web:7:slot-1"


class _FakeConfigManager:
    def __init__(self, allowed: bool):
        self.allowed = allowed

    def has_server_access(self, user_id, server_name, *a, **kw):
        return self.allowed


def test_the_route_refuses_a_server_the_caller_cannot_reach(monkeypatch):
    monkeypatch.setattr(
        code_routes, "get_config_manager", lambda: _FakeConfigManager(False)
    )

    body = RunCodeRequest(code="result = 1", server_name="someone-elses")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(run_code_route(body, user=CALLER))

    assert excinfo.value.status_code == 403


def test_the_route_rejects_an_empty_snippet(monkeypatch):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(run_code_route(RunCodeRequest(code="  \n "), user=CALLER))

    assert excinfo.value.status_code == 400


def test_the_route_clamps_the_timeout_to_the_hard_cap(monkeypatch):
    seen: dict = {}

    async def _execute(code, **kw):
        seen.update(kw)
        return {}

    monkeypatch.setattr(code_routes, "execute_code", _execute)
    asyncio.run(run_code_route(RunCodeRequest(code="pass", timeout=9999), user=CALLER))

    assert seen["timeout"] == code_routes.MAX_TIMEOUT


def test_the_route_is_authenticated_like_every_other_router():
    from condor.web.auth import get_current_user

    route = next(r for r in code_routes.router.routes if r.path == "/code/run")
    assert any(
        d.call is get_current_user for d in route.dependant.dependencies
    ), "an endpoint that executes arbitrary Python must never be anonymous"


# ── guidance: the assistant has to be told this exists ──


@pytest.mark.parametrize(
    "instructions",
    [
        pytest.param(lambda s: s._chat_base(), id="chat"),
        pytest.param(lambda s: s._worker_base(), id="worker"),
        pytest.param(lambda s: s._agent_base("quant", "Quant"), id="agent"),
        pytest.param(
            lambda s: s._agent_base("quant", "Quant", worker=True), id="agent-worker"
        ),
    ],
)
def test_every_seat_is_told_to_reach_for_run_code(instructions):
    from mcp_servers.condor import server

    text = instructions(server)
    assert "run_code" in text
    assert "create_routine" in text, "and when to promote a snippet to a routine"


def test_the_routine_cookbook_sends_one_offs_to_run_code():
    from pathlib import Path

    skill = (
        Path(__file__).resolve().parent.parent
        / "agents/_shared/skills/routine_cookbook/SKILL.md"
    )
    text = skill.read_text()
    assert "run_code" in text, "the cookbook must not send a one-off to author a file"
