"""The orphan listing must hand the agent a call it can actually make.

The listing used to say "close the position via the gateway tools (remove liquidity by position
address)" — an instruction with no implementing tool. An agent following it found that
``stop_executor`` was a no-op (the executor is already terminated), ``manage_amm``
does not handle CLMM positions, and ``explore_dex_pools`` is read-only, so the position stayed open.

The listing now emits a concrete ``manage_clmm(action="close", ...)`` call built from the record's
own fields. Two are easy to get wrong and are pinned here:
- ``connector_name`` is the NETWORK for an lp_executor ("solana-mainnet-beta"), so it must land in
  ``network=``, never ``connector=``
- ``lp_provider`` is the DEX, and ``pool_address`` must be forwarded or the close 400s

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio

from mcp_servers.hummingbot_api.tools.executors import list_orphaned_positions

ORPHAN = {
    "executor_id": "HJJUFaSZdThCH6rRv5agcVCZ4widziThm1aRdh2LtcTW",
    "executor_type": "lp_executor",
    "connector_name": "solana-mainnet-beta",
    "trading_pair": "SOL-USDC",
    "lp_provider": "orca/clmm",
    "pool_address": "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
    "close_type": "POSITION_HOLD",
    "closed_at": "2026-08-13T18:04:11+00:00",
    "position_address": "H4vD69DsraHjHyKvRwRPHVGe2aJkvAUaNK5tMif2CiNw",
    "hold_reason": "close_retries_exhausted",
    "needs_onchain_reconciliation": False,
}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, _url):
        return _Response(self._payload)


class _Executors:
    def __init__(self, payload):
        self.session = _Session(payload)
        self.base_url = "http://api"


class _Client:
    def __init__(self, payload):
        self.executors = _Executors(payload)


def _list_orphans(orphans):
    client = _Client({"count": len(orphans), "orphans": orphans})
    return asyncio.run(list_orphaned_positions(client))["formatted_output"]


def test_listing_emits_a_runnable_close_call():
    output = _list_orphans([ORPHAN])

    assert 'manage_clmm(action="close"' in output
    assert 'connector="orca/clmm"' in output
    assert 'network="solana-mainnet-beta"' in output
    assert f'position_address="{ORPHAN["position_address"]}"' in output
    assert f'pool_address="{ORPHAN["pool_address"]}"' in output


def test_network_is_not_passed_as_the_connector():
    """connector_name holds the network for an lp_executor — mixing them up produces a dead call."""
    output = _list_orphans([ORPHAN])

    assert 'connector="solana-mainnet-beta"' not in output


def test_listing_reports_the_dex_and_pool():
    output = _list_orphans([ORPHAN])

    assert "dex: orca/clmm" in output
    assert f"pool: {ORPHAN['pool_address']}" in output


def test_listing_says_stopping_will_not_close_it():
    """The failure mode was an agent trying to stop an already-terminated executor."""
    output = _list_orphans([ORPHAN])

    assert "Stopping the executor will NOT close it" in output
    assert "resolve_orphan" in output


def test_no_close_call_when_the_position_address_is_unknown():
    """A SYSTEM_CLEANUP orphan needs reconciliation first — never a close call with None in it."""
    unknown = {
        **ORPHAN,
        "close_type": "SYSTEM_CLEANUP",
        "position_address": None,
        "hold_reason": None,
        "needs_onchain_reconciliation": True,
    }

    output = _list_orphans([unknown])

    # The trailing summary always names the recovery call; what must not appear is a
    # per-record "close with:" line, which would carry position_address=None.
    assert "close with:" not in output
    assert 'position_address="None"' not in output
    assert "position address unknown" in output


def test_empty_listing_stays_clean():
    output = _list_orphans([])

    assert "No orphaned positions" in output
    assert "close with:" not in output
