"""Control-socket agent handlers: dispatch + error mapping for the lifecycle /
consult / delegate surface (full-headless). Underlying agent machinery is
stubbed so nothing spawns a real LLM run.
"""

import asyncio
import uuid

import pytest

from condor.control.client import ControlError, call_control
from condor.control.handlers import build_agent_handlers
from condor.control.server import ControlServer


def _sock():
    return f"/tmp/condor-ah-{uuid.uuid4().hex[:8]}.sock"


async def _serve(sock):
    server = ControlServer(build_agent_handlers(), socket_path=sock)
    await server.start()
    return server


def test_agent_list_dispatches(tmp_path, monkeypatch):
    from condor.agents import engine

    class _E:
        def get_info(self):
            return {"agent_id": "mm_1", "status": "running", "tick_count": 3}

    monkeypatch.setattr(engine, "get_all_engines", lambda: {"mm_1": _E()})
    sock = _sock()

    async def run():
        server = await _serve(sock)
        try:
            return await call_control("agent.list", socket_path=sock)
        finally:
            await server.stop()

    out = asyncio.run(run())
    assert out == {
        "agents": [{"agent_id": "mm_1", "status": "running", "tick_count": 3}]
    }


def test_agent_verb_unknown_errors(tmp_path):
    sock = _sock()

    async def run():
        server = await _serve(sock)
        try:
            await call_control(
                "agent.verb",
                {"slug": "mm", "agent_id": "mm_1", "verb": "bogus"},
                socket_path=sock,
            )
        finally:
            await server.stop()

    with pytest.raises(ControlError) as ei:
        asyncio.run(run())
    assert ei.value.status == 400


def test_delegate_start_returns_run_id(tmp_path, monkeypatch):
    """delegate.start dispatches to start_delegation and returns its run_id
    (a delegation is a run — C.1)."""
    from condor.agents import delegate as dg

    async def fake_start(*, agent_slug, task, timeout_s=None, risk_limits=None):
        return "01RUNDELEGATE0000000000000"

    monkeypatch.setattr(dg, "start_delegation", fake_start)
    sock = _sock()

    async def run():
        server = await _serve(sock)
        try:
            return await call_control(
                "delegate.start", {"agent": "scout", "task": "scan"}, socket_path=sock
            )
        finally:
            await server.stop()

    assert asyncio.run(run()) == {
        "run_id": "01RUNDELEGATE0000000000000",
        "status": "running",
    }


def test_delegate_get_and_stop_are_removed(tmp_path):
    """The delegation status/stop surface is gone (C.1) — a delegation is a
    run, tracked via run.get / agent.verb. The old methods now 404."""
    sock = _sock()

    async def run(method, params):
        server = await _serve(sock)
        try:
            await call_control(method, params, socket_path=sock)
        finally:
            await server.stop()

    for method in ("delegate.list", "delegate.get", "delegate.stop"):
        with pytest.raises(ControlError) as ei:
            asyncio.run(
                run(method, {"task_id": "x"} if method != "delegate.list" else None)
            )
        assert ei.value.status == 404  # unknown method
