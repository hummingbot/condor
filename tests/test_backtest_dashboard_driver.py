"""FEAT-076: the dashboard submits the routine, so the web route stops driving.

``condor/web/routes/backtesting.py`` is three routes over the local archive and
makes no call to the Hummingbot API at all. Submitting, polling and saving
happen once, in ``backtest_chart`` -- which is what puts the dashboard on the
retry-on-stall poll and the timed-out-result recovery for the first time, and
what makes a dashboard run saved the moment it completes rather than the moment
somebody opens it.
"""

from __future__ import annotations

import condor.web.routes.backtesting as web

# ── The route stopped being an API client ─────────────────────────────────────


def test_the_web_route_makes_no_call_to_the_hummingbot_api():
    source = open(web.__file__).read()
    assert "client.backtesting" not in source
    assert "get_client" not in source


def test_only_the_archive_routes_survive():
    paths = {route.path for route in web.router.routes}
    assert paths == {"/backtesting/archive", "/backtesting/archive/{task_id}"}
    # The submit route's request model went with it.
    assert not hasattr(web, "SubmitBacktestRequest")
