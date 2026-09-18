"""``PUT /wallet/preferred``: which wallet this account calls its own on a chain.

The route is thin, and what is worth pinning is the boundary rather than the
store (which has its own tests): it answers for the signed-in user and nobody
else, it refuses a source it does not know instead of writing it, and the
answer comes back on ``GET /wallet`` so the browser does not have to remember
what it just sent.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.wallet as wallet_routes
from condor import wallet_store
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from tests.wallet_helpers import attach_wallet as attach

USER = WebUser(id=1, username="runner", first_name="R", role="user")
OTHER = WebUser(id=2, username="other", first_name="O", role="user")


def client(user: WebUser = USER) -> TestClient:
    app = FastAPI()
    app.include_router(wallet_routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_attached_wallet_comes_back_as_the_chosen_one():
    address = attach(USER.id)

    body = client().get("/wallet").json()

    assert body["address"] == address
    assert body["preferred"] == {"solana": "browser"}


def test_a_chain_can_be_pointed_at_gateway():
    attach(USER.id)
    api = client()

    put = api.put("/wallet/preferred", json={"chain": "solana", "source": "gateway"})

    assert put.status_code == 200
    assert put.json()["preferred"] == {"solana": "gateway"}
    assert api.get("/wallet").json()["preferred"] == {"solana": "gateway"}


def test_preferring_a_browser_wallet_there_is_none_of_is_refused():
    response = client().put(
        "/wallet/preferred", json={"chain": "solana", "source": "browser"}
    )

    assert response.status_code == 400
    assert "no browser wallet" in response.json()["detail"]


def test_an_unknown_source_never_reaches_the_store():
    attach(USER.id)

    response = client().put(
        "/wallet/preferred", json={"chain": "solana", "source": "either"}
    )

    # 422 rather than 400: the model itself only admits the two sources, so the
    # request is rejected before any handler can write one.
    assert response.status_code == 422
    assert wallet_store.get_preferred(USER.id) == {"solana": "browser"}


def test_one_user_cannot_set_another_users_preference():
    attach(USER.id)
    attach(OTHER.id)

    client(OTHER).put(
        "/wallet/preferred", json={"chain": "solana", "source": "gateway"}
    )

    assert wallet_store.get_preferred(USER.id) == {"solana": "browser"}
    assert wallet_store.get_preferred(OTHER.id) == {"solana": "gateway"}
