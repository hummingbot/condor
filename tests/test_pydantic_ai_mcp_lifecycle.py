"""A post-startup MCP collapse must be loud and must kill the client (CORR-332).

``_run_mcp_lifecycle`` used to record an exception only when it fired *before*
the ready event. If an MCP stdio server died later — subprocess crash, host
restart, tool server OOM — ``run_mcp_servers()`` raised on exit, the exception
was discarded with nothing logged, and ``self._agent`` stayed set. ``alive``
therefore kept reporting True and the session layer kept handing prompts to an
agent that had no tools left.

The same clause swallowed ``CancelledError``, so a cancelled lifecycle task
completed "successfully" and ``stop()``'s ``wait_for`` reported no problem.
"""

import asyncio
import contextlib
import logging

from condor.acp.pydantic_ai_client import PydanticAIClient


class _FakeAgent:
    """Stands in for the pydantic-ai ``Agent`` the lifecycle task holds open."""

    def __init__(self, fail: BaseException | None = None, on_enter: bool = False):
        self._fail = fail
        self._on_enter = on_enter
        self.entered = False

    def run_mcp_servers(self):
        @contextlib.asynccontextmanager
        async def _ctx():
            if self._fail is not None and self._on_enter:
                raise self._fail
            self.entered = True
            yield
            if self._fail is not None:
                raise self._fail

        return _ctx()


def _client(agent: _FakeAgent) -> PydanticAIClient:
    """A client wired up exactly as ``start()`` leaves it, minus the real Agent."""
    client = PydanticAIClient(model="ollama:llama3.1")
    client._agent = agent
    client._mcp_servers = [object()]
    client._ready_event = asyncio.Event()
    client._shutdown_event = asyncio.Event()
    client._startup_error = None
    client._lifecycle_error = None
    return client


def test_post_startup_mcp_failure_is_logged_and_kills_the_client(caplog):
    """The MCP context blows up after ready: log it, and stop claiming alive."""
    boom = RuntimeError("mcp stdio server died")
    client = _client(_FakeAgent(fail=boom))

    async def run():
        client._mcp_task = asyncio.create_task(client._run_mcp_lifecycle())
        await client._ready_event.wait()
        assert client.alive is True  # healthy while the servers are up
        client._shutdown_event.set()  # unblocks the wait; __aexit__ then raises
        await client._mcp_task

    with caplog.at_level(logging.ERROR, logger="condor.acp.pydantic_ai_client"):
        asyncio.run(run())

    assert client.alive is False, "a toolless client must not report alive"
    assert client._lifecycle_error is boom
    assert client._mcp_servers == []
    assert any(
        rec.levelno >= logging.ERROR and rec.exc_info for rec in caplog.records
    ), "the post-startup failure must be logged with its traceback"
    # start()'s contract is untouched: this was never a startup failure.
    assert client._startup_error is None


def test_cancelled_lifecycle_task_is_visibly_cancelled():
    """A cancelled lifecycle task must not complete like a clean shutdown."""
    client = _client(_FakeAgent())

    async def run():
        client._mcp_task = asyncio.create_task(client._run_mcp_lifecycle())
        await client._ready_event.wait()
        client._mcp_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await client._mcp_task
        return client._mcp_task

    task = asyncio.run(run())

    assert task.cancelled(), "cancellation must not be converted into a normal return"
    assert client.alive is False


def test_startup_failure_still_reaches_start_unchanged():
    """A failure before ready is still handed to ``start()`` via _startup_error."""
    boom = RuntimeError("mcp server never came up")
    agent = _FakeAgent(fail=boom, on_enter=True)
    client = _client(agent)

    async def run():
        client._mcp_task = asyncio.create_task(client._run_mcp_lifecycle())
        await client._ready_event.wait()
        await client._mcp_task  # returns normally; start() raises the error

    asyncio.run(run())

    assert client._startup_error is boom
    assert agent.entered is False


def test_clean_shutdown_leaves_no_lifecycle_error():
    """The ordinary stop() path stays silent and clears the agent itself."""
    client = _client(_FakeAgent())

    async def run():
        client._mcp_task = asyncio.create_task(client._run_mcp_lifecycle())
        await client._ready_event.wait()
        await client.stop()

    asyncio.run(run())

    assert client._lifecycle_error is None
    assert client._startup_error is None
    assert client.alive is False  # stop() clears the agent
    assert client._mcp_task is None


def test_stop_survives_a_lifecycle_task_cancelled_from_outside():
    """stop() must not turn the task's own cancellation into the caller's."""
    client = _client(_FakeAgent())

    async def run():
        client._mcp_task = asyncio.create_task(client._run_mcp_lifecycle())
        await client._ready_event.wait()
        client._mcp_task.cancel()
        await asyncio.sleep(0)
        await client.stop()  # must not raise CancelledError at us

    asyncio.run(run())

    assert client.alive is False
    assert client._mcp_task is None


# --- A really killed MCP subprocess (PR #240 retest) --------------------------
#
# Everything above drives ``run_mcp_servers()`` *raising*. A SIGKILLed stdio
# server never does: the MCP session's receive loop just closes its streams, the
# lifecycle task stayed parked on ``_shutdown_event`` and ``alive`` stayed True
# while every tool call failed with ``ClosedResourceError``. These use a real
# subprocess and a real pydantic-ai Agent; only the model is scripted.

_SERVER_SCRIPT = """
import os, sys
from mcp.server.fastmcp import FastMCP

with open(sys.argv[1], "w") as f:
    f.write(str(os.getpid()))

mcp = FastMCP("probe")

@mcp.tool()
def ping() -> str:
    return "pong"

mcp.run()
"""


def _tool_calling_model():
    """Calls ``ping`` once, then answers with whatever the tool said."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        last = messages[-1].parts[-1]
        if getattr(last, "part_kind", "") in ("tool-return", "retry-prompt"):
            return ModelResponse(parts=[TextPart(f"tool said: {last.content}")])
        return ModelResponse(parts=[ToolCallPart("ping", {})])

    return FunctionModel(respond)


async def _started_client(tmp_path, monkeypatch) -> tuple[PydanticAIClient, int]:
    import sys

    script = tmp_path / "probe_server.py"
    script.write_text(_SERVER_SCRIPT)
    pid_file = tmp_path / "pid"
    client = PydanticAIClient(
        model="ollama:llama3.1",
        mcp_servers=[{"command": sys.executable, "args": [str(script), str(pid_file)]}],
    )

    async def _model(self):
        return _tool_calling_model()

    monkeypatch.setattr(PydanticAIClient, "_build_model", _model)
    await client.start()
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    return client, int(pid_file.read_text())


async def _kill(pid: int) -> None:
    import os
    import signal

    os.kill(pid, signal.SIGKILL)
    await asyncio.sleep(0.3)  # let the stdio reader see EOF


def test_killed_subprocess_marks_an_idle_client_dead(tmp_path, monkeypatch):
    """No prompt needed: the lifecycle task notices the closed transport."""
    import condor.acp.pydantic_ai_client as mod

    monkeypatch.setattr(mod, "MCP_TRANSPORT_POLL_SECONDS", 0.1)

    async def run():
        client, pid = await _started_client(tmp_path, monkeypatch)
        try:
            assert "pong" in await client.prompt("go")
            assert client.alive is True
            await _kill(pid)
            await asyncio.wait_for(asyncio.shield(client._mcp_task), timeout=10)
            assert client.alive is False
            assert isinstance(client._lifecycle_error, ConnectionError)
        finally:
            await client.stop()

    asyncio.run(run())


def test_killed_subprocess_fails_the_turn_and_marks_the_client_dead(
    tmp_path, monkeypatch
):
    """The prompt meets the dead transport before any poll does."""
    import condor.acp.pydantic_ai_client as mod
    from condor.acp.client import PromptDone

    monkeypatch.setattr(mod, "MCP_TRANSPORT_POLL_SECONDS", 3600)

    async def run():
        client, pid = await _started_client(tmp_path, monkeypatch)
        try:
            await _kill(pid)
            assert client.alive is True  # nothing has looked yet
            events = [e async for e in client.prompt_stream("go")]
            done = [e for e in events if isinstance(e, PromptDone)]
            assert done and done[-1].stop_reason == "error"
            assert client.alive is False
            assert client._lifecycle_error is not None
        finally:
            await asyncio.wait_for(client.stop(), timeout=15)
        assert client._mcp_task is None

    asyncio.run(run())


def test_a_live_subprocess_is_not_marked_dead(tmp_path, monkeypatch):
    """The poll must not kill a healthy client between turns."""
    import condor.acp.pydantic_ai_client as mod

    monkeypatch.setattr(mod, "MCP_TRANSPORT_POLL_SECONDS", 0.05)

    async def run():
        client, _ = await _started_client(tmp_path, monkeypatch)
        try:
            await asyncio.sleep(0.5)  # several polls
            assert client.alive is True
            assert "pong" in await client.prompt("go")
            assert client._lifecycle_error is None
        finally:
            await client.stop()
        assert client._lifecycle_error is None

    asyncio.run(run())
