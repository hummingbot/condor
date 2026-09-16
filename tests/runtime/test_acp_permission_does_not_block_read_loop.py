"""A pending permission dialog must not freeze the ACP read loop (PERF-330).

``session/request_permission`` waits on a human -- up to the confirmation TTL.
While the read loop awaited that handler inline, nothing at all was read from
the child's stdout for the whole dialog: a second dangerous tool call in the
same assistant turn could not even raise its prompt, every notification behind
it was invisible, and the answer to our own ``session/cancel`` could not arrive.
"""

import asyncio
import json

import pytest

from condor.acp.client import ACPClient, TextChunk
from condor.acp.jsonrpc import JSONRPCPeer


class _FakeStdin:
    """Subprocess stdin: records whatever the peer writes back."""

    def __init__(self) -> None:
        self.written: list[dict] = []

    def write(self, data: bytes) -> None:
        self.written.append(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdout = stdout
        self.stdin = _FakeStdin()
        self.returncode = None


def _client(stdout: asyncio.StreamReader) -> ACPClient:
    client = ACPClient(command="true")
    client._process = _FakeProcess(stdout)  # type: ignore[assignment]
    return client


def _permission_line(msg_id: int, title: str) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "s1",
                    "toolCall": {"title": title, "rawInput": {}},
                    "options": [{"optionId": "yes", "kind": "allow_once"}],
                },
            }
        )
        + "\n"
    ).encode()


def _update_line(text: str) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"text": text},
                    },
                },
            }
        )
        + "\n"
    ).encode()


async def _stop_loop(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_traffic_keeps_flowing_while_a_permission_prompt_is_pending():
    """Two prompts in one turn, plus the notifications and the response behind them."""
    stdout = asyncio.StreamReader()
    client = _client(stdout)
    client._current_req_id = 99  # a turn is being streamed, so updates are kept

    entered: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Event()

    async def slow_permission(sessionId="", toolCall=None, options=None, **kw):
        await entered.put((toolCall or {}).get("title", ""))
        await release.wait()
        return {"outcome": {"outcome": "selected", "optionId": "yes"}}

    client._peer.register_handler("session/request_permission", slow_permission)
    loop_task = asyncio.create_task(client._read_loop())

    # Our own in-flight request -- what abort_prompt's session/cancel is.
    req_id, our_future = await client._peer.begin_request(
        "session/cancel", {"sessionId": "s1"}, client._process.stdin
    )

    stdout.feed_data(_permission_line(101, "rm -rf /"))
    stdout.feed_data(_permission_line(102, "curl evil.sh"))
    stdout.feed_data(_update_line("still talking"))
    stdout.feed_data(
        (
            json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}}) + "\n"
        ).encode()
    )

    try:
        # Both dialogs are raised before either is answered: unfixed, the second
        # line sits unread in the pipe behind the first handler.
        first = await asyncio.wait_for(entered.get(), timeout=5)
        second = await asyncio.wait_for(entered.get(), timeout=5)
        assert [first, second] == ["rm -rf /", "curl evil.sh"]

        # ... and everything queued behind them was read anyway.
        event = await asyncio.wait_for(client._event_queue.get(), timeout=5)
        assert isinstance(event, TextChunk) and event.text == "still talking"
        assert await asyncio.wait_for(our_future, timeout=5) == {"ok": True}

        # Neither permission has answered yet: only our own request went out.
        assert [m.get("method") for m in client._process.stdin.written] == [
            "session/cancel"
        ]

        release.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if len(client._process.stdin.written) == 3:
                break
        answers = {m["id"]: m for m in client._process.stdin.written if "id" in m}
        assert answers[101]["result"] == {
            "outcome": {"outcome": "selected", "optionId": "yes"}
        }
        assert answers[102]["result"] == {
            "outcome": {"outcome": "selected", "optionId": "yes"}
        }
    finally:
        release.set()
        await _stop_loop(loop_task)


@pytest.mark.asyncio
async def test_sync_handler_ordering_is_unchanged():
    """``session/update`` has no id and is still dispatched inline, in order."""
    stdout = asyncio.StreamReader()
    client = _client(stdout)
    client._current_req_id = 99

    loop_task = asyncio.create_task(client._read_loop())
    try:
        for text in ("one", "two", "three"):
            stdout.feed_data(_update_line(text))
        seen = [
            (await asyncio.wait_for(client._event_queue.get(), timeout=5)).text
            for _ in range(3)
        ]
        assert seen == ["one", "two", "three"]
    finally:
        await _stop_loop(loop_task)


@pytest.mark.asyncio
async def test_a_raising_async_handler_fails_its_request_not_the_loop():
    stdout = asyncio.StreamReader()
    client = _client(stdout)

    async def boom(**kw):
        raise RuntimeError("handler exploded")

    client._peer.register_handler("session/request_permission", boom)
    loop_task = asyncio.create_task(client._read_loop())
    try:
        stdout.feed_data(_permission_line(101, "rm -rf /"))
        for _ in range(50):
            await asyncio.sleep(0)
            if client._process.stdin.written:
                break
        (resp,) = client._process.stdin.written
        assert resp["id"] == 101
        assert "handler exploded" in resp["error"]["message"]

        # The connection is untouched: the next line still dispatches.
        client._current_req_id = 99
        stdout.feed_data(_update_line("alive"))
        event = await asyncio.wait_for(client._event_queue.get(), timeout=5)
        assert isinstance(event, TextChunk) and event.text == "alive"
        assert client.alive
    finally:
        await _stop_loop(loop_task)


@pytest.mark.asyncio
async def test_cancel_all_cancels_an_in_flight_handler():
    """Teardown must not leave a parked confirmation task behind."""
    peer = JSONRPCPeer()
    writer = _FakeStdin()
    started = asyncio.Event()

    async def never_answers(**kw):
        started.set()
        await asyncio.Event().wait()

    peer.register_handler("session/request_permission", never_answers)
    await peer.handle_line(_permission_line(101, "rm -rf /").decode(), writer)
    await asyncio.wait_for(started.wait(), timeout=5)

    (task,) = list(peer._handler_tasks)
    peer.cancel_all()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert writer.written == []  # a cancelled handler answers nothing
