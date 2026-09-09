"""Tests for SEC-591: ``db_path`` is a URL path segment, not free text.

The archived routes take the database to read as a *query* parameter, and hand
it to a client that interpolates it raw:
``f"/archived-bots/{db_path}/summary"``. yarl then *parses* the result rather
than escaping it, so a value like ``"../accounts/master_account/credentials?"``
does not 404 — it resolves to a different, real endpoint, and the shared session
attaches the server's BasicAuth to it. Unlike a FastAPI *path* parameter (a
single non-slash segment by the time Starlette has routed it), a query parameter
carries ``/``, ``..``, ``?`` and ``#`` through untouched, so every one of these
routes was an authenticated-GET pivot for anyone holding TRADER on one server.

This is the SEC-115 class at the sites SEC-115 never covered; ``validate_db_path``
is the multi-segment sibling of its ``validate_identifier``, and these tests pin
both halves: the guard admits only what a real archive path looks like, and the
routes refuse a bad one *before* a client is ever asked for.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.archived as archived_routes
import condor.web.routes.controller_performance as cperf_routes
from condor.fetchers._identifiers import IdentifierError, validate_db_path
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")

# The pivots, one per escape technique. Every one of these reaches a *different*
# upstream endpoint (or a differently-parsed URL) if it is passed through.
PIVOTS = [
    # Plain traversal — verified against this repo's yarl to resolve to
    # /accounts/master_account/credentials.
    "../accounts/master_account/credentials?",
    "../../admin",
    # ".." embedded mid-path, not just as a prefix.
    "bots/archived/../../accounts/master_account/credentials.sqlite",
    "a/./b.sqlite",
    # Query and fragment: everything after them stops being the path.
    "run.sqlite?x=1",
    "run.sqlite#frag",
    # Percent-encoded and double-encoded separators, for the decoder upstream.
    "..%2Faccounts%2Fmaster_account.sqlite",
    "..%252Faccounts%252Fmaster_account.sqlite",
    "%2e%2e/admin.sqlite",
    # NUL and whitespace.
    "run\x00.sqlite",
    "run .sqlite",
    "run.sqlite\n",
    "\trun.sqlite",
    # Windows separators and drive letters.
    "..\\accounts\\master.sqlite",
    "C:\\bots\\run.sqlite",
    # Empty segments and bare separators.
    "",
    "/",
    "//accounts/master.sqlite",
    "bots//run.sqlite",
    "bots/run.sqlite/",
    # A prefix-sibling of the archive directory, reached by climbing out first.
    "../archived-bots-private/run.sqlite",
    # Not a database at all — the upstream reader only ever opens .sqlite/.db.
    "accounts/master_account/credentials",
    "run.sqlite.bak",
]

# What the backend actually reports, absolute and relative, both suffixes.
LEGIT_PATHS = [
    "bots/archived/y/data/broken.sqlite",
    "bots/archived/x/data/other-20260101-000000.db",
    "/data/sec591-probe.sqlite",
    "/archived/ancient-sec591.sqlite",
    "/a-sec591.sqlite",
    "bots/archived/hummingbot-v2-1.5/data/v2_with_controllers.sqlite",
]

# The archived-run LRU is module level and keyed by (server, db_path), so the
# happy-path tests below would otherwise leave a warm entry that a later test
# reading the same run silently hits instead of its own stub backend.
SERVER = "sec591-srv"


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    from condor.fetchers.archived_run import _performance_cache

    _performance_cache.clear()
    yield
    _performance_cache.clear()


# ── The guard itself ──


@pytest.mark.parametrize("payload", PIVOTS)
def test_the_guard_refuses_every_escape(payload):
    with pytest.raises(IdentifierError):
        validate_db_path(payload)


@pytest.mark.parametrize("path", LEGIT_PATHS)
def test_the_guard_admits_a_real_archive_path(path):
    assert validate_db_path(path) == path


def test_a_non_string_is_refused_rather_than_crashing():
    for value in (None, 12, ["run.sqlite"]):
        with pytest.raises(IdentifierError):
            validate_db_path(value)


def test_no_admitted_path_can_leave_the_archive_directory():
    """The property the charset exists for, asserted on the built URL.

    Whatever is admitted, the URL the client builds still addresses something
    *under* ``/archived-bots/`` — not its parent, not a sibling, and not the
    prefix-sibling ``/archived-bots-private``.
    """
    from yarl import URL

    for path in LEGIT_PATHS:
        url = URL(f"http://api:8000/archived-bots/{validate_db_path(path)}/summary")
        assert url.path.replace("//", "/").startswith("/archived-bots/")
        assert url.query_string == ""
        assert ".." not in url.path


# ── The routes refuse before a client is ever built ──


class SpyClient:
    """Records every upstream call, so a pivot that got through is visible."""

    def __init__(self):
        self.calls = []
        self.archived_bots = self._ArchivedBots(self)
        self.bot_orchestration = self._Anything(self, "bot_orchestration")

    class _ArchivedBots:
        def __init__(self, outer):
            self._outer = outer

        async def get_database_summary(self, db_path):
            self._outer.calls.append(("get_database_summary", db_path))
            return {"bot_name": "b", "total_trades": 0}

        async def get_database_trades(self, db_path, **kw):
            self._outer.calls.append(("get_database_trades", db_path))
            return {"trades": []}

        async def get_database_executors(self, db_path):
            self._outer.calls.append(("get_database_executors", db_path))
            return {"executors": []}

    class _Anything:
        def __init__(self, outer, name):
            self._outer = outer
            self._name = name

        def __getattr__(self, method):
            async def _call(*args, **kwargs):
                self._outer.calls.append((f"{self._name}.{method}", args, kwargs))
                return []

            return _call


class FakeConfigManager:
    """Access is granted — the point is that the *value* is refused anyway."""

    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        return self._client


def _client(monkeypatch, spy) -> TestClient:
    cm = FakeConfigManager(spy)
    for target in (
        archived_routes,
        cperf_routes,
    ):
        monkeypatch.setattr(target, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)

    app = FastAPI()
    app.include_router(archived_routes.router)
    app.include_router(cperf_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


GUARDED_ROUTES = [
    "/servers/sec591-srv/archived/performance",
    "/servers/sec591-srv/archived/executors",
    "/servers/sec591-srv/archived/controllers",
    "/servers/sec591-srv/archived/report",
]


@pytest.mark.parametrize("route", GUARDED_ROUTES)
@pytest.mark.parametrize("payload", PIVOTS)
def test_route_refuses_the_pivot_and_makes_no_upstream_call(
    monkeypatch, route, payload
):
    spy = SpyClient()

    resp = _client(monkeypatch, spy).get(route, params={"db_path": payload})

    assert resp.status_code == 400, resp.text
    assert spy.calls == [], f"{route} reached the backend with {payload!r}"


@pytest.mark.parametrize("payload", PIVOTS)
def test_run_history_refuses_the_pivot_too(monkeypatch, payload):
    """``/terminated/history`` takes the same value on the same access check."""
    spy = SpyClient()

    resp = _client(monkeypatch, spy).get(
        "/servers/sec591-srv/terminated/history",
        params={"bot_name": "b", "deployed_at": "2026-01-01", "db_path": payload},
    )

    assert resp.status_code == 400, resp.text
    assert spy.calls == [], f"run history reached the backend with {payload!r}"


def test_run_history_without_a_db_path_is_still_allowed(monkeypatch):
    """The parameter is optional; omitting it must not become a 400."""
    spy = SpyClient()

    resp = _client(monkeypatch, spy).get(
        "/servers/sec591-srv/terminated/history",
        params={"bot_name": "b", "deployed_at": "2026-01-01"},
    )

    assert resp.status_code != 400, resp.text


@pytest.mark.parametrize("path", LEGIT_PATHS)
def test_a_real_path_still_reaches_the_backend(monkeypatch, path):
    """The guard must not break the ordinary read."""
    spy = SpyClient()

    resp = _client(monkeypatch, spy).get(
        "/servers/sec591-srv/archived/performance", params={"db_path": path}
    )

    assert resp.status_code == 200, resp.text
    assert ("get_database_summary", path) in spy.calls


def test_the_rejection_is_not_swallowed_by_the_route_try_block(monkeypatch):
    """The guard runs before the try that turns upstream failures into 404/502.

    If it moved inside, a pivot would come back as one of those instead of a
    400, and — worse — would already have been sent upstream.
    """
    spy = SpyClient()

    resp = _client(monkeypatch, spy).get(
        "/servers/sec591-srv/archived/performance",
        params={"db_path": "../accounts/master_account/credentials?"},
    )

    assert resp.status_code == 400
    assert spy.calls == []


def test_the_guard_is_reachable_from_the_fetcher_module_too():
    """Sanity: the helper lives beside ``validate_identifier``, one shape."""
    from condor.fetchers import _identifiers

    assert hasattr(_identifiers, "validate_identifier")
    assert asyncio.iscoroutinefunction(validate_db_path) is False
