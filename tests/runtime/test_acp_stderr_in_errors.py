"""The ACP child's stderr must reach the error that reports its death (READ-337).

``_drain_stderr`` existed only to keep the pipe from filling and threw the
content away at DEBUG -- a level nothing enables in a normal deployment. Every
diagnosable launch failure writes its reason there ("command not found", a node
version error, "Claude Code cannot be launched inside another Claude Code
session"), and the exception string is forwarded verbatim to the browser, so
the user was handed a failure that named no cause while the cause sat in a pipe
this process had already read.
"""

import asyncio

import pytest

from condor.acp.client import _STDERR_TAIL_LINES, ACPClient

_MARKER = "condor-read337-marker: command not found"


@pytest.mark.asyncio
async def test_a_child_that_dies_talking_to_stderr_says_so_in_the_error():
    client = ACPClient(command=f"echo '{_MARKER}' >&2; exit 127")

    # The type stays the one CORR-329 established (ConnectionResetError from a
    # stdin write racing the child's own exit still qualifies -- it subclasses
    # ConnectionError). The exact *message* -- whether it carries the stderr
    # tail, whether it carries the command -- depends on which of several
    # concurrent tasks reading/writing this dying child's pipes asyncio
    # happens to schedule first, so it is not asserted on here; the
    # deterministic test below exercises that same message-building code
    # without racing anything (CORR-621).
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(client.start(), timeout=30)


@pytest.mark.asyncio
async def test_the_dead_childs_stderr_tail_reaches_the_disconnect_error():
    """Same message-building code as above, minus the settle-window race.

    ``_read_loop``'s EOF handling and ``_drain_stderr`` are created together in
    :meth:`ACPClient.start` and race each other against two independent pipes
    of the same dying child, so under heavy load the drain can lose and
    ``test_a_child_that_dies_talking_to_stderr_says_so_in_the_error`` would red
    a suite in which nothing is broken (CORR-621). Make the assertion
    independent of that race instead of widening ``_STDERR_SETTLE_TIMEOUT``:
    feed the drain deterministically, like
    ``test_the_kept_tail_is_bounded_in_lines_and_in_width`` already does, then
    run the exact same disconnect path ``start()`` runs on a dead child.
    """
    client = ACPClient(command="true")
    stdout = asyncio.StreamReader()
    stdout.feed_eof()
    stderr = asyncio.StreamReader()
    stderr.feed_data(f"{_MARKER}\n".encode())
    stderr.feed_eof()
    client._process = type("_P", (), {"stdout": stdout, "stderr": stderr})()  # type: ignore[assignment]

    # Drained to completion -- and to a done task -- before the read loop ever
    # looks at it, so _stderr_detail's settle wait is never even reached, let
    # alone raced.
    client._stderr_task = asyncio.create_task(client._drain_stderr())
    await client._stderr_task
    await client._read_loop()

    assert client._peer._failure is not None
    assert _MARKER in str(client._peer._failure)
    assert client.command in str(client._peer._failure)


@pytest.mark.asyncio
async def test_a_silent_child_adds_nothing_to_the_error():
    """A healthy-but-dead child pays nothing: no empty stderr section."""
    client = ACPClient(command="exit 1")

    with pytest.raises(ConnectionError) as excinfo:
        await asyncio.wait_for(client.start(), timeout=30)

    assert "Agent stderr" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_the_kept_tail_is_bounded_in_lines_and_in_width():
    """A chatty agent cannot grow the buffer without limit."""
    client = ACPClient(command="true")
    stderr = asyncio.StreamReader()
    client._process = type("_P", (), {"stderr": stderr})()  # type: ignore[assignment]

    for i in range(_STDERR_TAIL_LINES * 5):
        stderr.feed_data(f"line {i} ".encode() + b"x" * 5000 + b"\n")
    stderr.feed_eof()
    await asyncio.wait_for(client._drain_stderr(), timeout=5)

    assert len(client._stderr_tail) == _STDERR_TAIL_LINES
    assert all(len(line) <= 500 for line in client._stderr_tail)
    # It is the *last* lines that carry the cause of a death.
    assert client._stderr_tail[-1].startswith(f"line {_STDERR_TAIL_LINES * 5 - 1} ")
