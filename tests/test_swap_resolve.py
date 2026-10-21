"""A swap stuck on SUBMITTED: how long, said honestly, and the exit (FEAT-140).

GitHub #278. A swap leaves ``SUBMITTED`` only after a status read *succeeds*, so a
failing Gateway ping, a ``None`` status, an unparseable network or an exception keeps
the row there indefinitely — deliberately, because a poll that could not check the
chain must never age a swap out. During the Agent Builder Cup the interface showed an
hourglass with no duration and no exit, and the only fix was redeploying the agent.

Four claims, and the first two are the ones that hold against *any* server:

1. **Past the threshold, something alerts, and it says how long.** The duration is the
   whole decision: a minute is an ordinary confirmation in flight, forty is stuck.
2. **It reads as unverifiable, never as failed.** Nobody could check. The swap may
   have landed. The word "failed" appears in no condition, no alert and no label —
   asserted literally here, because the way this regresses is somebody reaching for
   the obvious word.
3. **Resolving goes to the server, and Condor records nothing.** Condor does not track
   swap confirmation and has no chain read, so a local status would be exactly the
   invented fact the operator was missing.
4. **A server without the route says so plainly and nothing changes.** Its 404 is a
   capability answer (``condor.fetchers.raw_api``), never an outage.

The repo has no async test setup, so coroutines are driven with ``asyncio.run()``.
"""

import asyncio
import types

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.swap_watch as swap_watch
import condor.web.routes.agents as agents_routes
from condor.swap_watch import (
    DEFAULT_UNVERIFIABLE_AFTER_SECONDS,
    SwapResolveUnsupported,
    SwapWatch,
    describe_duration,
    parse_timestamp,
    reset_watch,
    unverifiable_after_seconds,
    watch,
)
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import ServerPermission

SERVER = "alpha"
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")
TX = "5xKQ5nAaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQq"
NOW = 1_800_000_000.0


def submitted(tx=TX, at=None, **extra):
    """One in-flight swap row, in the shape hapi's search returns."""
    row = {
        "transaction_hash": tx,
        "status": "SUBMITTED",
        "connector": "jupiter/router",
        "network": "solana-mainnet-beta",
        "trading_pair": "SOL-USDC",
        "side": "BUY",
    }
    if at is not None:
        row["timestamp"] = at
    row.update(extra)
    return row


@pytest.fixture(autouse=True)
def _fresh_watch():
    """The watch is process state; a leaked first observation would cross tests."""
    reset_watch()
    yield
    reset_watch()


# ── 1. the duration, and the alert past the threshold ────────────────────────


def test_a_swap_past_the_threshold_alerts_with_how_long_it_has_been_unverifiable():
    book = SwapWatch()
    stuck = submitted(at=NOW - 4000)

    (alert,) = book.alerts([stuck], now=NOW)

    assert alert["condition"] == "unverifiable"
    assert alert["unverifiable_for_seconds"] == pytest.approx(4000.0)
    # The duration is in the message, not only in a field a renderer might drop.
    assert alert["unverifiable_for"] == "1h 6m"
    assert "1h 6m" in alert["message"]


def test_a_swap_below_the_threshold_is_pending_and_does_not_alert():
    """An alert on an ordinary in-flight confirmation trains the operator to
    ignore the alert, which is worse than no alert at all."""
    book = SwapWatch()
    fresh = submitted(at=NOW - 30)

    (row,) = book.conditions([fresh], now=NOW)
    assert row["condition"] == "pending"
    assert row["unverifiable_for"] == "30s"
    assert book.alerts([fresh], now=NOW) == []


def test_a_settled_swap_is_not_a_condition_at_all():
    book = SwapWatch()
    rows = [
        {"transaction_hash": "a", "status": "CONFIRMED", "timestamp": NOW - 9999},
        {"transaction_hash": "b", "status": "FAILED", "timestamp": NOW - 9999},
    ]
    assert book.conditions(rows, now=NOW) == []
    assert book.alerts(rows, now=NOW) == []


def test_the_clock_falls_back_to_condor_s_first_observation():
    """A row with no recorded timestamp still gets a duration — and it is
    labelled ``observed``, because a restart resets it and it can only
    understate the wait."""
    book = SwapWatch()
    row = submitted()  # no timestamp

    book.observe([row], now=NOW - 4000)
    (condition,) = book.conditions([row], now=NOW)

    assert condition["since"] == "observed"
    assert condition["unverifiable_for_seconds"] == pytest.approx(4000.0)
    assert condition["condition"] == "unverifiable"


def test_the_row_s_own_timestamp_wins_over_the_first_observation():
    """It survives a restart and is the honest age of the swap."""
    book = SwapWatch()
    row = submitted(at=NOW - 7200)

    book.observe([row], now=NOW - 10)
    (condition,) = book.conditions([row], now=NOW)

    assert condition["since"] == "submitted"
    assert condition["unverifiable_for_seconds"] == pytest.approx(7200.0)


def test_no_clock_at_all_is_reported_as_unknown_and_never_as_zero():
    """A swap nobody can date is not a swap submitted just now."""
    book = SwapWatch()
    (condition,) = book.conditions([submitted()], now=NOW)

    assert condition["since"] == "unknown"
    assert condition["unverifiable_for_seconds"] is None
    assert condition["unverifiable_for"] == ""
    # And it cannot alert, because there is no duration to report.
    assert book.alerts([submitted()], now=NOW) == []


def test_an_answer_from_the_server_clears_the_fallback_clock():
    book = SwapWatch()
    book.observe([submitted()], now=NOW - 4000)
    book.observe([{"transaction_hash": TX, "status": "CONFIRMED"}], now=NOW)

    # Seen again as SUBMITTED, the clock restarts from now rather than carrying
    # the stale first observation: the server did answer in between.
    book.observe([submitted()], now=NOW)
    (condition,) = book.conditions([submitted()], now=NOW + 5)
    assert condition["unverifiable_for_seconds"] == pytest.approx(5.0)


def test_a_row_missing_from_one_page_is_not_treated_as_settled():
    """``observe`` sees one page of a filtered search, so absence says nothing."""
    book = SwapWatch()
    book.observe([submitted()], now=NOW - 4000)
    book.observe([submitted(tx="other")], now=NOW)

    (condition,) = book.conditions([submitted()], now=NOW)
    assert condition["unverifiable_for_seconds"] == pytest.approx(4000.0)


def test_tracking_is_bounded():
    book = SwapWatch()
    for i in range(swap_watch.MAX_TRACKED_SWAPS + 50):
        book.observe([submitted(tx=f"tx{i}")], now=NOW + i)
    assert len(book._first_seen) <= swap_watch.MAX_TRACKED_SWAPS


def test_the_threshold_is_overridable_and_a_bad_override_does_not_disable_it(
    monkeypatch,
):
    monkeypatch.setenv(swap_watch.UNVERIFIABLE_AFTER_ENV, "60")
    assert unverifiable_after_seconds() == 60.0
    assert SwapWatch().alerts([submitted(at=NOW - 90)], now=NOW)

    for bad in ("nonsense", "0", "-5", ""):
        monkeypatch.setenv(swap_watch.UNVERIFIABLE_AFTER_ENV, bad)
        assert unverifiable_after_seconds() == DEFAULT_UNVERIFIABLE_AFTER_SECONDS


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (45, "45s"),
        (90, "1m"),
        (3600, "1h"),
        (4000, "1h 6m"),
        (172_800, "2d"),
        (180_000, "2d 2h"),
    ],
)
def test_durations_read_at_the_magnitude_the_decision_turns_on(seconds, expected):
    assert describe_duration(seconds) == expected


@pytest.mark.parametrize(
    "value",
    ["2027-01-15T12:00:00Z", "2027-01-15T12:00:00+00:00", "2027-01-15T12:00:00"],
)
def test_every_timestamp_spelling_upstream_has_used_is_read(value):
    """ISO with and without a zone, epoch seconds and epoch milliseconds.

    A naive ISO string is read as UTC, which is what the API serialises; reading
    it as local time would shift the duration by the operator's offset.
    """
    assert parse_timestamp(value) == pytest.approx(1_800_014_400.0)
    assert parse_timestamp(1_800_014_400) == pytest.approx(1_800_014_400.0)
    assert parse_timestamp(1_800_014_400_000) == pytest.approx(1_800_014_400.0)
    assert parse_timestamp("1800014400") == pytest.approx(1_800_014_400.0)


@pytest.mark.parametrize("value", [None, "", "not a date", 0, -1, True, {}])
def test_an_unreadable_timestamp_is_no_timestamp(value):
    assert parse_timestamp(value) is None


# ── 2. unverifiable, not failed ──────────────────────────────────────────────


def test_nothing_about_a_stuck_swap_says_failed():
    """The regression is somebody reaching for the obvious word. A swap nobody
    could check may well have landed; calling it failed is as wrong as calling
    it confirmed."""
    book = SwapWatch()
    (alert,) = book.alerts([submitted(at=NOW - 4000)], now=NOW)

    assert "unverifiable" in alert["message"].lower()
    assert alert["status"] == "SUBMITTED"
    for text in (alert["message"], alert["condition"], alert["unverifiable_for"]):
        assert "fail" not in text.lower(), text
    # And the message says what is actually unknown.
    assert "on-chain" in alert["message"]


def test_the_telegram_label_says_unverifiable_with_the_duration(monkeypatch):
    from handlers.dex.swap import _unverifiable_label

    monkeypatch.setattr(swap_watch.time, "time", lambda: NOW)

    assert _unverifiable_label(submitted(at=NOW - 4000)) == "unverifiable 1h 6m"
    # Below the threshold there is no label at all: the hourglass already says
    # "in flight", and a label on every pending swap means nothing.
    assert _unverifiable_label(submitted(at=NOW - 30)) == ""
    assert _unverifiable_label({"status": "CONFIRMED"}) == ""


def test_the_history_line_carries_the_label(monkeypatch):
    from handlers.dex.swap import _format_compact_swap_line, _format_detailed_swap_line

    monkeypatch.setattr(swap_watch.time, "time", lambda: NOW)
    stuck = submitted(at=NOW - 4000, input_amount=1.0, output_amount=126.0, price=126.0)

    compact = _format_compact_swap_line(stuck)
    detailed = _format_detailed_swap_line(stuck)
    for rendered in (compact, detailed):
        assert "unverifiable" in rendered
        assert "1h 6m" in rendered.replace("\\", "")


def test_the_mcp_search_result_alerts_without_hiding_the_search(monkeypatch):
    from mcp_servers.hummingbot_api.formatters.gateway import (
        format_gateway_swap_result,
    )
    from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    monkeypatch.setattr(swap_watch.time, "time", lambda: NOW)

    class _Router:
        async def search_swaps(self, **kwargs):
            return {"data": [submitted(at=NOW - 4000)]}

    client = types.SimpleNamespace(gateway_swap=_Router())
    result = asyncio.run(
        manage_gateway_swaps(client, GatewaySwapRequest(action="search"))
    )

    assert [row["condition"] for row in result["alerts"]] == ["unverifiable"]
    rendered = format_gateway_swap_result("search", result)
    assert "Unverifiable swaps (1)" in rendered
    assert "1h 6m" in rendered
    # The caller asked for a search and still gets the search.
    assert "SOL-USDC" in rendered


def test_a_status_read_answers_how_long_nobody_has_been_able_to_confirm(monkeypatch):
    from mcp_servers.hummingbot_api.formatters.gateway import (
        format_gateway_swap_result,
    )
    from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    monkeypatch.setattr(swap_watch.time, "time", lambda: NOW)

    class _Router:
        async def get_swap_status(self, tx_hash):
            return {"status": "SUBMITTED", "timestamp": NOW - 4000}

    client = types.SimpleNamespace(gateway_swap=_Router())
    result = asyncio.run(
        manage_gateway_swaps(
            client, GatewaySwapRequest(action="get_status", transaction_hash=TX)
        )
    )

    assert result["condition"]["unverifiable_for"] == "1h 6m"
    rendered = format_gateway_swap_result("get_status", result)
    assert "unverifiable for 1h 6m" in rendered
    assert "fail" not in rendered.lower()


# ── 3 & 4. the exit: the server decides, or says it cannot ───────────────────


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = {} if body is None else body
        # A real ``request_info`` carries the backend URL, which is exactly what
        # the redaction rule (SEC-116) exists to keep out of a response. The
        # double has to carry one too, or the leak could not be tested at all.
        self.request_info = types.SimpleNamespace(
            real_url="http://api:8000/gateway/swaps/x/resolve"
        )
        self.history = ()
        self.headers = {}

    @property
    def ok(self):
        return 200 <= self.status < 400

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Answers every POST from one canned response and records the calls."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    get = post
    put = post


def fake_client(response):
    session = FakeSession(response)
    client = types.SimpleNamespace(
        bot_orchestration=types.SimpleNamespace(
            session=session, base_url="http://api:8000"
        )
    )
    return client, session


def test_resolve_asks_the_server_and_returns_the_server_s_answer():
    client, session = fake_client(
        FakeResponse(200, {"status": "CONFIRMED", "resolved_by": "chain_read"})
    )

    answered = asyncio.run(swap_watch.resolve(client, TX))

    url, kwargs = session.calls[0]
    assert url == f"http://api:8000/gateway/swaps/{TX}/resolve"
    assert "json" not in kwargs, "Condor must send no state of its own"
    assert answered == {
        "transaction_hash": TX,
        "result": {"status": "CONFIRMED", "resolved_by": "chain_read"},
    }


def test_resolve_sets_no_swap_state_of_its_own():
    """Condor does not track swap confirmation and has no chain read. The only
    thing it may touch is its own first-observation bookkeeping."""
    client, _ = fake_client(FakeResponse(200, {"status": "FAILED"}))
    book = watch()
    book.observe([submitted()], now=NOW - 4000)

    answered = asyncio.run(swap_watch.resolve(client, TX))

    # The server said FAILED; that answer is passed through untouched and no
    # Condor-side status appears beside it.
    assert answered["result"] == {"status": "FAILED"}
    assert set(answered) == {"transaction_hash", "result"}
    # Its own clock for that hash is spent — the server has now answered.
    assert TX not in book._first_seen


def test_a_server_without_the_route_is_a_capability_answer_not_an_outage():
    client, _ = fake_client(FakeResponse(404, {"detail": "Not Found"}))

    with pytest.raises(SwapResolveUnsupported):
        asyncio.run(swap_watch.resolve(client, TX))


def test_an_unreachable_client_shape_lands_on_the_same_branch():
    with pytest.raises(SwapResolveUnsupported):
        asyncio.run(swap_watch.resolve(types.SimpleNamespace(), TX))


def test_a_real_upstream_failure_keeps_its_http_identity():
    """A 500 is not "this server is too old"; conflating them would have the
    operator upgrading an API that is simply broken."""
    import aiohttp

    client, _ = fake_client(FakeResponse(500, {"detail": "boom"}))

    with pytest.raises(aiohttp.ClientResponseError):
        asyncio.run(swap_watch.resolve(client, TX))


def test_resolve_refuses_an_empty_hash():
    client, session = fake_client(FakeResponse(200, {}))
    with pytest.raises(ValueError):
        asyncio.run(swap_watch.resolve(client, "  "))
    assert session.calls == []


def test_the_mcp_tool_reports_an_unsupported_server_plainly_and_changes_nothing():
    from mcp_servers.hummingbot_api.formatters.gateway import (
        format_gateway_swap_result,
    )
    from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    client, _ = fake_client(FakeResponse(404, {"detail": "Not Found"}))
    book = watch()
    book.observe([submitted()], now=NOW - 4000)

    result = asyncio.run(
        manage_gateway_swaps(
            client, GatewaySwapRequest(action="resolve", transaction_hash=TX)
        )
    )

    assert result["supported"] is False
    assert "does not offer a resolve route" in result["detail"]
    # Nothing local changed: the swap is still being watched as unverifiable.
    assert TX in book._first_seen
    rendered = format_gateway_swap_result("resolve", result)
    assert "unavailable" in rendered
    assert "SUBMITTED and unverifiable" in rendered


def test_the_mcp_tool_hands_back_the_server_s_answer_when_the_route_exists():
    from mcp_servers.hummingbot_api.formatters.gateway import (
        format_gateway_swap_result,
    )
    from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    client, _ = fake_client(FakeResponse(200, {"status": "CONFIRMED"}))
    result = asyncio.run(
        manage_gateway_swaps(
            client, GatewaySwapRequest(action="resolve", transaction_hash=TX)
        )
    )

    assert result["supported"] is True
    assert result["result"] == {"status": "CONFIRMED"}
    assert "Condor recorded nothing" in format_gateway_swap_result("resolve", result)


def test_resolve_is_registered_as_its_own_mcp_tool_and_is_not_gated():
    """Same shape as the rest of the swap family (FEAT-064): one name per verb,
    and the gate reads the name. ``resolve_swap`` signs nothing, so putting a
    human in front of the only exit from a stuck swap would be a brake that
    does not work for an unattended seat."""
    import mcp_servers.hummingbot_api.server as server
    from condor.runtime.danger import DANGEROUS_TOOLS, is_dangerous_tool_call

    assert hasattr(server, "resolve_swap")
    assert "resolve_swap" not in DANGEROUS_TOOLS
    assert not is_dangerous_tool_call(
        {"tool": "mcp__mcp-hummingbot__resolve_swap", "input": {}}
    )


# ── the dashboard half ───────────────────────────────────────────────────────


class FakeConfigManager:
    def __init__(self, client):
        self._client = client

    def get_server_permission(self, user_id, server_name):
        return ServerPermission.TRADER if server_name == SERVER else None

    def has_server_access(self, user_id, server_name, min_permission=None):
        return server_name == SERVER

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        return self._client


def web(monkeypatch, client):
    cm = FakeConfigManager(client)
    monkeypatch.setattr("config_manager.get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(agents_routes.router)
    app.dependency_overrides[get_current_user] = lambda: TRADER
    return TestClient(app)


def test_the_dashboard_lists_stuck_swaps_with_their_duration(monkeypatch):
    monkeypatch.setattr(swap_watch.time, "time", lambda: NOW)

    class _Router:
        def __init__(self):
            self.kwargs = None

        async def search_swaps(self, **kwargs):
            self.kwargs = kwargs
            return {"data": [submitted(at=NOW - 4000), submitted(tx="b", at=NOW - 30)]}

    router = _Router()
    client = types.SimpleNamespace(gateway_swap=router)

    body = (
        web(monkeypatch, client)
        .get("/agents/stuck-swaps", params={"server_name": SERVER})
        .json()
    )

    assert router.kwargs["status"] == "SUBMITTED"
    assert [row["condition"] for row in body["swaps"]] == ["unverifiable", "pending"]
    assert [row["unverifiable_for"] for row in body["swaps"]] == ["1h 6m", "30s"]
    assert len(body["alerts"]) == 1
    assert "1h 6m" in body["alerts"][0]["message"]
    assert body["threshold_seconds"] == DEFAULT_UNVERIFIABLE_AFTER_SECONDS


def test_an_unreachable_server_never_reads_as_no_stuck_swaps(monkeypatch):
    """The whole failure mode is a server that cannot be asked. Answering it
    with an empty list is the lie this view exists to stop telling.

    The hint comes from ``describe_executor_error``, so it names the failure
    without echoing ``str(exc)`` — an aiohttp error stringifies with the
    backend's host and port in it (SEC-116).
    """
    import aiohttp

    class _Router:
        async def search_swaps(self, **kwargs):
            raise aiohttp.ClientConnectorError(
                types.SimpleNamespace(ssl=None, host="api", port=8000),
                OSError("connection refused"),
            )

    client = types.SimpleNamespace(gateway_swap=_Router())
    body = (
        web(monkeypatch, client)
        .get("/agents/stuck-swaps", params={"server_name": SERVER})
        .json()
    )

    assert body["server_online"] is False
    assert "unreachable" in body["error_hint"]
    assert "api:8000" not in body["error_hint"]
    assert body["swaps"] == [] and body["alerts"] == []


def test_the_dashboard_resolve_returns_the_server_s_answer(monkeypatch):
    client, session = fake_client(FakeResponse(200, {"status": "CONFIRMED"}))
    client.gateway_swap = types.SimpleNamespace()

    body = (
        web(monkeypatch, client)
        .post(f"/agents/stuck-swaps/{TX}/resolve", params={"server_name": SERVER})
        .json()
    )

    assert body["supported"] is True
    assert body["result"] == {"status": "CONFIRMED"}
    assert session.calls[0][0].endswith(f"/gateway/swaps/{TX}/resolve")


def test_the_dashboard_resolve_says_plainly_when_the_server_has_no_route(monkeypatch):
    client, _ = fake_client(FakeResponse(404, {"detail": "Not Found"}))
    client.gateway_swap = types.SimpleNamespace()
    book = watch()
    book.observe([submitted()], now=NOW - 4000)

    resp = web(monkeypatch, client).post(
        f"/agents/stuck-swaps/{TX}/resolve", params={"server_name": SERVER}
    )
    body = resp.json()

    # Not a 502 and not a 404: the request was fine and the server is up.
    assert resp.status_code == 200
    assert body["supported"] is False
    assert "does not offer a swap resolve route" in body["error_hint"]
    assert "SUBMITTED" in body["error_hint"]
    assert body["result"] == {}
    # And nothing local changed.
    assert TX in book._first_seen


def test_the_dashboard_resolve_keeps_a_real_failure_as_a_failure(monkeypatch):
    client, _ = fake_client(FakeResponse(500, {"detail": "boom"}))
    client.gateway_swap = types.SimpleNamespace()

    resp = web(monkeypatch, client).post(
        f"/agents/stuck-swaps/{TX}/resolve", params={"server_name": SERVER}
    )
    assert resp.status_code == 502
    # The API's own message, never ``str(exc)``: an aiohttp error stringifies
    # with the backend's host and port in it (SEC-116).
    assert "boom" in resp.json()["detail"]
    assert "api:8000" not in resp.json()["detail"]


def test_a_server_the_caller_cannot_reach_is_refused(monkeypatch):
    client, _ = fake_client(FakeResponse(200, {}))
    client.gateway_swap = types.SimpleNamespace()
    app = web(monkeypatch, client)

    assert (
        app.get("/agents/stuck-swaps", params={"server_name": "other"}).status_code
        == 403
    )
    assert (
        app.post(
            f"/agents/stuck-swaps/{TX}/resolve", params={"server_name": "other"}
        ).status_code
        == 403
    )


# ── the runbook ──────────────────────────────────────────────────────────────


def test_the_runbook_names_the_stuck_swap_recovery_steps():
    """#278's third proposal, and the part that would have saved the race time
    on its own: the operator did not know there was anything to do but redeploy."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    body = (repo_root / "agents/_shared/skills/operate_your_loop/SKILL.md").read_text()
    section = body[body.index("## 6.") :]

    assert "SUBMITTED" in section
    assert "resolve_swap" in section
    assert "unverifiable" in section.lower()
    # It must say the two things a reader could otherwise get wrong.
    assert "not failed" in section.lower() or "unverifiable, not failed" in section
    assert "redeploy" in section, "the runbook must rule out the redeploy"
    assert "explorer" in section, "read the chain before claiming an outcome"
