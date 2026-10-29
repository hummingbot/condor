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


def test_history_not_shadowed_by_the_task_id_route():
    """``/delegations/history`` is a literal, and `{task_id}` would swallow it."""
    assert (
        _first_full_match("GET", "/agents/delegations/history")
        == "list_delegation_history"
    )


def test_notify_is_not_shadowed_by_a_slug_route():
    """``/agents/notify`` is a literal too (ARCH-088)."""
    assert _first_full_match("POST", "/agents/notify") == "notify_user"


def test_agent_detail_still_matches_real_slug():
    assert _first_full_match("GET", "/agents/my-agent") == "get_agent"


def test_import_is_a_literal_not_an_agent_slug():
    """``/agents/import`` and its preview are literals (FEAT-133)."""
    assert _first_full_match("POST", "/agents/import") == "import_agent"
    assert _first_full_match("POST", "/agents/import/preview") == "preview_agent_import"


def test_export_still_hangs_off_a_real_slug():
    assert _first_full_match("GET", "/agents/my-agent/bundle") == "export_agent_bundle"


def test_update_hangs_off_a_real_slug_and_its_preview_is_not_shadowed():
    """``/{slug}/update`` and ``/{slug}/update/preview`` (FEAT-145)."""
    assert (
        _first_full_match("POST", "/agents/my-agent/update")
        == "update_agent_from_bundle"
    )
    assert (
        _first_full_match("POST", "/agents/my-agent/update/preview")
        == "preview_agent_update"
    )


def _app_match(method: str, path: str) -> str | None:
    """The same dispatch question, asked of the whole app rather than one router.

    ``_first_full_match`` above sees the router before it is mounted, so it
    cannot tell whether the dashboard's URL — ``/api/v1/hub/catalogue`` — is
    served at all. That gap is not hypothetical: every backend hub test mounts
    ``hub_router`` bare and calls ``/hub/catalogue``, while the frontend's tests
    mock ``/api/v1/hub/catalogue``, so a wrong prefix on either side passes both
    suites and only shows up in a browser as the SPA fallback's
    ``No such API route``.
    """
    from starlette.routing import Match

    from condor.web.app import create_app

    scope = {"type": "http", "method": method, "path": path, "path_params": {}}
    for route in create_app().routes:
        match, _ = route.matches(scope)
        if match == Match.FULL and getattr(route, "endpoint", None) is not None:
            return route.endpoint.__name__
    return None


def test_hub_routes_answer_at_the_paths_the_dashboard_requests():
    """Every hub URL ``frontend/src/lib/api.ts`` builds must be registered.

    The literals below are the ones that file requests, prefix included. Change
    one side without the other and this fails instead of the Hub view 404ing.
    """
    assert (
        _app_match("GET", "/api/v1/hub/catalogue") == "browse_hub_catalogue"
    ), "the Hub view's catalogue browser (api.ts browseHub)"
    assert _app_match("GET", "/api/v1/hub/agents/river_scalper") == "get_hub_listing"
    assert (
        _app_match("POST", "/api/v1/hub/agents/river_scalper/install/preview")
        == "preview_hub_install"
    )
    assert (
        _app_match("POST", "/api/v1/hub/agents/river_scalper/install")
        == "install_from_hub"
    )
    assert _app_match("DELETE", "/api/v1/hub/agents/listing-1") == "unpublish_from_hub"
