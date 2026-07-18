"""Route-order regression tests for the agents API router (CORR-061).

Starlette matches routes in registration order, so literal paths like
``/agents/runs/...`` must be registered before the ``/agents/{slug}``
catch-all or they become unreachable (matched as ``slug="runs"``).
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


def test_delegation_routes_are_gone():
    """Delegations were removed from the product — no route may resurrect
    the path (a match would be the /{slug} catch-all misfiring)."""
    assert _first_full_match("GET", "/agents/delegations") == "get_agent"


def test_run_routes_not_shadowed():
    rid = "01JZZZZZZZZZZZZZZZZZZZZZZZ"
    assert _first_full_match("GET", f"/agents/runs/{rid}") == "get_run_route"
    assert _first_full_match("GET", f"/agents/runs/{rid}/export") == "export_run_route"
    assert (
        _first_full_match("GET", f"/agents/runs/{rid}/executors")
        == "get_run_executors"
    )


def test_agent_detail_still_matches_real_slug():
    assert _first_full_match("GET", "/agents/my-agent") == "get_agent"


def test_agent_runs_list_matches_slug_route():
    assert _first_full_match("GET", "/agents/my-agent/runs") == "list_agent_runs"
