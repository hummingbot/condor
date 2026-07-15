"""SolanaConnector: tx-status, fill parsing, balance — with a faked RPC client.

Signing/sending a real versioned tx is covered by the live e2e; here we verify
the parsing and balance logic that has no network dependency.
"""

import json
import types
from decimal import Decimal

import pytest
from solders.keypair import Keypair
from solders.signature import Signature

from condor.executors.solana import WSOL_MINT, SolanaConnector, TxResult

VALID_SIG = str(Signature.from_bytes(bytes(64)))


def _connector(fake_client, keypair=None):
    c = SolanaConnector.__new__(SolanaConnector)
    c.keypair = keypair or Keypair()
    c.rpc_url = "http://fake"
    c._client = fake_client
    c._decimals = {}
    return c


class _Resp:
    def __init__(self, value):
        self.value = value


def test_status_confirmed_pending_failed():
    async def go(conf_status, err):
        st = types.SimpleNamespace(err=err, confirmation_status=conf_status)
        client = types.SimpleNamespace(
            get_signature_statuses=_async_return(_Resp([st]))
        )
        return await _connector(client).status(VALID_SIG)

    import asyncio
    assert asyncio.run(go("confirmed", None)) == 1
    assert asyncio.run(go("processed", None)) == 0
    assert asyncio.run(go(None, "SomeErr")) == -1


def test_status_unknown_signature_is_pending():
    import asyncio
    client = types.SimpleNamespace(get_signature_statuses=_async_return(_Resp([None])))
    assert asyncio.run(_connector(client).status(VALID_SIG)) == 0


def test_get_transaction_parses_fill_deltas():
    import asyncio
    owner = str(Keypair().pubkey())
    mint = "MemeMint1111111111111111111111111111111111"
    tx = {
        "slot": 42,
        "transaction": {"message": {"accountKeys": [{"pubkey": owner}, {"pubkey": "other"}]}},
        "meta": {
            "err": None,
            "fee": 5000,
            "preBalances": [1_000_000_000, 0],
            "postBalances": [980_000_000, 0],
            "preTokenBalances": [],
            "postTokenBalances": [
                {"mint": mint, "owner": owner, "uiTokenAmount": {"uiAmountString": "1234.5"}},
            ],
        },
    }
    value = types.SimpleNamespace(to_json=lambda: json.dumps(tx))
    client = types.SimpleNamespace(get_transaction=_async_return(_Resp(value)))
    c = _connector(client, keypair=_KeypairWith(owner))
    res = asyncio.run(c.get_transaction(VALID_SIG))
    assert res.confirmed and res.fee_lamports == 5000 and res.slot == 42
    assert res.token_deltas[mint] == Decimal("1234.5")
    assert res.sol_delta_lamports == -20_000_000


def test_balance_sol_uses_lamports():
    import asyncio
    client = types.SimpleNamespace(get_balance=_async_return(_Resp(2_500_000_000)))
    c = _connector(client)
    assert asyncio.run(c.balance()) == Decimal("2.5")
    assert asyncio.run(c.balance(WSOL_MINT)) == Decimal("2.5")


def test_token_decimals_wsol_is_nine():
    import asyncio
    c = _connector(types.SimpleNamespace())
    assert asyncio.run(c.token_decimals(WSOL_MINT)) == 9


# -- helpers ----------------------------------------------------------------

def _async_return(value):
    async def _fn(*a, **k):
        return value
    return _fn


class _KeypairWith:
    def __init__(self, pub):
        self._pub = pub

    def pubkey(self):
        return self._pub
