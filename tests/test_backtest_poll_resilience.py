"""A stalled poll is not a failed backtest.

The API server computes a backtest on the same event loop it answers HTTP on, so
a wide window over fine candles stalls every request to it. The poll then hit the
client's own request timeout and the bare ``TimeoutError`` propagated out of the
routine — killing a run whose task was still perfectly healthy on the server, and
reporting "❌ Routine backtest_chart failed: TimeoutError:" to the user.

What is pinned here: an unanswered poll only means "ask again", and the two
things that must NOT change with it — the overall deadline still bounds the wait,
and a server that answers with nonsense is still an error.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.backtesting import BacktestError, _poll_task


def _client(answers):
    """A backtesting client whose ``get_task`` replays ``answers``.

    An ``Exception`` instance in the list is raised instead of returned.
    """
    seen = []

    async def get_task(task_id):
        answer = answers[min(len(seen), len(answers) - 1)]
        seen.append(task_id)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = SimpleNamespace(backtesting=SimpleNamespace(get_task=get_task))
    return client, seen


def _poll(client, timeout=5.0):
    return asyncio.run(_poll_task(client, "t-1", poll_interval=0.0, timeout=timeout))


def test_a_poll_that_times_out_is_retried_not_raised():
    """The exact failure from the incident: the task was fine, the request wasn't."""
    client, seen = _client(
        [
            TimeoutError(),
            TimeoutError(),
            {"task_id": "t-1", "status": "completed", "result": {"x": 1}},
        ]
    )

    task = _poll(client)

    assert task["status"] == "completed"
    assert len(seen) == 3


def test_a_server_that_never_answers_still_gives_up_at_the_deadline():
    """Retrying must not become waiting forever."""
    client, _ = _client([TimeoutError()])

    with pytest.raises(BacktestError) as exc:
        _poll(client, timeout=0.0)

    assert "still running" in str(exc.value)
    assert "t-1" in str(exc.value)


def test_an_unreadable_answer_is_still_an_error():
    """A server that replies with nonsense is a different problem from a slow one."""
    client, _ = _client(["not a task"])

    with pytest.raises(BacktestError) as exc:
        _poll(client)

    assert "unreadable" in str(exc.value)


def test_the_last_status_seen_is_what_the_deadline_reports():
    """A stalled final poll must not erase what the server last said."""

    async def get_task(task_id):
        if not seen:
            seen.append(task_id)
            return {"task_id": "t-1", "status": "queued"}
        seen.append(task_id)
        raise TimeoutError()

    seen: list[str] = []
    client = SimpleNamespace(backtesting=SimpleNamespace(get_task=get_task))

    with pytest.raises(BacktestError) as exc:
        asyncio.run(_poll_task(client, "t-1", poll_interval=0.0, timeout=0.02))

    assert "still queued" in str(exc.value)
