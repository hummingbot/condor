"""The installed client must actually carry the methods condor calls.

condor pins ``hummingbot-api-client`` to an exact version, and that pin moves as the
client releases. It once resolved a commit from before ``execute_quote`` was added while
``mcp_servers/hummingbot_api/tools/gateway_swap.py`` already called it — an AttributeError
on the first real invocation. Nothing caught it: every other test in this suite hands the
tools a stub client that defines whatever it is asked for, so the stubs answered for a
method the real package did not have.

These assertions run against the resolved dependency, so a lockfile that drifts behind
the surface condor uses fails here instead of in front of a user.
"""

import inspect

import pytest

pytest.importorskip("hummingbot_api_client")

import hummingbot_api_client  # noqa: E402
from hummingbot_api_client import HummingbotAPIClient  # noqa: E402

# Method per router, taken from what condor actually calls.
REQUIRED_SURFACE = {
    "gateway_swap": [
        "execute_quote",
        "execute_swap",
        "get_swap_quote",
        "get_swap_status",
        "search_swaps",
    ],
}

# The same, for the WebSocket classes the package exports at the top level.
# ``condor/web/streams/hummingbot_ws.py`` subscribes through these, and a
# subscription is as easy to lose to a client release as a REST call is.
REQUIRED_WS_SURFACE = {
    "ExecutorsWebSocket": [
        "subscribe_all_bots_status",
    ],
}


@pytest.mark.parametrize(
    "router,method",
    [
        (router, method)
        for router, methods in REQUIRED_SURFACE.items()
        for method in methods
    ],
)
def test_the_installed_client_exposes_what_condor_calls(router, method):
    client = HummingbotAPIClient.__new__(HummingbotAPIClient)
    router_type = type(client).__annotations__.get(router)
    if router_type is None:
        # Routers are attributes built in __init__ rather than annotations on some
        # versions; fall back to the class the package exports for this router.
        module = __import__(f"hummingbot_api_client.routers.{router}", fromlist=["*"])
        router_type = next(
            obj
            for name, obj in vars(module).items()
            if inspect.isclass(obj) and obj.__module__ == module.__name__
        )
    assert hasattr(router_type, method), (
        f"The installed hummingbot-api-client has no {router}.{method}. condor calls it; "
        "the lockfile is resolving a commit from before it was added."
    )


@pytest.mark.parametrize(
    "ws_class,method",
    [
        (ws_class, method)
        for ws_class, methods in REQUIRED_WS_SURFACE.items()
        for method in methods
    ],
)
def test_the_installed_client_exposes_the_subscriptions_condor_makes(ws_class, method):
    cls = getattr(hummingbot_api_client, ws_class, None)
    assert cls is not None, (
        f"The installed hummingbot-api-client exports no {ws_class}. condor opens it "
        "to subscribe; the pin is resolving a version from before it was added."
    )
    assert hasattr(cls, method), (
        f"The installed hummingbot-api-client has no {ws_class}.{method}. condor calls "
        "it; the pin is resolving a version from before it was added."
    )
