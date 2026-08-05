"""Route-order regression tests for the agents API router (CORR-061).

Starlette matches routes in registration order, so literal paths like
``/agents/delegations`` must be registered before the ``/agents/{slug}``
catch-all or they become unreachable (matched as ``slug="delegations"``).
"""

from starlette.routing import Match

from condor.web.routes.agents import router


def _first_full_match(method: str, path: str) -> str | None:
    """Return the endpoint name of the first route that fully matches, mirroring Starlette dispatch."""
    scope = {"type": "http", "method": method, "path": path, "path_params": {}}
    for route in router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route.endpoint.__name__
    return None


def test_delegations_list_not_shadowed_by_slug_catch_all():
    assert _first_full_match("GET", "/agents/delegations") == "list_delegations"


def test_delegation_detail_and_stop_not_shadowed():
    assert _first_full_match("GET", "/agents/delegations/t1") == "get_delegation_status"
    assert (
        _first_full_match("POST", "/agents/delegations/t1/stop")
        == "stop_delegation_route"
    )
    assert (
        _first_full_match("GET", "/agents/delegations/t1/events")
        == "get_delegation_events"
    )


def test_agent_detail_still_matches_real_slug():
    assert _first_full_match("GET", "/agents/my-agent") == "get_agent"
