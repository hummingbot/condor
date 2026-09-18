"""A record is dropped only when the chain says the account is not there.

The listing that feeds reconcile is one call through hummingbot-api's proxy,
and an answer that comes back empty is indistinguishable from a chain with no
vaults on it. Believing it deleted the record of a vault that existed, which is
a user's label, strategy and private config gone for a reason that was never
about the chain. So absence now has to be said twice: missing from the listing,
*and* 404 on a direct read.
"""

import aiohttp
import pytest

from condor import vault_store
from condor.web.routes import vaults as routes

ACCOUNT = "4YfP3qnEiNeof88jsBE3LpeiV8M1VFPBtyZejmhQXaer"
CHAIN = {
    "creator": "2LjWzppfJyTfTJT3gPAvmk6wukYUwi8cv1CuXjBHhfGj",
    "treasury": "3xV8qNoHiNCopjSmdqt83c1Gg5VgZTWd36ENZqWf6Vd6",
    "state": "Running",
}


class FakeGateway:
    """Answers the bulk listing one way and the single read another."""

    server = "vaults"

    def __init__(self, listing, single):
        self._listing = listing
        self._single = single
        self.reads = 0

    async def list_vaults(self):
        return self._listing

    async def vault(self, account):
        self.reads += 1
        if isinstance(self._single, Exception):
            raise self._single
        return self._single


def _not_found():
    return aiohttp.ClientResponseError(None, (), status=404, message="no vault")


def _record(user_id, tmp_store):
    return vault_store.create_record(
        user_id,
        account=ACCOUNT,
        label="Demo vault",
        server="vaults",
        network="mainnet-beta",
        vault_id="ab" * 32,
        treasury_address=CHAIN["treasury"],
        creator_address=CHAIN["creator"],
        pending={"config_hash": "00" * 32},
    )


@pytest.fixture
def user(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_store.paths, "user_dir", lambda uid: tmp_path)
    return 1


async def _reconcile(gw, user_id):
    return await routes._reconcile(
        gw, user_id, vault_store.for_server(user_id, "vaults")
    )


@pytest.mark.asyncio
async def test_an_empty_listing_does_not_delete_a_vault_that_answers(user, tmp_path):
    _record(user, tmp_path)
    gw = FakeGateway(listing=[], single={**CHAIN, "account": ACCOUNT})

    kept = await _reconcile(gw, user)

    assert (
        ACCOUNT in kept
    ), "the direct read found it; the empty listing was not evidence"
    assert gw.reads == 1
    assert vault_store.get(user, ACCOUNT) is not None


@pytest.mark.asyncio
async def test_a_read_that_fails_changes_nothing(user, tmp_path):
    _record(user, tmp_path)
    gw = FakeGateway(listing=[], single=RuntimeError("gateway is restarting"))

    kept = await _reconcile(gw, user)

    assert ACCOUNT in kept, "'I could not hear' is not 'it is gone'"
    assert vault_store.get(user, ACCOUNT) is not None


@pytest.mark.asyncio
async def test_a_404_on_the_direct_read_is_absence(user, tmp_path):
    _record(user, tmp_path)
    gw = FakeGateway(listing=[], single=_not_found())

    kept = await _reconcile(gw, user)

    assert ACCOUNT not in kept
    assert (
        vault_store.get(user, ACCOUNT) is None
    ), "the chain said so, so the record goes"


@pytest.mark.asyncio
async def test_a_listed_vault_is_never_read_again(user, tmp_path):
    _record(user, tmp_path)
    gw = FakeGateway(listing=[{**CHAIN, "account": ACCOUNT}], single=_not_found())

    kept = await _reconcile(gw, user)

    assert ACCOUNT in kept
    assert (
        gw.reads == 0
    ), "the listing answered; asking again would be an RPC call per vault"
    assert kept[ACCOUNT]["chain"]["state"] == "Running"
