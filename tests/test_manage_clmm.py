"""manage_clmm — the tool that makes an orphaned CLMM position recoverable.

Before this tool existed the orphan warning told the agent to "close the position via the gateway
tools", but nothing exposed a CLMM close: ``manage_executors(action="stop")`` is correctly a no-op
on an already-terminated executor, ``manage_amm`` covers AMMs only, and ``explore_dex_pools`` is
read-only. The recovery instruction had no implementing tool.

Two details carry the recovery path and are pinned here:
- an orphan record reports ``lp_provider`` as ``"orca/clmm"`` while Gateway routes on the bare
  ``"orca"``, so the tool must normalise it
- an lp_executor's position is opened by the bot straight against Gateway, so the API database has
  no row to read the pool from and ``pool_address`` must be forwarded on close

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import CLMMRequest
from mcp_servers.hummingbot_api.tools.gateway_clmm import manage_clmm_impl


class _RecordingCLMM:
    """Captures the kwargs each client.gateway_clmm.* call receives."""

    def __init__(self):
        self.calls = []

    def _record(self, name):
        async def call(**kwargs):
            self.calls.append((name, kwargs))
            return {"transaction_hash": "sig123", "status": "CONFIRMED"}

        return call

    def __getattr__(self, name):
        return self._record(name)


class _Client:
    def __init__(self):
        self.gateway_clmm = _RecordingCLMM()


def _run(request, client=None):
    return asyncio.run(manage_clmm_impl(client, request))


# --- connector normalisation -------------------------------------------------


def test_lp_provider_form_is_normalised_to_the_bare_connector():
    """An orphan's lp_provider ('orca/clmm') must reach Gateway as 'orca'."""
    client = _Client()

    result = _run(
        CLMMRequest(
            action="close",
            connector="orca/clmm",
            network="solana-mainnet-beta",
            position_address="POS",
            pool_address="POOL",
        ),
        client,
    )

    assert result["connector"] == "orca"
    _, kwargs = client.gateway_clmm.calls[0]
    assert kwargs["connector"] == "orca"


def test_unsupported_connector_is_rejected_by_name():
    with pytest.raises(ToolError, match="Unsupported CLMM connector"):
        _run(
            CLMMRequest(
                action="close",
                connector="sushiswap",
                network="ethereum-mainnet",
                position_address="POS",
            )
        )


# --- the orphan recovery call ------------------------------------------------


def test_close_forwards_pool_address():
    """Without pool_address the API 400s: an lp_executor position is never in its database."""
    client = _Client()

    _run(
        CLMMRequest(
            action="close",
            connector="orca",
            network="solana-mainnet-beta",
            position_address="H4vD69DsraHjHyKvRwRPHVGe2aJkvAUaNK5tMif2CiNw",
            pool_address="Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
        ),
        client,
    )

    name, kwargs = client.gateway_clmm.calls[0]
    assert name == "close_position"
    assert kwargs["pool_address"] == "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
    assert kwargs["position_address"] == "H4vD69DsraHjHyKvRwRPHVGe2aJkvAUaNK5tMif2CiNw"


def test_collect_fees_forwards_pool_address():
    client = _Client()

    _run(
        CLMMRequest(
            action="collect_fees",
            connector="meteora",
            network="solana-mainnet-beta",
            position_address="POS",
            pool_address="POOL",
        ),
        client,
    )

    name, kwargs = client.gateway_clmm.calls[0]
    assert name == "collect_fees"
    assert kwargs["pool_address"] == "POOL"


# --- validation guards -------------------------------------------------------


def test_no_action_returns_the_guide_without_touching_the_client():
    result = _run(CLMMRequest())

    assert result["action"] is None
    assert "manage_clmm" in result["formatted_output"]
    assert "orphan" in result["formatted_output"].lower()


def test_connector_is_required():
    with pytest.raises(ToolError, match="connector is required"):
        _run(
            CLMMRequest(
                action="close", network="solana-mainnet-beta", position_address="POS"
            )
        )


def test_network_is_required():
    with pytest.raises(ToolError, match="network is required"):
        _run(CLMMRequest(action="close", connector="orca", position_address="POS"))


def test_close_requires_position_address():
    with pytest.raises(ToolError, match="position_address is required"):
        _run(
            CLMMRequest(action="close", connector="orca", network="solana-mainnet-beta")
        )


def test_remove_liquidity_requires_a_percentage():
    with pytest.raises(ToolError, match="percentage_to_remove is required"):
        _run(
            CLMMRequest(
                action="remove_liquidity",
                connector="orca",
                network="solana-mainnet-beta",
                position_address="POS",
            )
        )


def test_open_requires_at_least_one_amount():
    """Both amounts optional individually, but an empty position is not a position."""
    with pytest.raises(ToolError, match="requires base_token_amount"):
        _run(
            CLMMRequest(
                action="open",
                connector="orca",
                network="solana-mainnet-beta",
                pool_address="POOL",
                lower_price="75",
                upper_price="76",
            )
        )


def test_guards_run_before_the_client_is_dereferenced():
    """A validation failure must not depend on having a client at all."""
    request = CLMMRequest(
        action="close", connector="orca", network="solana-mainnet-beta"
    )

    with pytest.raises(ToolError, match="position_address is required"):
        _run(request, client=None)


# --- amounts are passed through as Decimals ---------------------------------


def test_remove_liquidity_passes_percentage_as_decimal():
    from decimal import Decimal

    client = _Client()

    _run(
        CLMMRequest(
            action="remove_liquidity",
            connector="raydium",
            network="solana-mainnet-beta",
            position_address="POS",
            percentage_to_remove="50",
        ),
        client,
    )

    _, kwargs = client.gateway_clmm.calls[0]
    assert kwargs["percentage_to_remove"] == Decimal("50")


# --- position_info: one position, or the wallet's -----------------------------
# position_address is declared on the request, so pydantic accepts it. It used to be
# dropped: the action always called positions_owned, which answers with every position
# the wallet holds on the connector. That reads as correct while a wallet holds one
# position and silently becomes the wrong answer the moment it holds two.


def test_position_info_with_an_address_reads_that_position():
    client = _Client()
    _run(CLMMRequest(action="position_info", connector="orca",
                     network="solana-mainnet-beta",
                     position_address="POS1"), client)

    names = [name for name, _ in client.gateway_clmm.calls]
    assert names == ["get_position_info"], (
        f"asked about one position, called {names} — positions_owned answers with every "
        "position the wallet holds, which is a different question"
    )
    assert client.gateway_clmm.calls[0][1]["position_address"] == "POS1"


def test_position_info_without_an_address_lists_the_wallet():
    client = _Client()
    _run(CLMMRequest(action="position_info", connector="orca",
                     network="solana-mainnet-beta",
                     wallet_address="WALLET"), client)

    names = [name for name, _ in client.gateway_clmm.calls]
    assert names == ["get_positions_owned"]
    assert client.gateway_clmm.calls[0][1]["wallet_address"] == "WALLET"


def test_a_single_position_is_returned_as_a_list():
    """The formatter renders position_info as rows either way, so the shapes must agree."""
    client = _Client()
    result = _run(CLMMRequest(action="position_info", connector="orca",
                              network="solana-mainnet-beta",
                              position_address="POS1"), client)

    assert isinstance(result["result"], list) and len(result["result"]) == 1
