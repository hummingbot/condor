"""The Gateway client: one address, and surfpool told from a node.

Two rules. Every Gateway call goes through the server's own hummingbot-api, so
there is no second address to keep in step with the one the bots trade
through — the test for that is that the client sends to the passthrough and
nowhere else. And the chain behind Gateway's RPC is classified from what the
RPC answers, verified 2026-09-17 against surfpool 1.5.0 and a QuickNode
mainnet endpoint.
"""

import pytest

from condor.gateway_client import (
    PROXY_PREFIX,
    GatewayClient,
    classify_rpc,
    host_of,
)

SURFPOOL_VERSION = {
    "jsonrpc": "2.0",
    "result": {"surfnet-version": "1.5.0", "solana-core": "4.1.2", "feature-set": 3345198602},
    "id": 1,
}
SURFPOOL_INFO = {"jsonrpc": "2.0", "result": {"context": {"slot": 1}, "value": {}}, "id": 1}
NODE_VERSION = {"jsonrpc": "2.0", "result": {"solana-core": "2.2.7", "feature-set": 1}, "id": 1}
METHOD_NOT_FOUND = {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": 1}
QUICKNODE_REFUSES = {
    "jsonrpc": "2.0",
    "id": 1,
    "error": {"code": -32601, "message": "the method getVersion does not exist/is not available"},
}


class FakeGatewayRouter:
    """The hummingbot-api client's gateway router, recording where it was sent."""

    def __init__(self):
        self.calls = []

    async def _get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"rpcUrl": "http://127.0.0.1:8899"}

    async def _post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return {"signature": "sig"}


class FakeClient:
    def __init__(self):
        self.gateway = FakeGatewayRouter()


class FakeCM:
    """A config manager whose every server has a client, and no Gateway URL of
    its own — because there is no such setting any more."""

    def __init__(self, clients=None):
        self.clients = clients if clients is not None else {}

    async def get_client(self, name):
        if name not in self.clients:
            raise ValueError(f"Server '{name}' not found")
        return self.clients[name]


def test_surfpool_is_recognised_by_its_version_field():
    info = classify_rpc(SURFPOOL_VERSION, SURFPOOL_INFO)
    assert info == {"kind": "surfpool", "surfnet_version": "1.5.0", "solana_core": "4.1.2"}


def test_surfpool_is_recognised_even_if_only_the_cheatcode_answers():
    # A future surfpool that drops the version field but still serves surfnet_*.
    info = classify_rpc(NODE_VERSION, SURFPOOL_INFO)
    assert info["kind"] == "surfpool"


def test_a_plain_node_is_a_node():
    info = classify_rpc(NODE_VERSION, METHOD_NOT_FOUND)
    assert info == {"kind": "node", "surfnet_version": None, "solana_core": "2.2.7"}


def test_a_provider_that_refuses_getversion_is_still_a_node():
    # QuickNode answers -32601 to getVersion; that is an answer, not surfpool.
    info = classify_rpc(QUICKNODE_REFUSES, QUICKNODE_REFUSES)
    assert info["kind"] == "node"
    assert info["solana_core"] is None


@pytest.mark.asyncio
async def test_every_call_goes_through_the_servers_own_hummingbot_api():
    # The whole reason this client has no URL: there is one address for a
    # server's Gateway, and it is the address its bots already trade through.
    client = FakeClient()
    gw = await GatewayClient.for_server(FakeCM({"vaults": client}), "vaults")
    await gw.solana_status("mainnet-beta")
    await gw.post("/chains/solana/poll", {"signature": "sig"})
    assert client.gateway.calls == [
        ("GET", f"{PROXY_PREFIX}/chains/solana/status", {"network": "mainnet-beta"}),
        ("POST", f"{PROXY_PREFIX}/chains/solana/poll", {"signature": "sig"}),
    ]


@pytest.mark.asyncio
async def test_an_unknown_server_is_the_config_managers_refusal():
    with pytest.raises(ValueError) as exc:
        await GatewayClient.for_server(FakeCM(), "ghost")
    assert "ghost" in str(exc.value)


@pytest.mark.asyncio
async def test_a_gateway_that_names_no_rpc_is_an_error_not_a_guess():
    client = FakeClient()

    async def no_rpc(path, params=None):
        return {"rpcProvider": "url"}

    client.gateway._get = no_rpc
    gw = await GatewayClient.for_server(FakeCM({"vaults": client}), "vaults")
    with pytest.raises(RuntimeError) as exc:
        await gw.solana_rpc_url()
    assert "rpcProvider url" in str(exc.value)


def test_host_of_never_returns_a_path_or_key():
    # A keyed provider URL: the key is the path. Only the host may leave the server.
    # The key is the path, so the vector carries a made-up one: a real provider
    # URL in a test file is a credential in the repo.
    assert host_of("https://x.quiknode.pro/0000000000000000000000000000000000000000/") == "x.quiknode.pro"
    assert host_of("http://127.0.0.1:8899") == "127.0.0.1:8899"
    assert host_of("https://user:secret@rpc.example.com:443/v1?key=abc") == "rpc.example.com:443"
