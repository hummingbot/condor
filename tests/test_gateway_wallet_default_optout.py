"""Importing a wallet with "set as default" unticked must leave the default alone.

Gateway's ``add-wallet`` promotes every newly imported key to the chain default,
and the hummingbot client method exposes no flag to turn that off. Unticking the
box only skipped Condor's *explicit* ``set-default`` call, so the implicit one
still switched the user's default wallet under them. The route now captures the
existing default before the import and restores it afterwards.
"""

import asyncio

import pytest

import condor.web.routes.settings as settings_module
from condor.web.models import GatewayWalletAddRequest, WebUser
from config_manager import ServerPermission

_USER = WebUser(id=1, role="admin")

OLD = "0xold"
NEW = "0xnew"


class FakeAccounts:
    def __init__(self, wallets):
        self._wallets = wallets
        self.set_default_calls = []

    async def list_gateway_wallets(self):
        return self._wallets

    async def add_gateway_wallet(self, chain, private_key):
        # Gateway's own behaviour: the new key becomes the chain default.
        for group in self._wallets:
            if group["chain"] == chain:
                group["walletAddresses"].append(NEW)
                group["default_address"] = NEW
                break
        else:
            self._wallets.append(
                {"chain": chain, "walletAddresses": [NEW], "default_address": NEW}
            )
        return {"address": NEW}

    async def set_default_gateway_wallet(self, chain, address):
        self.set_default_calls.append((chain, address))
        for group in self._wallets:
            if group["chain"] == chain:
                group["default_address"] = address
        return {"default": True}


class FakeClient:
    def __init__(self, wallets):
        self.accounts = FakeAccounts(wallets)


class FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, *_a, **_kw):
        return True

    def get_server_permission(self, *_a, **_kw):
        # SEC-166 made wallet import owner-only; this fixture is the owner.
        return ServerPermission.OWNER

    def is_admin(self, _user_id):
        return False

    async def get_client(self, _name):
        return self._client


@pytest.fixture
def bind(monkeypatch):
    def _bind(wallets):
        client = FakeClient(wallets)
        monkeypatch.setattr(
            settings_module, "get_config_manager", lambda: FakeCM(client)
        )
        return client

    return _bind


def _add(set_default):
    return asyncio.run(
        settings_module.gateway_wallet_add(
            req=GatewayWalletAddRequest(
                chain="ethereum", private_key="0x" + "1" * 64, set_default=set_default
            ),
            server="srv",
            user=_USER,
        )
    )


def test_untick_restores_the_previous_default(bind):
    client = bind(
        [{"chain": "ethereum", "walletAddresses": [OLD], "default_address": OLD}]
    )

    res = _add(set_default=False)

    assert res["address"] == NEW
    assert res["is_default"] is False
    assert client.accounts.set_default_calls == [("ethereum", OLD)]
    group = client.accounts._wallets[0]
    assert group["default_address"] == OLD
    assert NEW in group["walletAddresses"]


def test_tick_makes_the_new_wallet_default(bind):
    client = bind(
        [{"chain": "ethereum", "walletAddresses": [OLD], "default_address": OLD}]
    )

    res = _add(set_default=True)

    assert res["is_default"] is True
    assert client.accounts.set_default_calls == [("ethereum", NEW)]
    assert client.accounts._wallets[0]["default_address"] == NEW


def test_first_wallet_on_a_chain_stays_default_even_when_unticked(bind):
    """With no previous default there is nothing to restore — Gateway needs one."""
    client = bind([])

    res = _add(set_default=False)

    assert res["is_default"] is True
    assert client.accounts.set_default_calls == []
    assert client.accounts._wallets[0]["default_address"] == NEW


def test_other_chains_defaults_are_not_touched(bind):
    client = bind(
        [
            {"chain": "solana", "walletAddresses": ["Sol1"], "default_address": "Sol1"},
            {"chain": "ethereum", "walletAddresses": [OLD], "default_address": OLD},
        ]
    )

    _add(set_default=False)

    assert client.accounts.set_default_calls == [("ethereum", OLD)]
    assert client.accounts._wallets[0]["default_address"] == "Sol1"
