"""FEAT-028: a routine an agent runs from chat is a real run.

The condor MCP server is a stdio subprocess, so a routine it executed itself was
invisible to the dashboard: no instance in ``RoutineStore``, no post-run hooks,
and — for a continuous one — no way to stop it. These tests pin the crossing:
``run``/``start``/``stop``/``list_instances`` go to the main process over
``call_main_api``, under the name the store (and the chat dock) knows.
"""

import asyncio

import pytest

import routines.base as routines_base
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.tools import routines as mcp_routines

ONESHOT_SRC = (
    "from pydantic import BaseModel\n"
    "class Config(BaseModel):\n"
    '    """probe"""\n'
    "async def run(config, context):\n"
    "    return 'done'\n"
)

CONTINUOUS_SRC = (
    "from pydantic import BaseModel\n"
    "CONTINUOUS = True\n"
    "class Config(BaseModel):\n"
    '    """watcher"""\n'
    "async def run(config, context):\n"
    "    return 'stopped'\n"
)

TYPED_SRC = (
    "from pydantic import BaseModel\n"
    "class Config(BaseModel):\n"
    '    """typed"""\n'
    "    limit: int = 5\n"
    "async def run(config, context):\n"
    "    return 'ok'\n"
)


class FakeAPI:
    """Stands in for the main process: records calls, replays canned instances."""

    def __init__(self, instance: dict | None = None, raises: Exception | None = None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.instance = instance or {"status": "completed", "result_text": "done"}
        self.raises = raises

    async def __call__(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        if self.raises:
            raise self.raises
        if method == "POST" and path in ("/routines/run", "/routines/start"):
            return {"instance_id": "inst-1"}
        if path.startswith("/routines/instances/") and method == "GET":
            return self.instance
        if path == "/routines/instances":
            return [{"instance_id": "inst-1", "routine_name": "scout/probe"}]
        return {}

    @property
    def posted(self) -> dict:
        return next(b for m, _, b in self.calls if m == "POST" and b)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """An isolated project root with a global library and one agent's routines."""
    monkeypatch.setattr(routines_base, "_PROJECT_ROOT", tmp_path)

    # The general library is discovered from the real ``routines/`` package dir
    # (not _PROJECT_ROOT), so stand it in wholesale with one temp routine.
    lib = tmp_path / "global_lib"
    lib.mkdir()
    (lib / "global_probe.py").write_text(ONESHOT_SRC)
    library = routines_base.discover_routines_from_path(lib)
    monkeypatch.setattr(routines_base, "discover_routines", lambda **kw: library)

    agent_dir = tmp_path / "agents" / "scout" / "routines"
    agent_dir.mkdir(parents=True)
    (agent_dir / "probe.py").write_text(ONESHOT_SRC)
    (agent_dir / "watcher.py").write_text(CONTINUOUS_SRC)
    (agent_dir / "typed.py").write_text(TYPED_SRC)

    monkeypatch.setattr(mcp_routines.settings, "active_server", "srv")
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", "")
    monkeypatch.setattr(mcp_routines, "_POLL_INTERVAL", 0)
    return tmp_path


def _api(monkeypatch, **kw) -> FakeAPI:
    api = FakeAPI(**kw)
    monkeypatch.setattr(mcp_routines, "call_main_api", api)
    return api


# ── run: the run happens in the main process, under the store's name ──


def test_agent_routine_is_run_under_its_prefixed_name(project, monkeypatch):
    """The dock filters the agent's panel on the ``{slug}/`` prefix, and
    RoutineStore resolves agent routines by the same key — a bare name would
    record a run that never shows up where this feature exists to show it."""
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", "scout")
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.run_routine("probe", {}))

    assert api.calls[0][:2] == ("POST", "/routines/run")
    assert api.posted["routine_name"] == "scout/probe"
    assert api.posted["attribute_to"] == "scout"
    assert api.posted["server_name"] == "srv"
    assert out["instance_id"] == "inst-1"
    assert out["result"]["text"] == "done"


def test_global_routine_keeps_its_bare_name(project, monkeypatch):
    """The general library is registered unprefixed; the chat is ``condor``."""
    api = _api(monkeypatch)

    asyncio.run(mcp_routines.run_routine("global_probe", {}))

    assert api.posted["routine_name"] == "global_probe"
    assert api.posted["attribute_to"] == "condor"


def test_run_polls_until_the_instance_leaves_running(project, monkeypatch):
    api = _api(monkeypatch)
    states = [
        {"status": "running"},
        {"status": "running"},
        {"status": "completed", "result_text": "done", "has_chart": True},
    ]

    async def call(method, path, body=None, timeout=None):
        api.calls.append((method, path, body))
        if method == "POST":
            return {"instance_id": "inst-1"}
        return states.pop(0)

    monkeypatch.setattr(mcp_routines, "call_main_api", call)

    out = asyncio.run(mcp_routines.run_routine("global_probe", {}))

    assert states == []  # every intermediate poll was consumed
    assert out["result"]["chart_image"] == "(PNG bytes, view via dashboard)"


def test_failed_run_surfaces_the_instances_error(project, monkeypatch):
    _api(monkeypatch, instance={"status": "failed", "error": "ValueError: boom"})

    out = asyncio.run(mcp_routines.run_routine("global_probe", {}))

    assert "ValueError: boom" in out["error"]
    assert out["instance_id"] == "inst-1"


def test_unreachable_main_process_is_a_tool_error(project, monkeypatch):
    """The model must get a message it can relay, not a raw transport failure."""
    _api(monkeypatch, raises=APIError("API error (403): No access"))

    out = asyncio.run(mcp_routines.run_routine("global_probe", {}))

    assert "No access" in out["error"]


def test_run_without_an_active_server_never_calls_the_api(project, monkeypatch):
    monkeypatch.setattr(mcp_routines.settings, "active_server", "")
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.run_routine("global_probe", {}))

    assert "server" in out["error"].lower()
    assert api.calls == []


def test_bad_config_fails_locally_with_a_useful_message(project, monkeypatch):
    """Validation stays in the subprocess so it does not arrive as a 404."""
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", "scout")
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.run_routine("typed", {"limit": "not-an-int"}))

    assert "Invalid config" in out["error"]
    assert api.calls == []


def test_continuous_routine_points_at_start(project, monkeypatch):
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", "scout")
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.run_routine("watcher", {}))

    assert "start" in out["error"]
    assert api.calls == []


# ── start / stop / list: one store, the dashboard's ──


def test_continuous_start_is_registered_in_the_main_process(project, monkeypatch):
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", "scout")
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.start_routine("watcher", {}))

    assert api.calls[0][:2] == ("POST", "/routines/start")
    assert api.posted["routine_name"] == "scout/watcher"
    assert out == {"started": True, "instance_id": "inst-1", "routine": "watcher"}


def test_start_rejects_a_one_shot_routine(project, monkeypatch):
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", "scout")
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.start_routine("probe", {}))

    assert "not continuous" in out["error"]
    assert api.calls == []


def test_stop_targets_the_main_process_instance(project, monkeypatch):
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.stop_routine("inst-1"))

    assert api.calls == [("POST", "/routines/instances/inst-1/stop", None)]
    assert out == {"stopped": True, "instance_id": "inst-1"}


def test_stop_reports_an_unknown_instance(project, monkeypatch):
    _api(monkeypatch, raises=APIError("API error (404): Instance not found"))

    out = asyncio.run(mcp_routines.stop_routine("nope"))

    assert "nope" in out["error"]


def test_list_instances_reads_the_main_process_store(project, monkeypatch):
    api = _api(monkeypatch)

    out = asyncio.run(mcp_routines.list_instances())

    assert api.calls == [("GET", "/routines/instances", None)]
    assert out["instances"][0]["routine_name"] == "scout/probe"


def test_manage_routines_awaits_the_delegated_actions(project, monkeypatch):
    """stop/list_instances became coroutines — the dispatcher must await them."""
    _api(monkeypatch)

    assert asyncio.run(mcp_routines.manage_routines("stop", "inst-1"))["stopped"]
    assert "instances" in asyncio.run(mcp_routines.manage_routines("list_instances"))
