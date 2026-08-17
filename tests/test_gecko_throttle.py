"""The shared GeckoTerminal budget: rate gate, circuit breaker, single-flight.

GeckoTerminal's free tier is ~30 requests/minute *per IP*, and every dashboard
viewer, the candle poll loop and the pool explorer draw on that one budget. The
symptom when it runs out is not an error — every caller in ``pool_data`` degrades
to an empty list — so a blown budget looks exactly like a chain with no pools.
These cover the three guards that keep it from blowing, and the accounting that
makes it visible when it does.
"""

import asyncio
import re
from pathlib import Path

import pytest

from handlers.dex import pool_data


def run(coro):
    """Drive a coroutine to completion (the suite has no async plugin)."""
    return asyncio.run(coro)


class _Counting:
    """A gecko client that records calls instead of making them."""

    def __init__(self, result=None, error=None, delay=0.0):
        self.calls = 0
        self._result = result if result is not None else {"data": []}
        self._error = error
        self._delay = delay

    async def api_request(self, *_a, **_k):
        return await self._answer()

    async def get_pool_by_network_address(self, *_a, **_k):
        return await self._answer()

    async def _answer(self):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


# ── Rate gate ──


def test_the_budget_is_spent_and_then_refused(monkeypatch):
    """Past the per-minute budget a caller is refused rather than queued forever.

    A concurrency cap alone never did this: four *fast* concurrent requests
    sustain well over 100/min without ever looking like a burst.
    """
    client = _Counting()
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)
    monkeypatch.setattr(pool_data, "_GECKO_RATE_LIMIT", 3)
    # Don't queue for a slot — fail fast so the test does not sleep a minute.
    monkeypatch.setattr(pool_data, "_GECKO_MAX_WAIT", 0.0)

    async def drive():
        for _ in range(3):
            await pool_data.gecko_request("GET", "networks/solana/pools")
        with pytest.raises(pool_data.GeckoRateLimited):
            await pool_data.gecko_request("GET", "networks/solana/pools")

    run(drive())
    assert client.calls == 3, "the refused call must not reach the wire"


def test_a_refusal_is_recognized_as_a_rate_limit():
    """Callers already branch on ``_is_rate_limited``; the local refusal must
    read the same as gecko's own 429 so none of them need a second code path."""
    assert pool_data._is_rate_limited(pool_data.GeckoRateLimited("no budget left"))


# ── Circuit breaker ──


def test_a_429_stops_the_next_call_entirely(monkeypatch):
    """Requests sent inside a throttled window are spent for nothing and keep the
    window rolling, so the cheapest response to a 429 is silence."""
    client = _Counting(error=RuntimeError("429 Too Many Requests"))
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    async def drive():
        with pytest.raises(RuntimeError):
            await pool_data.gecko_request("GET", "networks/solana/pools")
        with pytest.raises(pool_data.GeckoRateLimited):
            await pool_data.gecko_request("GET", "networks/solana/pools")

    run(drive())
    assert client.calls == 1


def test_a_429_is_never_retried(monkeypatch):
    """Gecko's limit is a per-minute window; no backoff short enough to sit
    inside a web request clears it, and a retry only spends more of the budget."""
    client = _Counting(error=RuntimeError("429 Too Many Requests"))
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    async def drive():
        with pytest.raises(RuntimeError):
            await pool_data.gecko_request("GET", "networks/solana/pools")

    run(drive())
    assert client.calls == 1


def test_a_timeout_is_retried(monkeypatch):
    """A slow chain listing is not a throttled one — that one is worth retrying."""
    import httpx

    client = _Counting(error=httpx.ReadTimeout("slow"))
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)
    monkeypatch.setattr(pool_data, "_GECKO_BACKOFF", (0.0, 0.0))

    async def drive():
        with pytest.raises(httpx.ReadTimeout):
            await pool_data.gecko_request("GET", "networks/solana/pools")

    run(drive())
    assert client.calls == pool_data._GECKO_RETRIES + 1


def test_the_breaker_reports_itself(monkeypatch):
    """ "Are we rate limited?" must have an answer that is not "read the logs"."""
    client = _Counting(error=RuntimeError("429 Too Many Requests"))
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    assert pool_data.gecko_health()["rate_limited"] is False

    async def drive():
        with pytest.raises(RuntimeError):
            await pool_data.gecko_request("GET", "networks/solana/pools")

    run(drive())

    health = pool_data.gecko_health()
    assert health["rate_limited"] is True
    assert health["count_429"] == 1
    assert 0 < health["cooldown_remaining"] <= pool_data._GECKO_COOLDOWN
    assert health["last_429_age"] is not None


def test_health_counts_the_budget_spent(monkeypatch):
    client = _Counting()
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    async def drive():
        for _ in range(3):
            await pool_data.gecko_request("GET", "networks/solana/pools")

    run(drive())
    assert pool_data.gecko_health()["requests_last_minute"] == 3


# ── Single-flight ──


def test_concurrent_askers_for_one_page_share_one_request(monkeypatch):
    """Flicking between explorer tabs re-asks for a listing while the first
    request is still in the air, so a TTL cache — written only on arrival —
    cannot help. Without this, four fast clicks are four upstream requests."""
    client = _Counting(delay=0.05)
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    async def drive():
        return await asyncio.gather(
            *(pool_data._gecko_page_rows("solana", "trending", "", 1) for _ in range(6))
        )

    results = run(drive())
    assert client.calls == 1
    assert all(r == [] for r in results)


def test_each_sharer_gets_its_own_rows(monkeypatch):
    """The shared answer is one list object; a caller mutating its copy — the
    explorer decorates rows per network — must not corrupt the others."""
    rows = [{"id": "solana_abc", "address": "abc"}]
    client = _Counting(result={"data": [{"id": "solana_abc", "attributes": {}}]})
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)
    monkeypatch.setattr(pool_data, "glom", lambda _payload, _spec: list(rows))

    async def drive():
        return await asyncio.gather(
            *(pool_data._gecko_page_rows("solana", "trending", "", 1) for _ in range(3))
        )

    a, b, _c = run(drive())
    assert a is not b
    a[0]["address"] = "mutated"
    assert b[0]["address"] == "abc"


def test_a_caller_walking_away_does_not_cancel_the_shared_fetch(monkeypatch):
    """The stampede's own cause — a viewer navigating away mid-request — must not
    take down the fetch the viewers who stayed are waiting on."""
    client = _Counting(delay=0.05)
    monkeypatch.setattr(pool_data, "_gecko_client", lambda: client)

    async def drive():
        leaver = asyncio.ensure_future(
            pool_data._gecko_page_rows("solana", "top", "", 1)
        )
        stayer = asyncio.ensure_future(
            pool_data._gecko_page_rows("solana", "top", "", 1)
        )
        await asyncio.sleep(0.01)
        leaver.cancel()
        return await stayer

    assert run(drive()) == []
    assert client.calls == 1


# ── The gate is the only door ──


def test_no_gecko_call_bypasses_the_budget():
    """Every outbound request goes through ``gecko_call``. Calling the client
    directly skips the rate gate — which is how the candle poll loop, the highest
    frequency caller in the process, used to exhaust the budget while the guards
    sat in front of two pool-list walks.
    """
    source = Path(pool_data.__file__).read_text()
    # The one legitimate use is inside gecko_call itself, which resolves the
    # method by name so tests can swap the client.
    direct = re.findall(r"_gecko_client\(\)\.\w+", source)
    assert direct == [], f"these bypass the rate gate: {direct}"


# Everything below the repo root except pool_data.py, which owns the client.
_REPO = Path(pool_data.__file__).resolve().parents[2]
_SKIP = {".venv", ".git", "node_modules", "__pycache__", "dist", "build"}


def _repo_sources():
    for path in _REPO.rglob("*.py"):
        if _SKIP & set(path.parts):
            continue
        if path.name == "pool_data.py" or path.parts[-2:] == (
            "tests",
            Path(__file__).name,
        ):
            continue
        yield path


def test_pool_data_is_the_only_owner_of_a_gecko_client():
    """A second ``GeckoTerminalAsyncClient()`` anywhere is two bugs at once.

    It bypasses the shared budget *and* — since none of the old call sites ever
    called ``close()`` — leaks an httpx client per invocation, which is how a
    process runs out of file descriptors rather than merely out of quota.
    """
    offenders = [
        str(p.relative_to(_REPO))
        for p in _repo_sources()
        if "GeckoTerminalAsyncClient()" in p.read_text()
    ]
    assert offenders == [], f"construct clients outside pool_data: {offenders}"


def test_nobody_hand_builds_a_geckoterminal_url():
    """A hand-rolled ``api.geckoterminal.com`` URL is a request the gate never
    sees. Routines and MCP tools reach the same per-IP limit as the dashboard."""
    offenders = [
        str(p.relative_to(_REPO))
        for p in _repo_sources()
        if "api.geckoterminal.com" in p.read_text()
    ]
    assert offenders == [], f"bypass the rate gate with a raw URL: {offenders}"
