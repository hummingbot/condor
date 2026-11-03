"""One access-checked server resolution, shared by /portfolio, /bots and /trade.

``handlers.portfolio._resolve_server_for_user`` and the block inside
``handlers.bots._shared.get_bots_client`` were hand-synced copies of the same
access-control decision (plus two more copies in ``handlers/cex/trade.py``), and
they had already drifted: the portfolio copy refused a request with no
``_user_id`` while the other three fell back to *every* enabled server on the
install -- exactly the CORR-246 leak, reachable again through /bots and /trade.

They now all go through ``resolve_accessible_server``. This pins the resolution
on the shared seam and through both entry points, so the next divergence is a
test failure instead of a stranger's balance sheet.
"""

import asyncio

import pytest

from handlers import portfolio
from handlers.bots import _shared as bots_shared

MINE = "mine-prod"
THEIRS = "someone-else"
USER_ID = 555
STRANGER_ID = 999


class FakeConfigManager:
    """Two enabled servers; the user can reach only what ``accessible`` says."""

    def __init__(self, accessible, servers=None):
        self._accessible = accessible
        # THEIRS first, so an unfiltered "first enabled server" fallback lands
        # on the server the user must never see.
        self._servers = (
            servers
            if servers is not None
            else {THEIRS: {"enabled": True}, MINE: {"enabled": True}}
        )
        self.clients_for = []

    def list_servers(self):
        return dict(self._servers)

    def get_accessible_servers(self, user_id):
        return list(self._accessible) if user_id == USER_ID else []

    async def get_client(self, name):
        self.clients_for.append(name)
        return f"client::{name}"


@pytest.fixture
def cm(monkeypatch):
    def _install(accessible, servers=None):
        fake = FakeConfigManager(accessible, servers)
        monkeypatch.setattr("config_manager.get_config_manager", lambda: fake)
        return fake

    return _install


def _user_data(active_server=None, user_id=USER_ID):
    user_data = {}
    if user_id is not None:
        user_data["_user_id"] = user_id
    if active_server is not None:
        from condor.preferences import set_active_server

        set_active_server(user_data, active_server)
    return user_data


class _Context:
    def __init__(self, user_data):
        self.user_data = user_data


# ============================================
# THE SHARED SEAM
# ============================================


def test_stale_preference_for_a_foreign_server_falls_back_to_an_accessible_one(cm):
    cm([MINE])

    resolved = bots_shared.resolve_accessible_server(_user_data(active_server=THEIRS))

    assert resolved == MINE


def test_accessible_preference_is_honoured(cm):
    cm([MINE, THEIRS])

    assert (
        bots_shared.resolve_accessible_server(_user_data(active_server=THEIRS))
        == THEIRS
    )


def test_a_user_with_no_shared_server_is_refused(cm):
    cm([])

    with pytest.raises(ValueError, match="No accessible API servers"):
        bots_shared.resolve_accessible_server(_user_data())


def test_a_disabled_server_is_never_a_candidate(cm):
    cm([MINE], servers={MINE: {"enabled": False}, THEIRS: {"enabled": True}})

    with pytest.raises(ValueError, match="No accessible API servers"):
        bots_shared.resolve_accessible_server(_user_data())


def test_missing_user_id_is_refused_rather_than_served_any_server(cm):
    cm([MINE, THEIRS])

    with pytest.raises(ValueError, match="No accessible API servers"):
        bots_shared.resolve_accessible_server(_user_data(user_id=None))


def test_no_user_data_at_all_is_refused(cm):
    cm([MINE, THEIRS])

    with pytest.raises(ValueError, match="No accessible API servers"):
        bots_shared.resolve_accessible_server(None)


def test_a_stale_access_grant_for_a_deleted_server_is_not_a_candidate(cm):
    """``server_access`` outliving ``servers`` must not name a phantom server."""
    cm(["deleted-server", MINE], servers={MINE: {"enabled": True}})

    assert bots_shared.resolve_accessible_server(_user_data()) == MINE


def test_or_none_variant_returns_none_instead_of_raising(cm):
    cm([])

    assert bots_shared.resolve_accessible_server_or_none(_user_data()) is None


def test_or_none_variant_still_refuses_a_foreign_server(cm):
    cm([MINE])

    resolved = bots_shared.resolve_accessible_server_or_none(
        _user_data(active_server=THEIRS)
    )

    assert resolved == MINE


# ============================================
# BOTH ENTRY POINTS AGREE
# ============================================


def test_get_bots_client_gives_a_stale_preference_holder_their_own_server(cm):
    fake = cm([MINE])

    client, name = asyncio.run(
        bots_shared.get_bots_client(12345, _user_data(active_server=THEIRS))
    )

    assert name == MINE
    assert client == f"client::{MINE}"
    assert fake.clients_for == [MINE], "no client is ever built for a foreign server"


def test_portfolio_gives_a_stale_preference_holder_the_same_server(cm):
    cm([MINE])

    resolved = portfolio._resolve_server_for_user(
        _Context(_user_data(active_server=THEIRS))
    )

    assert resolved == MINE


def test_get_bots_client_without_user_data_no_longer_serves_every_server(cm):
    """The legacy all-servers fallback is gone; /bots now fails closed too."""
    fake = cm([MINE, THEIRS])

    with pytest.raises(ValueError, match="No accessible API servers"):
        asyncio.run(bots_shared.get_bots_client(12345))

    assert fake.clients_for == []


def test_get_bots_client_refuses_a_stranger_with_no_grants(cm):
    fake = cm([MINE, THEIRS])
    stranger = {"_user_id": STRANGER_ID}

    with pytest.raises(ValueError, match="No accessible API servers"):
        asyncio.run(bots_shared.get_bots_client(12345, stranger))

    assert fake.clients_for == []


def test_the_refusal_message_is_defined_once(cm):
    assert portfolio.NO_ACCESSIBLE_SERVERS is bots_shared.NO_ACCESSIBLE_SERVERS
