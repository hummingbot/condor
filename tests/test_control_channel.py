"""Control channel: unix-socket JSON-RPC server + client, and the executor
handlers over a real ExecutorLog-backed runtime with a faked gateway.
"""

import asyncio
import uuid

import pytest

from condor.control.client import ControlError, call_control
from condor.control.handlers import build_executor_handlers
from condor.control.server import ControlServer
from condor.executors.log import ExecutorLog
from condor.executors.ops import ExecutorOpError
from condor.executors.runtime import ExecutorRuntime
from tests.test_executors import FakeGateway, swap_config


def _sock(_tmp_path):
    # Short path: macOS caps AF_UNIX socket paths at ~104 chars; pytest tmp_path
    # is far too long. The real socket (store/condor-control.sock) is well under.
    return f"/tmp/condor-ct-{uuid.uuid4().hex[:8]}.sock"


async def _serve(handlers, sock):
    server = ControlServer(handlers, socket_path=sock)
    await server.start()
    return server


# -- transport -----------------------------------------------------------------


def test_ping_roundtrip(tmp_path):
    sock = _sock(tmp_path)

    async def run():
        server = await _serve({"ping": lambda: {"ok": True}}, sock)
        try:
            return await call_control("ping", socket_path=sock)
        finally:
            await server.stop()

    assert asyncio.run(run()) == {"ok": True}


def test_unknown_method_errors(tmp_path):
    sock = _sock(tmp_path)

    async def run():
        server = await _serve({"ping": lambda: {"ok": True}}, sock)
        try:
            await call_control("nope", socket_path=sock)
        finally:
            await server.stop()

    with pytest.raises(ControlError) as ei:
        asyncio.run(run())
    assert ei.value.status == 404


def test_handler_error_status_propagates(tmp_path):
    sock = _sock(tmp_path)

    def boom():
        raise ExecutorOpError(404, "Executor not found")

    async def run():
        server = await _serve({"executor.get": lambda **kw: boom()}, sock)
        try:
            await call_control("executor.get", {"executor_id": "x"}, socket_path=sock)
        finally:
            await server.stop()

    with pytest.raises(ControlError) as ei:
        asyncio.run(run())
    assert ei.value.status == 404
    assert "not found" in ei.value.message


def test_socket_unavailable_raises(tmp_path):
    with pytest.raises(ControlError) as ei:
        asyncio.run(call_control("ping", socket_path=_sock(tmp_path)))
    assert ei.value.status == 503


# -- executor handlers end-to-end ----------------------------------------------


def test_executor_create_list_stop_over_socket(tmp_path, monkeypatch):
    sock = _sock(tmp_path)
    log = ExecutorLog(tmp_path / "agents")
    runtime = ExecutorRuntime(store=log)
    runtime._jupiter = FakeGateway(prices=[100.0])

    # Point the executor handlers at our test runtime.
    from condor.executors import service

    monkeypatch.setattr(service, "_runtime", runtime, raising=False)

    async def run():
        server = await _serve(build_executor_handlers(), sock)
        try:
            cfg = swap_config(agent_slug="mm", agent_id="mm_1")
            created = await call_control(
                "executor.create",
                {
                    "type": "order_spot",
                    "config": {
                        "chain_network": cfg.chain_network,
                        "wallet_address": cfg.wallet_address,
                        "base_token": cfg.base_token,
                        "quote_token": cfg.quote_token,
                        "amount": str(cfg.amount),
                        "side": cfg.side,
                    },
                    "agent_slug": "mm",
                    "agent_id": "mm_1",
                },
                socket_path=sock,
            )
            assert created["status"] == "PENDING"
            eid = created["id"]
            # let it run to completion (FakeGateway confirms immediately)
            await runtime.wait_all()

            got = await call_control("executor.get", {"executor_id": eid}, socket_path=sock)
            assert got["id"] == eid
            assert got["status"] == "CLOSED"

            listed = await call_control("executor.list", {"agent_id": "mm_1"}, socket_path=sock)
            assert eid in [e["id"] for e in listed["executors"]]
            return eid
        finally:
            await server.stop()

    eid = asyncio.run(run())
    # the swap really landed in the per-agent log
    assert log.load(eid).status == "CLOSED"
