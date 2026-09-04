"""The shared performance surface, read from an API that may not have it (FEAT-087).

``GET /performance/history`` serves controllers and executors in one row shape,
and it is unreleased: the branch behind hummingbot/hummingbot-api#226 has it and
the published image does not. So the interesting cases are not the happy path —
they are the three ways a request can fail to produce rows, which must reach the
browser as three different things:

* **404** — this API is older. Not an error, not an offline server; the client
  draws its derived series and says why.
* **400** — a filter aimed at the wrong population. The caller's own mistake,
  and it has to read as one: reported as an offline server it would silently
  route the browser to a fallback and hide the bug.
* **no answer at all** — the server is down, which is a fourth thing again and
  must not be remembered as "this API is old".

Plus the two mappings nothing downstream may re-derive: fees that were never
measured, and a ``POSITION_HOLD`` close whose PnL stays unrealized.
"""

import asyncio

import aiohttp
import pytest

from condor.fetchers.performance_history import (
    PerformanceHistoryUnsupported,
    extract_rows,
    fetch_performance_history,
    probe_performance_history,
    reject_foreign_filters,
)
from condor.web.models import PerformanceSnapshot

# ── Doubles ──


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else {"status": "success", "data": []}
        self.request_info = None
        self.history = ()
        self.headers = {}

    @property
    def ok(self):
        return 200 <= self.status < 400

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records the one GET it is asked for and answers with a canned response."""

    def __init__(self, response=None, raises=None):
        self.response = response or FakeResponse()
        self.raises = raises
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.response


class FakeClient:
    def __init__(self, session=None, base_url="http://api:8000"):
        self.bot_orchestration = (
            type("R", (), {"session": session, "base_url": base_url})()
            if session is not None
            else type("R", (), {})()
        )


ROW = {
    "timestamp": "2026-09-01T20:52:14.404309+00:00",
    "subject": "executor",
    "scope_id": "exec-1",
    "status": "RUNNING",
    "is_terminal": False,
    "realized_pnl_quote": 1.5,
    "unrealized_pnl_quote": 2.5,
    "global_pnl_quote": 4.0,
    "global_pnl_pct": 0.02,
    "volume_quote": 1200.0,
    "cum_fees_quote": 0.35,
    "bot_name": None,
    "controller_id": "main",
    "executor_id": "exec-1",
    "executor_type": "position_executor",
    "account_name": "master_account",
    "connector_name": "lighter",
    "trading_pair": "ETH-USDC",
    "close_type": None,
    "performance": {},
    "custom_info": {},
}


# ── The request ──


def test_request_carries_subject_and_drops_absent_filters():
    session = FakeSession(FakeResponse(body={"status": "success", "data": [ROW]}))
    client = FakeClient(session)

    asyncio.run(
        fetch_performance_history(
            client, subject="executor", executor_id="exec-1", controller_id=None
        )
    )

    url, kwargs = session.calls[0]
    assert url == "http://api:8000/performance/history"
    params = kwargs["params"]
    assert params["subject"] == "executor"
    assert params["executor_id"] == "exec-1"
    # An empty controller_id is a filter for a controller literally named "",
    # not the absence of one — so an absent filter is absent from the query.
    assert "controller_id" not in params


def test_limit_is_clamped_to_the_route_ceiling():
    # Upstream declares le=1000; asking for more is a 422, which is exactly the
    # trap the controller history route fell into (CORR-260).
    session = FakeSession()
    asyncio.run(
        fetch_performance_history(FakeClient(session), subject="controller", limit=5000)
    )
    assert session.calls[0][1]["params"]["limit"] == 1000


# ── The three failures, kept apart ──


def test_404_is_unsupported_not_an_error():
    session = FakeSession(FakeResponse(status=404, body={"detail": "Not Found"}))
    with pytest.raises(PerformanceHistoryUnsupported):
        asyncio.run(fetch_performance_history(FakeClient(session), subject="executor"))


def test_400_keeps_its_status_so_it_can_be_forwarded():
    # `upstream_error` reads `status`/`message` off the exception and maps a 4xx
    # to a 400. Losing the status here would turn the caller's bad request into
    # a 502 and send the browser to a fallback instead of showing the mistake.
    session = FakeSession(
        FakeResponse(status=400, body={"detail": "bot_name is not a valid filter"})
    )
    with pytest.raises(aiohttp.ClientResponseError) as exc:
        asyncio.run(fetch_performance_history(FakeClient(session), subject="executor"))
    assert exc.value.status == 400
    assert "bot_name" in exc.value.message


def test_a_client_with_no_session_is_unsupported_not_a_crash():
    # A test double, or a future client shape. "Cannot ask" lands on the same
    # fallback a 404 does rather than taking the page down.
    with pytest.raises(PerformanceHistoryUnsupported):
        asyncio.run(fetch_performance_history(FakeClient(None), subject="executor"))


def test_error_detail_never_leaks_the_upstream_body():
    # An upstream error page can be anything, and this string reaches a browser.
    session = FakeSession(FakeResponse(status=500, body=ValueError("not json")))
    with pytest.raises(aiohttp.ClientResponseError) as exc:
        asyncio.run(
            fetch_performance_history(FakeClient(session), subject="controller")
        )
    assert exc.value.message == "the trading API returned HTTP 500"


# ── The probe ──


def test_probe_says_yes_when_the_route_answers():
    session = FakeSession(FakeResponse(body={"status": "success", "data": []}))
    assert asyncio.run(probe_performance_history(FakeClient(session))) == {
        "supported": True
    }


def test_probe_says_no_for_a_404():
    session = FakeSession(FakeResponse(status=404, body={"detail": "Not Found"}))
    result = asyncio.run(probe_performance_history(FakeClient(session)))
    assert result["supported"] is False
    assert not result.get("unknown")


def test_probe_separates_an_old_api_from_an_unreachable_one():
    # A server that was merely down must not have a fallback pinned to it for
    # the whole cache TTL after it comes back, so "could not ask" is its own
    # answer rather than a "no".
    session = FakeSession(raises=aiohttp.ClientError("connection refused"))
    result = asyncio.run(probe_performance_history(FakeClient(session)))
    assert result["supported"] is False
    assert result["unknown"] is True


def test_probe_asks_the_cheapest_possible_question():
    session = FakeSession()
    asyncio.run(probe_performance_history(FakeClient(session)))
    params = session.calls[0][1]["params"]
    assert params["limit"] == 1
    # The controller subject: guaranteed to exist wherever the route does, and
    # the question is whether the route is there at all.
    assert params["subject"] == "controller"


# ── The cross-population rule ──


def test_an_executor_filter_is_rejected_for_the_controller_subject():
    assert "executor_id" in (
        reject_foreign_filters("controller", executor_id="e1") or ""
    )


def test_bot_name_is_rejected_for_the_executor_subject():
    # A bot name is deliberately not a join key between the two populations.
    assert "bot_name" in (reject_foreign_filters("executor", bot_name="bot-a") or "")


def test_controller_id_is_legal_on_both_subjects():
    assert reject_foreign_filters("executor", controller_id="main") is None
    assert reject_foreign_filters("controller", controller_id="main") is None


def test_absent_filters_are_not_offences():
    assert reject_foreign_filters("controller", executor_id=None, bot_name=None) is None


# ── The envelope and the row ──


def test_rows_are_read_out_of_the_data_envelope():
    assert extract_rows({"status": "success", "data": [ROW]}) == [ROW]
    assert extract_rows({"snapshots": [ROW]}) == [ROW]
    assert extract_rows([ROW]) == [ROW]
    assert extract_rows(None) == []


def test_snapshot_maps_volume_quote_not_volume_traded():
    # The two routes spell volume differently, and reading the controller
    # route's name off this payload would silently make every volume zero.
    assert PerformanceSnapshot.from_raw(ROW).volume_quote == 1200.0


def test_unmeasured_fees_stay_none_rather_than_becoming_zero():
    # Controllers report null because `PerformanceReport` has no fees field.
    # A fees chart must render "not measured" differently from zero.
    row = {**ROW, "subject": "controller", "cum_fees_quote": None}
    assert PerformanceSnapshot.from_raw(row).cum_fees_quote is None
    # ...and a measured zero survives as a zero.
    assert (
        PerformanceSnapshot.from_raw({**ROW, "cum_fees_quote": 0}).cum_fees_quote == 0.0
    )


def test_position_hold_keeps_its_pnl_unrealized():
    # Upstream makes the realized/unrealized split from settlement, and
    # POSITION_HOLD is the exception: the position was handed to
    # `position_holds`, so counting it as realized double-counts it. The model
    # reads the two numbers and does no arithmetic on them.
    row = {
        **ROW,
        "is_terminal": True,
        "status": "TERMINATED",
        "close_type": "POSITION_HOLD",
        "realized_pnl_quote": 0.0,
        "unrealized_pnl_quote": 42.0,
    }
    snap = PerformanceSnapshot.from_raw(row)
    assert snap.realized_pnl_quote == 0.0
    assert snap.unrealized_pnl_quote == 42.0
    assert snap.close_type == "POSITION_HOLD"


def test_a_terminal_row_is_marked_as_one():
    # It is what makes a closed executor's series a single query: the terminal
    # row *is* the last point, so no reader appends a final value after it.
    assert (
        PerformanceSnapshot.from_raw({**ROW, "is_terminal": True}).is_terminal is True
    )


def test_a_malformed_number_does_not_take_down_the_chart():
    assert (
        PerformanceSnapshot.from_raw(
            {**ROW, "global_pnl_quote": "nonsense"}
        ).global_pnl_quote
        == 0.0
    )


def test_the_controller_only_fields_stay_none_on_an_executor_row():
    # A bot name invented for an executor would become a fabricated join key
    # between the two populations.
    assert PerformanceSnapshot.from_raw(ROW).bot_name is None
