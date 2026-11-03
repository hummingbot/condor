"""The response that carries a backtest is not a chat-latency call.

The shared API client caps every request at 60s (``config_manager.get_client``),
and aiohttp's ``total`` covers the body read -- so the cap applied to the one poll
that succeeds, the one carrying every executor plus processed_data. A three-month
1m window did not transfer and parse in a minute: the run completed on the server
and Condor timed out reading the answer, losing it before ``_save`` could keep it.

What is pinned here: the read carries its own deadline shaped for a payload, a
client that exposes no session still works, and a finished run that never reached
the store can still be fetched back out of the server.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio
import json
from types import SimpleNamespace

import condor.backtesting as core
from tests.conftest import load_shared_routine

# At import time, like ``test_backtest_one_surface`` does: the suite's autouse
# fixture points AGENTS_ROOT at a tmp dir, so a load from inside a test would look
# for the shipped routine somewhere it was never copied.
bc = load_shared_routine("backtest_chart")

COMPLETED = {
    "task_id": "t-1",
    "status": "completed",
    "config": {"config": {"id": "conf"}},
    "result": {"executors": [{"side": 1}]},
}


class _Response:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status = status
        self.ok = status < 400
        self._text = text

    async def read(self):
        """The real read: bytes off the wire, parsed by the caller off-loop."""
        return json.dumps(self._payload).encode("utf-8")

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Records the url and timeout every request was issued with."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        return self._response


def _client(response):
    session = _Session(response)
    return (
        SimpleNamespace(
            backtesting=SimpleNamespace(session=session, base_url="http://api:8000")
        ),
        session,
    )


def test_the_read_gets_a_deadline_sized_for_a_payload_not_for_a_chat():
    """The regression itself: 60s of total is not the budget this response gets."""
    client, session = _client(_Response(COMPLETED))

    task = asyncio.run(core.get_task(client, "t-1"))

    assert task == COMPLETED
    url, timeout = session.calls[0]
    assert url == "http://api:8000/backtesting/tasks/t-1"
    # A payload that is streaming keeps resetting sock_read however long the
    # transfer takes; a server stalled computing sends nothing and still trips it.
    assert timeout.sock_read == 60
    assert timeout.total > 60


def test_a_client_without_a_session_still_polls():
    """The router fallback -- what keeps every test double in the suite working."""
    seen = []

    async def get_task(task_id):
        seen.append(task_id)
        return COMPLETED

    client = SimpleNamespace(backtesting=SimpleNamespace(get_task=get_task))

    assert asyncio.run(core.get_task(client, "t-1")) == COMPLETED
    assert seen == ["t-1"]


def test_a_refused_read_is_an_error_not_a_retry():
    """A 404 must fail fast; only an unanswered request means "ask again"."""
    client, _ = _client(_Response(None, status=404, text="no such task"))

    try:
        asyncio.run(core.get_task(client, "t-1"))
    except core.BacktestError as e:
        assert "404" in str(e)
    else:
        raise AssertionError("a 404 should raise")


def test_a_finished_run_can_be_fetched_back_after_a_lost_read(tmp_path, monkeypatch):
    """``render it later with task_id=...`` only works if a miss reaches the server."""
    from condor import backtest_store

    monkeypatch.setattr(backtest_store, "_store", None, raising=False)
    monkeypatch.setenv("CONDOR_DATA_DIR", str(tmp_path))
    store = backtest_store.BacktestStore(tmp_path / "backtests")
    monkeypatch.setattr(backtest_store, "get_backtest_store", lambda: store)

    client, _ = _client(_Response(COMPLETED))
    task = asyncio.run(core.fetch_and_save(client, "srv", "t-1"))

    assert task is not None
    saved = store.get_result("t-1")
    assert saved and saved["server"] == "srv"
    # Same normalization the wire gets, so a recovered run renders like a fresh one.
    assert saved["result"]["executors"][0]["side"] == "BUY"


def test_an_unfinished_run_is_not_saved(tmp_path, monkeypatch):
    """The store means "a completed result"; a running task must not enter it."""
    from condor import backtest_store

    store = backtest_store.BacktestStore(tmp_path / "backtests")
    monkeypatch.setattr(backtest_store, "get_backtest_store", lambda: store)

    client, _ = _client(_Response({"task_id": "t-1", "status": "running"}))

    assert asyncio.run(core.fetch_and_save(client, "srv", "t-1")) is None
    assert store.get_result("t-1") is None


def test_a_server_that_cannot_be_reached_is_not_an_error():
    """A recovery attempt is best-effort: the caller still reports the real miss."""

    async def get_task(task_id):
        raise ConnectionError("down")

    client = SimpleNamespace(backtesting=SimpleNamespace(get_task=get_task))

    assert asyncio.run(core.fetch_and_save(client, "srv", "t-1")) is None


# ── The routine's side of the same story ──────────────────────────────────────


def test_the_routine_asks_the_server_for_a_task_it_has_never_stored(monkeypatch):
    """A run lost to a timed-out read is rendered by ``task_id=`` after all."""
    stored: dict[str, dict] = {}
    fetched = []

    monkeypatch.setattr(bc, "_is_saved", lambda tid: tid in stored)

    async def fake_get_client(*a, **k):
        return SimpleNamespace(name="client")

    async def fake_fetch_and_save(client, server, task_id):
        fetched.append((server, task_id))
        stored[task_id] = COMPLETED
        return COMPLETED

    async def fake_render(result, config, chat_id, context):
        return result

    monkeypatch.setattr(bc, "get_client", fake_get_client)
    monkeypatch.setattr(bc, "fetch_and_save", fake_fetch_and_save)
    monkeypatch.setattr(bc, "_render", fake_render)
    monkeypatch.setattr(bc, "_server_name", lambda chat_id, context: "srv")

    async def fake_load_saved_task(tid):
        return stored[tid]["result"], bc.Config()

    monkeypatch.setattr(bc, "_load_saved_task", fake_load_saved_task)

    out = asyncio.run(bc.run(bc.Config(task_id="t-1"), SimpleNamespace(_chat_id=1)))

    assert fetched == [("srv", "t-1")]
    assert out == COMPLETED["result"]


def test_a_task_already_in_the_store_never_touches_the_server(monkeypatch):
    """The recovery is a fall-through, not a refetch: a stored run stays local."""
    monkeypatch.setattr(bc, "_is_saved", lambda tid: True)

    async def boom(*a, **k):
        raise AssertionError("a stored task must not reach the server")

    async def fake_render(result, config, chat_id, context):
        return result

    monkeypatch.setattr(bc, "get_client", boom)
    monkeypatch.setattr(bc, "fetch_and_save", boom)
    monkeypatch.setattr(bc, "_render", fake_render)

    async def fake_load_saved_task(tid):
        return COMPLETED["result"], bc.Config()

    monkeypatch.setattr(bc, "_load_saved_task", fake_load_saved_task)

    out = asyncio.run(bc.run(bc.Config(task_id="t-1"), SimpleNamespace(_chat_id=1)))
    assert out == COMPLETED["result"]
