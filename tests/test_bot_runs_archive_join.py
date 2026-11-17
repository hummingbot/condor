"""One Runs tab covers stopped bots too: each run carries its archive's db_path.

The dashboard used to show the same stopped bots twice — a Runs tab reading
``bot_runs`` out of the API's Postgres, and an Archived tab that walked every
archived sqlite through the per-database ``/summary`` endpoint (five full table
loads each, on the API's event loop). Runs absorbed Archived, which only works
because a deployment writes ``bot_name == instance_name`` and archives the
instance directory under that same name, so the two stores address one run by
one string.

These pin the join: it uses the cheap listing (a directory walk that opens no
database), it is keyed on the instance name, a run with no archive gets ``None``
rather than a guessed path, and a backend that cannot list archives still serves
the runs.
"""

import asyncio

import pytest

from condor.fetchers.bot_performance import clear_archived_cache
from condor.web.routes import controller_performance as cp


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    clear_archived_cache()
    yield
    clear_archived_cache()


class FakeClient:
    """A backend with a bot-runs table and a set of archived databases."""

    def __init__(self, runs, archived=(), archived_error=None):
        self._runs = runs
        self._archived = list(archived)
        self._archived_error = archived_error
        self.list_calls = 0
        # The client is its own router namespace, as the fakes elsewhere do.
        self.base_url = "http://fake-server"
        self.bot_orchestration = self
        self.archived_bots = self

    async def get_bot_runs(self, **kwargs):
        self.runs_kwargs = kwargs
        offset, limit = kwargs.get("offset", 0), kwargs.get("limit")
        return self._runs[offset : offset + limit]

    async def list_databases(self):
        self.list_calls += 1
        if self._archived_error:
            raise self._archived_error
        return self._archived

    # The archived list must be the *only* per-bot call the route makes.
    async def get_database_summary(self, db_path):  # pragma: no cover - guard
        raise AssertionError(
            "the runs route must never open an archived database to list runs"
        )

    async def get_latest_controller_performance(self, **kwargs):
        return []


def _run_route(client, monkeypatch, limit=100, offset=0):
    """Call the bot-runs route against ``client``, bypassing auth and config."""

    class FakeCM:
        async def get_client(self, name):
            return client

    monkeypatch.setattr(cp, "get_config_manager", lambda: FakeCM())
    return asyncio.run(
        cp.get_bot_runs(name="srv", limit=limit, offset=offset, user=object())
    )


ARCHIVED_DIR = "bots/archived/{n}/data/{n}.sqlite"


def test_an_archived_run_carries_the_path_to_its_database(monkeypatch):
    name = "backpack_mm_perps-20260811-115730"
    client = FakeClient(
        runs=[{"bot_name": name, "deployment_status": "ARCHIVED"}],
        archived=[ARCHIVED_DIR.format(n=name)],
    )

    resp = _run_route(client, monkeypatch)

    assert resp.runs[0].archive_db_path == ARCHIVED_DIR.format(n=name)


def test_a_run_whose_database_is_gone_reports_no_archive(monkeypatch):
    """Deleting a bot run removes its directory but can leave the PG row behind.

    The path must come from the listing, never be synthesised from the bot name —
    otherwise the row offers a drill-in that 404s.
    """
    client = FakeClient(
        runs=[{"bot_name": "deleted-20260101-000000", "deployment_status": "ARCHIVED"}],
        archived=[ARCHIVED_DIR.format(n="someone-else-20260101-000000")],
    )

    resp = _run_route(client, monkeypatch)

    assert resp.runs[0].archive_db_path is None


def test_a_live_run_has_no_archive_yet(monkeypatch):
    client = FakeClient(
        runs=[{"bot_name": "live-20260828-120000", "deployment_status": "DEPLOYED"}],
        archived=[],
    )

    resp = _run_route(client, monkeypatch)

    assert resp.runs[0].deployment_status == "DEPLOYED"
    assert resp.runs[0].archive_db_path is None


def test_runs_still_load_when_the_archive_listing_fails(monkeypatch):
    """The archive join is enrichment; losing it must not lose the run history."""
    client = FakeClient(
        runs=[{"bot_name": "b-20260101-000000", "deployment_status": "ARCHIVED"}],
        archived_error=RuntimeError("archived-bots endpoint missing"),
    )

    resp = _run_route(client, monkeypatch)

    assert [r.bot_name for r in resp.runs] == ["b-20260101-000000"]
    assert resp.runs[0].archive_db_path is None


def test_the_listing_is_fetched_once_per_page_not_once_per_run(monkeypatch):
    """What makes the merge cheap: one directory walk covers the whole table."""
    names = [f"bot-2026010{i}-000000" for i in range(1, 6)]
    client = FakeClient(
        runs=[{"bot_name": n, "deployment_status": "ARCHIVED"} for n in names],
        archived=[ARCHIVED_DIR.format(n=n) for n in names],
    )

    resp = _run_route(client, monkeypatch)

    assert client.list_calls == 1
    assert all(r.archive_db_path for r in resp.runs)


# ── WR-05: a full page is not the whole history ──


def _three_runs():
    return [{"bot_name": f"bot-{n}", "id": n} for n in (3, 2, 1)]


def test_a_page_that_is_not_the_last_says_so(monkeypatch):
    client = FakeClient(_three_runs())

    out = _run_route(client, monkeypatch, limit=2)

    assert [r.bot_name for r in out.runs] == ["bot-3", "bot-2"]
    assert out.total == 2, "the page length, never the probe row"
    assert out.has_more is True
    # Asked for one more than the page; the caller's offset is untouched.
    assert client.runs_kwargs["limit"] == 3
    assert client.runs_kwargs["offset"] == 0


def test_the_last_page_says_there_is_nothing_older(monkeypatch):
    out = _run_route(FakeClient(_three_runs()), monkeypatch, limit=2, offset=2)

    assert [r.bot_name for r in out.runs] == ["bot-1"]
    assert out.has_more is False


def test_a_history_that_exactly_fills_the_page_has_no_more(monkeypatch):
    out = _run_route(FakeClient(_three_runs()), monkeypatch, limit=3)

    assert out.total == 3
    assert out.has_more is False
