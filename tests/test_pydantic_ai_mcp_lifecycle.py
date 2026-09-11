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
