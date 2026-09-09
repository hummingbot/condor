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

    with pytest.raises(ConnectionError) as excinfo:
        await asyncio.wait_for(client.start(), timeout=30)

    # The type stays the one CORR-329 established; only the message grew.
    assert _MARKER in str(excinfo.value)
    assert client.command in str(excinfo.value)


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
