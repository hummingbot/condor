"""Live-only executor reads filter server-side, so history cannot crowd them out
(PERF-733).

Nothing upstream prunes terminated executor records — the Agent Builder Cup's
hosts ended the race with 35 / 173 / 0 / 2 / 16 of them — and
``fetch_all_executors`` walks up to ``MAX_EXECUTORS_FETCH`` rows in
``EXECUTORS_PAGE_SIZE`` pages. A caller that wants only live executors must
therefore pass ``status=`` so the filter reaches ``search_executors``, instead of
walking the whole history and dropping the terminal rows afterwards: the walk's
budget is then spent on rows the caller keeps, and a history larger than the cap
cannot truncate the live rows away before they are returned.

What is pinned here: the filter travels to ``search_executors`` on every page of
the walk, a terminal history larger than the cap still yields every live row to a
live-only caller (and really does truncate when unfiltered, so that is not a
vacuous assertion), the unfiltered walk still sees terminated rows for the paths
that need them, and the boot orphan scan reads under the filter rather than
relying on its own client-side guard.
"""

import asyncio

from condor.fetchers.executors import (
    EXECUTORS_PAGE_SIZE,
    MAX_EXECUTORS_FETCH,
    fetch_all_executors,
)

AGENT = "solana_dex_lp_expert"
STRATEGY = "lp_slot_operator"
DEAD_TAG = f"{AGENT}.{STRATEGY}_1"

# Enough terminated history to overrun the walker's cap on its own.
TERMINAL_ROWS = MAX_EXECUTORS_FETCH + EXECUTORS_PAGE_SIZE
LIVE_ROWS = 3


def _row(executor_id: str, status: str, controller_id: str = DEAD_TAG) -> dict:
    return {
        "id": executor_id,
        "status": status,
        "controller_id": controller_id,
        "type": "lp_executor",
        "trading_pair": "SOL-USDC",
        "config": {"controller_id": "main", "trading_pair": "SOL-USDC"},
    }


class _FilteringClient:
    """A paginated client that honours ``status`` server-side, as the API does.

    The live rows sit *behind* the terminal flood, which is the case the item is
    about: a host with enough accumulated history pushes its own live executors
    past the cap of an unfiltered walk.
    """

    def __init__(self, terminal: int = TERMINAL_ROWS, live: int = LIVE_ROWS):
        self.rows = [_row(f"done-{i}", "TERMINATED") for i in range(terminal)]
        self.rows += [_row(f"live-{i}", "RUNNING") for i in range(live)]
        self.executors = self
        self.searches: list[dict] = []

    async def search_executors(self, **kwargs):
        self.searches.append(kwargs)
        status = kwargs.get("status")
        matching = [r for r in self.rows if not status or r["status"] == status]
        offset = int(kwargs.get("cursor") or 0)
        limit = int(kwargs["limit"])
        page = matching[offset : offset + limit]
        nxt = offset + limit
        return {
            "data": page,
            "next_cursor": str(nxt) if nxt < len(matching) else None,
        }


def test_a_live_only_walk_sends_the_filter_on_every_page():
    client = _FilteringClient()

    rows = asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert [r["id"] for r in rows] == [f"live-{i}" for i in range(LIVE_ROWS)]
    # Not "the first request carried it": the filter is bound into the partial,
    # so a walk that needed several pages would carry it on all of them.
    assert client.searches
    assert all(s.get("status") == "RUNNING" for s in client.searches)


def test_history_larger_than_the_cap_still_yields_every_live_row():
    client = _FilteringClient()

    rows = asyncio.run(fetch_all_executors(client, status="RUNNING"))

    assert len(rows) == LIVE_ROWS
    # One request, not a walk: the server-side filter means the 5000+ terminated
    # rows are never paged through at all.
    assert len(client.searches) == 1


def test_the_same_history_does_truncate_the_live_rows_when_unfiltered():
    """The previous assertion is not vacuous: dropping the filter loses them."""
    client = _FilteringClient()

    rows = asyncio.run(fetch_all_executors(client))

    assert len(rows) == MAX_EXECUTORS_FETCH
    assert not [r for r in rows if r["status"] == "RUNNING"]
    assert all("status" not in s for s in client.searches)


def test_the_unfiltered_walk_still_sees_terminated_rows():
    """The orphan and history paths read terminal rows through this same walker.

    ``list_orphaned_positions`` asks the API's own
    ``/executors/positions/orphaned`` endpoint, so the filter cannot reach it at
    all; what the filter could have broken is every other reader of terminated
    rows, which goes through an unfiltered walk. That stays unfiltered.
    """
    client = _FilteringClient(terminal=2, live=1)

    rows = asyncio.run(fetch_all_executors(client))

    assert [r["status"] for r in rows] == ["TERMINATED", "TERMINATED", "RUNNING"]


def test_the_boot_orphan_scan_reads_under_the_filter():
    from condor.runtime.loops import LoopSupervisor

    client = _FilteringClient(terminal=TERMINAL_ROWS, live=1)

    async def factory(status):
        return client

    found = asyncio.run(
        LoopSupervisor()._scan_orphaned_executors(
            {(AGENT, STRATEGY): {"chat_id": 77, "user_id": 5}},
            client_factory=factory,
        )
    )

    # The live row is reported even though 5500 terminated ones precede it,
    # which is only true because the search was filtered server-side.
    assert [o.executor_id for o in found] == ["live-0"]
    assert all(s.get("status") == "RUNNING" for s in client.searches)
