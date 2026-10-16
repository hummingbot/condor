"""One tick, one executor walk: the shared TTL cache in front of the walker
(PERF-736).

Adopting a dead session's executors (CORR-726) put a full ``fetch_all_executors``
walk in the hot tick path, immediately before ``run_core_providers`` — and the
drift provider walks the same ``status="RUNNING"`` set as one of its four
gathered reads. One tick therefore asked the venue the identical question twice,
microseconds apart, through a walker whose own docstring says it is not for the
hot poll. Both cadences are right (a sibling session can end at any point, and
the venue answers for the whole account), so the fix is to ask *once*.

What is pinned here is everything that makes sharing an answer safe rather than
merely cheaper: the filter set and ``max_items`` are in the key, so no caller is
served the rows of a different question; one server's book never reaches
another; an unidentifiable client (every other fake in this suite) is not cached
at all; a failure is never remembered; and the entry expires on a clock, not on
a sleep.

The tick-level claim — adoption and the drift provider sharing one walk — lives
in ``test_executor_adoption.py``, beside the pass that created the duplicate.
"""

import asyncio

import pytest

from condor.fetchers import executors as executors_mod
from condor.fetchers.executors import (
    EXECUTORS_POLL_MAX,
    clear_executor_cache,
    fetch_all_executors,
    fetch_executors,
)

AGENT = "solana_dex_lp_expert"
HOST = "http://cache-host:8000"
OTHER_HOST = "http://other-host:8000"


@pytest.fixture(autouse=True)
def _clean_cache():
    """The cache is process-wide, so it is cleared around every test here.

    The same convention the bot-performance caches follow in their own modules
    (``clear_snapshot_cache`` / ``clear_history_cache``): the clear hook exists
    so a module that reaches a process-wide cache cannot leak an entry into the
    next test, or inherit one from the last.
    """
    clear_executor_cache()
    yield
    clear_executor_cache()


def _row(
    executor_id: str, status: str = "RUNNING", controller_id: str = "main"
) -> dict:
    return {
        "id": executor_id,
        "status": status,
        "controller_id": controller_id,
        "type": "lp_executor",
        "trading_pair": "SOL-USDC",
        "config": {"trading_pair": "SOL-USDC"},
    }


class _Client:
    """A single-page client that records every search, and names its server."""

    def __init__(
        self, base_url: str = HOST, rows=None, raises=None, delay: float = 0.0
    ):
        self.base_url = base_url
        self.rows = list(rows) if rows is not None else [_row("e1")]
        self.raises = raises
        self.delay = delay
        self.searches: list[dict] = []
        self.executors = self

    async def search_executors(self, **kwargs):
        self.searches.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        status = kwargs.get("status")
        matching = [r for r in self.rows if not status or r["status"] == status]
        # ``limit`` is honoured, and the cursor is the offset into the matches:
        # the walker clamps the page size down to what is left of ``max_items``,
        # so a fake that ignored the limit could not tell two ``max_items``
        # apart.
        start = int(kwargs.get("cursor") or 0)
        page = matching[start : start + kwargs["limit"]]
        end = start + len(page)
        return {
            "executors": page,
            "next_cursor": str(end) if end < len(matching) else None,
        }


class _Clock:
    """A monotonic clock a test advances by hand, never by sleeping."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


# ── Sharing one answer ──


def test_two_identical_walks_in_the_ttl_cost_one_request():
    client = _Client()

    first = asyncio.run(fetch_all_executors(client, status="RUNNING"))
    second = asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(client.searches) == 1
    assert [r["id"] for r in first] == [r["id"] for r in second] == ["e1"]


def test_concurrent_callers_coalesce_onto_one_walk():
    """A cold cache is no help on its own: two callers at the same instant
    would both miss it, which is exactly the drift provider's ``gather``."""
    client = _Client(delay=0.01)

    async def _both():
        return await asyncio.gather(
            fetch_all_executors(client, status="RUNNING"),
            fetch_all_executors(client, status="RUNNING"),
        )

    first, second = asyncio.run(_both())

    assert len(client.searches) == 1
    assert first is second, "both callers got the one walk's rows"


# ── Never the wrong answer ──


def test_a_different_filter_set_is_not_served_the_running_rows():
    client = _Client(rows=[_row("live"), _row("stopping", status="SHUTTING_DOWN")])

    running = asyncio.run(fetch_all_executors(client, status="RUNNING"))
    shutting = asyncio.run(fetch_all_executors(client, status="SHUTTING_DOWN"))
    everything = asyncio.run(fetch_all_executors(client))

    assert [r["id"] for r in running] == ["live"]
    assert [r["id"] for r in shutting] == ["stopping"]
    assert [r["id"] for r in everything] == ["live", "stopping"]
    assert [s.get("status") for s in client.searches] == [
        "RUNNING",
        "SHUTTING_DOWN",
        None,
    ]


def test_a_different_max_items_is_its_own_entry():
    """The executors page walks to ``offset + limit + 1``; the agents walk to the
    safety cap. A short walk's rows are not the long walk's answer."""
    client = _Client(rows=[_row(f"e{i}") for i in range(3)])

    short = asyncio.run(fetch_all_executors(client, max_items=1, status="RUNNING"))
    full = asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(short) == 1
    assert len(full) == 3
    assert len(client.searches) == 2


def test_an_unhashable_filter_value_still_keys():
    """The route turns user input into list filters (``executor_types``,
    ``trading_pairs``), which a tuple key could not hold as-is."""
    client = _Client()

    asyncio.run(fetch_all_executors(client, executor_types=["position_executor"]))
    asyncio.run(fetch_all_executors(client, executor_types=["position_executor"]))
    asyncio.run(fetch_all_executors(client, executor_types=["grid_executor"]))

    assert [s.get("executor_types") for s in client.searches] == [
        ["position_executor"],
        ["grid_executor"],
    ]


def test_two_servers_never_share_an_entry():
    one = _Client(base_url=HOST, rows=[_row("on-host")])
    two = _Client(base_url=OTHER_HOST, rows=[_row("on-other")])

    first = asyncio.run(fetch_all_executors(one, status="RUNNING"))
    second = asyncio.run(fetch_all_executors(two, status="RUNNING"))

    assert [r["id"] for r in first] == ["on-host"]
    assert [r["id"] for r in second] == ["on-other"]
    assert len(one.searches) == len(two.searches) == 1


def test_a_client_with_no_base_url_is_never_cached():
    """Every other executor fake in this suite is one of these, which is why
    their call counts are unchanged by this cache."""
    client = _Client(base_url="")

    asyncio.run(fetch_all_executors(client, status="RUNNING"))
    asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(client.searches) == 2
    assert not executors_mod._walk_cache


def test_a_failed_walk_is_not_remembered():
    client = _Client(raises=RuntimeError("API unreachable"))

    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(client.searches) == 2, "a failure must not be served as an answer"
    assert not executors_mod._walk_cache


# ── Freshness and bounds ──


def test_the_entry_expires_on_the_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(executors_mod, "time", clock)
    client = _Client()

    asyncio.run(fetch_all_executors(client, status="RUNNING"))
    clock.now += executors_mod._WALK_TTL / 2
    asyncio.run(fetch_all_executors(client, status="RUNNING"))
    assert len(client.searches) == 1

    clock.now += executors_mod._WALK_TTL + 0.1
    asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(client.searches) == 2


def test_the_ttl_is_far_under_the_fastest_shipped_loop_cadence():
    """No tick may ever be served the previous tick's book. The shipped loops
    run at ``frequency_sec`` 60 at the fastest, so the TTL has an order of
    magnitude of headroom — this is the bound the comment claims."""
    assert executors_mod._WALK_TTL <= 6.0


def test_the_cache_is_lru_bounded(monkeypatch):
    """The key carries user-supplied filters, so an unbounded dict would let a
    scripted scroll pin arbitrarily many pages of rows for the TTL."""
    monkeypatch.setattr(executors_mod, "_WALK_CACHE_MAX", 2)
    client = _Client()

    for pair in ("SOL-USDC", "ETH-USDC", "BTC-USDC"):
        asyncio.run(fetch_all_executors(client, trading_pairs=[pair]))

    assert len(executors_mod._walk_cache) == 2
    assert not [k for k in executors_mod._walk_cache if "SOL-USDC" in repr(k)]


def test_clear_executor_cache_drops_the_entry():
    client = _Client()

    asyncio.run(fetch_all_executors(client, status="RUNNING"))
    clear_executor_cache()
    asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(client.searches) == 2
    assert not executors_mod._walk_inflight


# ── The one caller that must not be cached ──


def test_the_two_second_poll_bypasses_the_cache():
    """``fetch_executors`` is the SDS poll, and its cadence *is* the TTL: an
    entry would be live for about as long as the gap between two ticks, so
    roughly every second tick would paint the dashboard from the previous
    tick's page. There is no duplicate to collapse — nothing else asks for the
    newest page at that instant — so the freshness is not for sale."""
    client = _Client()

    asyncio.run(fetch_executors(client))
    asyncio.run(fetch_executors(client))

    assert len(client.searches) == 2
    assert all(s["limit"] == EXECUTORS_POLL_MAX for s in client.searches)
    assert not executors_mod._walk_cache


def test_the_poll_and_the_unfiltered_walk_do_not_feed_each_other():
    """And the bypass is one-way-safe: the on-demand unfiltered walk is still
    cached, and never serves — or is served — the poll's single page."""
    client = _Client(rows=[_row(f"e{i}") for i in range(3)])

    asyncio.run(fetch_executors(client))
    walked = asyncio.run(fetch_all_executors(client))
    asyncio.run(fetch_executors(client))

    assert len(walked) == 3
    assert [s["limit"] for s in client.searches] == [
        EXECUTORS_POLL_MAX,
        executors_mod.EXECUTORS_PAGE_SIZE,
        EXECUTORS_POLL_MAX,
    ]


# ── The boot scan, which the cache also collapses ──


def test_the_boot_scan_shares_one_walk_across_strategies_on_one_server():
    """``LoopSupervisor._scan_orphaned_executors`` walks once per settled
    strategy, and strategies of one agent share a server. The rows are the same
    question; each strategy's own ``live`` set is applied to them afterwards, so
    sharing the walk cannot move an orphan from one strategy to another."""
    from condor.runtime.loops import LoopSupervisor

    client = _Client(
        rows=[
            _row("alpha-orphan", controller_id=f"{AGENT}.alpha_1"),
            _row("beta-orphan", controller_id=f"{AGENT}.beta_1"),
        ]
    )

    async def factory(_status):
        return client

    found = asyncio.run(
        LoopSupervisor()._scan_orphaned_executors(
            {
                (AGENT, "alpha"): {"chat_id": 1, "user_id": 1},
                (AGENT, "beta"): {"chat_id": 1, "user_id": 1},
            },
            client_factory=factory,
        )
    )

    assert len(client.searches) == 1, "two settled strategies, one walk"
    assert sorted(o.executor_id for o in found) == ["alpha-orphan", "beta-orphan"]
    assert sorted((o.agent_slug, o.strategy_slug) for o in found) == [
        (AGENT, "alpha"),
        (AGENT, "beta"),
    ]
