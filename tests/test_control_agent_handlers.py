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
    assert out == {"agents": [{"agent_id": "mm_1", "status": "running", "tick_count": 3}]}


def test_agent_verb_unknown_errors(tmp_path):
    sock = _sock()

    async def run():
        server = await _serve(sock)
        try:
            await call_control(
                "agent.verb", {"slug": "mm", "agent_id": "mm_1", "verb": "bogus"},
                socket_path=sock,
            )
        finally:
            await server.stop()

    with pytest.raises(ControlError) as ei:
        asyncio.run(run())
    assert ei.value.status == 400


def test_delegate_list_dispatches(tmp_path, monkeypatch):
    from condor.agents import delegate as dg

    monkeypatch.setattr(dg, "get_all_delegations", lambda: {})
    sock = _sock()

    async def run():
        server = await _serve(sock)
        try:
            return await call_control("delegate.list", socket_path=sock)
        finally:
            await server.stop()

    assert asyncio.run(run()) == {"delegations": []}


def test_delegate_get_missing_errors(tmp_path, monkeypatch):
    from condor.agents import delegate as dg

    monkeypatch.setattr(dg, "get_delegation", lambda _tid: None)
    sock = _sock()

    async def run():
        server = await _serve(sock)
        try:
            # a task_id that won't match the "<slug>-dN" transcript-file fallback
            await call_control("delegate.get", {"task_id": "nomatch"}, socket_path=sock)
        finally:
            await server.stop()

    with pytest.raises(ControlError) as ei:
        asyncio.run(run())
    assert ei.value.status == 404
