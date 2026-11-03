"""Resolving a bare ``ollama:`` / ``lmstudio:`` key must not park the loop.

``PydanticAIClient.start()`` builds its model on the one event loop that also
runs Telegram polling, the dashboard, every WebSocket and every other agent
session. When the model id is left to us ("ollama:"), building it means asking
the local backend what it serves — which used to be a synchronous
``urllib.request.urlopen``, freezing everything else for the whole timeout
whenever the backend was down or hung (PERF-331).
"""

import asyncio
import dataclasses

import pytest
from aiohttp import web

import condor.runtime.timeouts as timeouts_mod
from condor.acp.pydantic_ai_client import PydanticAIClient


def _short_probe(monkeypatch, budget: float = 0.25) -> None:
    """Shrink the probe budget so a hung backend fails in test time."""
    monkeypatch.setattr(
        timeouts_mod,
        "TIMEOUTS",
        dataclasses.replace(timeouts_mod.TIMEOUTS, local_model_probe=budget),
    )


async def _ticker(stop: asyncio.Event, counter: list) -> None:
    """Stand in for every other coroutine sharing the loop."""
    while not stop.is_set():
        await asyncio.sleep(0.01)
        counter.append(1)


def test_probe_against_a_hung_backend_leaves_the_loop_responsive(monkeypatch):
    _short_probe(monkeypatch)

    async def scenario():
        # A socket that accepts the connection and then never answers: the
        # worst case for the caller, because it fails on read, not connect.
        release = asyncio.Event()

        async def hang(reader, writer):
            await release.wait()
            writer.close()

        server = await asyncio.start_server(hang, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        stop = asyncio.Event()
        ticks: list = []
        beat = asyncio.create_task(_ticker(stop, ticks))
        try:
            client = PydanticAIClient(model="ollama:")
            with pytest.raises(RuntimeError, match="No local model found"):
                await client._resolve_default_local_model(
                    prefix="ollama", base_url=f"http://127.0.0.1:{port}/v1"
                )
        finally:
            stop.set()
            await beat
            release.set()
            server.close()
            await server.wait_closed()

        # The probe was in flight for at least one budget; a blocked loop
        # produces zero ticks in that window.
        assert (
            len(ticks) >= 10
        ), f"loop was starved during the probe: {len(ticks)} ticks"

    asyncio.run(scenario())


def test_resolution_order_is_unchanged(monkeypatch):
    """env override → /v1/models first id → Ollama /api/tags first name."""
    monkeypatch.delenv("CONDOR_DEFAULT_LOCAL_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    _short_probe(monkeypatch, budget=2.0)

    async def scenario():
        serve_v1 = {"on": True}

        async def models(_request):
            if not serve_v1["on"]:
                return web.json_response({"error": "nope"}, status=404)
            return web.json_response({"data": [{"id": "openai-compat-model"}]})

        async def tags(_request):
            return web.json_response({"models": [{"name": "native-model"}]})

        app = web.Application()
        app.router.add_get("/v1/models", models)
        app.router.add_get("/api/tags", tags)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}/v1"
        client = PydanticAIClient(model="ollama:")
        try:
            assert (
                await client._resolve_default_local_model(
                    prefix="ollama", base_url=base
                )
                == "openai-compat-model"
            )

            serve_v1["on"] = False
            assert (
                await client._resolve_default_local_model(
                    prefix="ollama", base_url=base
                )
                == "native-model"
            )
            # lmstudio never falls back to the Ollama-native endpoint.
            with pytest.raises(RuntimeError, match="No local model found"):
                await client._resolve_default_local_model(
                    prefix="lmstudio", base_url=base
                )

            monkeypatch.setenv("CONDOR_DEFAULT_LOCAL_MODEL", "env-wins")
            assert (
                await client._resolve_default_local_model(
                    prefix="ollama", base_url=base
                )
                == "env-wins"
            )
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
