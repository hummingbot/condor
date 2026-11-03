"""Regression tests for condor reading shapes the Hummingbot API never sends.

Every case here failed silently or crashed a whole screen because condor read a
key the API does not have — the kind of bug that survives manual use because the
field simply renders blank:

- ``pnl_summary`` is emitted as ``null`` (not omitted), so ``.get(k, {})`` hands
  back None and the next ``.get`` raises inside a per-position loop.
- the zero-liquidity filters read ``liquidity`` / ``base_amount``; the API sends
  ``base_token_amount`` / ``quote_token_amount``, so the filters were no-ops.
- a close returns ``base_token_amount_removed``; condor read ``base_amount``,
  missed, and re-opened with PRE-CLOSE amounts.
- an open that was submitted but not confirmed has ``position_address: None``;
  paths that did not branch on it printed a literal "None".
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from handlers.dex import liquidity as liquidity_mod
from handlers.dex import lp_monitor_handlers as lpm
from handlers.dex import menu as menu_mod
from handlers.dex import pools as pools_mod
from handlers.dex._shared import resolve_network_id
from mcp_servers.hummingbot_api.formatters.gateway import (
    format_amm_result,
    format_clmm_result,
)


def _position(**overrides):
    """A position row shaped like hapi's GatewayCLMMRepository.position_to_dict."""
    row = {
        "position_address": "Pos1",
        "pool_address": "Pool1",
        "network": "solana-mainnet-beta",
        "connector": "meteora",
        "trading_pair": "SOL-USDC",
        "base_token": "SOL",
        "quote_token": "USDC",
        "created_at": "2026-08-01T00:00:00",
        "closed_at": None,
        "status": "OPEN",
        "lower_price": 100.0,
        "upper_price": 140.0,
        "lower_bin_id": None,
        "upper_bin_id": None,
        "entry_price": None,
        "current_price": None,
        "percentage": None,
        "initial_base_token_amount": None,
        "initial_quote_token_amount": None,
        "position_rent": None,
        "base_token_amount": 1.5,
        "quote_token_amount": 200.0,
        "in_range": True,
        "base_fee_collected": 0.0,
        "quote_fee_collected": 0.0,
        "base_fee_pending": 0.0,
        "quote_fee_pending": 0.0,
        # The bug: the key is always PRESENT and null when hapi cannot compute
        # PnL (no entry/current price, or no initial amounts).
        "pnl_summary": None,
        "last_updated": "2026-08-01T00:00:00",
    }
    row.update(overrides)
    return row


# ============================================
# pnl_summary: null, not {}
# ============================================


def test_every_position_formatter_survives_a_null_pnl_summary():
    """One AttributeError here took down the whole /lp screen."""
    pos = _position()

    assert pools_mod._format_position_detail(pos, detailed=True)
    assert pools_mod._format_position_detail(pos, detailed=False)
    assert liquidity_mod._format_compact_position_line(pos)
    assert liquidity_mod._format_closed_position_line(pos)
    assert liquidity_mod._format_detailed_position_line(pos)
    text, _keyboard = lpm.format_position_detail_view(
        pos, token_cache={}, token_prices={}, index=0, total=1, instance_id="i1"
    )
    assert text


def test_a_populated_pnl_summary_still_renders_its_real_keys():
    """The real key names, straight from position_to_dict's pnl_summary."""
    pos = _position(
        entry_price=120.0,
        current_price=130.0,
        pnl_summary={
            "entry_price": 120.0,
            "current_price": 130.0,
            "price_change_pct": 8.33,
            "initial_base": 2.0,
            "initial_quote": 100.0,
            "initial_value_quote": 340.0,
            "current_base": 1.5,
            "current_quote": 200.0,
            "current_lp_value_quote": 395.0,
            "total_fees_base": 0.0,
            "total_fees_quote": 3.0,
            "total_fees_value_quote": 3.0,
            "hodl_value_quote": 360.0,
            "impermanent_loss_quote": 35.0,
            "current_total_value_quote": 398.0,
            "total_pnl_quote": 58.0,
            "total_pnl_pct": 17.06,
            "duration_hours": 12.0,
            "fee_apr_estimate": 6.4,
        },
    )

    detailed = liquidity_mod._format_detailed_position_line(pos)
    # current_base / current_quote, not the invented current_base_total.
    assert "1.5" in detailed and "200" in detailed
    # Per-token PnL is derived (1.5 - 2.0 = -0.5), since hapi reports none.
    assert "-0.5" in detailed

    compact = pools_mod._format_position_detail(pos, detailed=False)
    assert "58" in compact, "the compact line must show total_pnl_quote"


# ============================================
# zero-liquidity filters
# ============================================


class _FakeClmmSearch:
    def __init__(self, positions):
        self.positions = positions

    async def search_positions(self, **kwargs):
        return {"data": self.positions}


class _FakeClient:
    """No `gateway` attribute: the token top-up branch is skipped."""

    def __init__(self, positions):
        self.gateway_clmm = _FakeClmmSearch(positions)


def test_the_dex_menu_drops_positions_whose_liquidity_is_gone():
    positions = [
        _position(position_address="Live", base_token_amount=1.5),
        _position(
            position_address="Empty", base_token_amount=0.0, quote_token_amount=0.0
        ),
    ]

    data = asyncio.run(menu_mod._fetch_lp_positions(_FakeClient(positions)))

    addresses = [p["position_address"] for p in data["lp_positions"]]
    assert addresses == ["Live"], "the drained position must be filtered out"


def test_the_lp_screen_drops_positions_whose_liquidity_is_gone():
    positions = [
        _position(position_address="Live", quote_token_amount=200.0),
        _position(
            position_address="Empty", base_token_amount=0.0, quote_token_amount=0.0
        ),
    ]

    data = asyncio.run(liquidity_mod._fetch_lp_positions(_FakeClient(positions)))

    addresses = [p["position_address"] for p in data["positions"]]
    assert addresses == ["Live"]


# ============================================
# rebalance: close result keys + confirmation gate
# ============================================


class _FakeMessage:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


class _FakeQuery:
    def __init__(self):
        self.message = _FakeMessage()

    async def answer(self, *a, **kw):
        return None


class _FakeRebalanceClmm:
    def __init__(self, close_result):
        self.close_result = close_result
        self.open_calls = []

    async def close_position(self, **kwargs):
        return self.close_result

    async def open_position(self, **kwargs):
        self.open_calls.append(kwargs)
        return {
            "transaction_hash": "OpenTx",
            "position_address": None,
            "status": "submitted",
        }


def _run_rebalance(monkeypatch, close_result):
    clmm = _FakeRebalanceClmm(close_result)
    client = SimpleNamespace(gateway_clmm=clmm)

    async def _fake_get_client(*a, **kw):
        return client

    import config_manager

    monkeypatch.setattr(config_manager, "get_client", _fake_get_client)

    query = _FakeQuery()
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1))
    pos = _position(base_token_amount=1.5, quote_token_amount=200.0)
    context = SimpleNamespace(
        user_data={"positions_cache": {"k": pos}, "token_cache": {}}
    )

    asyncio.run(lpm.handle_lpm_rebalance_execute(update, context, "k"))
    return clmm, query


def test_rebalance_reopens_with_the_amounts_the_close_actually_returned(monkeypatch):
    """hapi sends base_token_amount_removed; the old keys always missed and the
    re-open silently used stale pre-close amounts."""
    clmm, _ = _run_rebalance(
        monkeypatch,
        {
            "transaction_hash": "CloseTx",
            "position_address": "Pos1",
            "status": "confirmed",
            "base_token_amount_removed": "1.4321",
            "quote_token_amount_removed": "198.77",
        },
    )

    assert len(clmm.open_calls) == 1
    call = clmm.open_calls[0]
    # Decimal, not float: a float puts "0.30000000000000004"/"1e-08" on the wire.
    assert call["base_token_amount"] == Decimal("1.4321")
    assert call["quote_token_amount"] == Decimal("198.77")
    assert isinstance(call["base_token_amount"], Decimal)
    # Never the pre-close cached amounts.
    assert call["base_token_amount"] != Decimal("1.5")


def test_rebalance_passes_none_for_a_side_the_close_returned_nothing_for(monkeypatch):
    clmm, _ = _run_rebalance(
        monkeypatch,
        {
            "transaction_hash": "CloseTx",
            "position_address": "Pos1",
            "status": "confirmed",
            "base_token_amount_removed": "1.4321",
            "quote_token_amount_removed": None,
        },
    )

    assert clmm.open_calls[0]["quote_token_amount"] is None


def test_rebalance_refuses_to_reopen_before_the_close_confirms(monkeypatch):
    """A submitted-not-confirmed close means the tokens are not in the wallet."""
    clmm, query = _run_rebalance(
        monkeypatch,
        {
            "transaction_hash": "CloseTx",
            "position_address": "Pos1",
            "status": "submitted",
            "base_token_amount_removed": None,
            "quote_token_amount_removed": None,
        },
    )

    assert clmm.open_calls == [], "no re-open until the close is confirmed"
    assert any("submitted" in t for t in query.message.texts)


def test_rebalance_reports_a_pending_open_instead_of_claiming_an_address(monkeypatch):
    _, query = _run_rebalance(
        monkeypatch,
        {
            "transaction_hash": "CloseTx",
            "position_address": "Pos1",
            "status": "confirmed",
            "base_token_amount_removed": "1.4321",
            "quote_token_amount_removed": "198.77",
        },
    )

    final = query.message.texts[-1]
    assert "None" not in final
    assert "submitted" in final or "confirm" in final


# ============================================
# network ids come from Gateway, not a static table
# ============================================


class _FakeNetworksGateway:
    """Gateway's real network ids: '{chain}-{network}' from conf/chains/."""

    async def list_networks(self):
        return {
            "networks": [
                {
                    "network_id": "solana-mainnet-beta",
                    "chain": "solana",
                    "network": "mainnet-beta",
                },
                {"network_id": "solana-devnet", "chain": "solana", "network": "devnet"},
                {
                    "network_id": "ethereum-mainnet",
                    "chain": "ethereum",
                    "network": "mainnet",
                },
                {"network_id": "ethereum-base", "chain": "ethereum", "network": "base"},
                {"network_id": "ethereum-bsc", "chain": "ethereum", "network": "bsc"},
                {
                    "network_id": "ethereum-arbitrum",
                    "chain": "ethereum",
                    "network": "arbitrum",
                },
            ],
            "count": 6,
        }


def _resolve(text):
    client = SimpleNamespace(gateway=_FakeNetworksGateway())
    return asyncio.run(resolve_network_id(client, text))


def test_evm_network_names_resolve_to_gatewaysown_ids():
    """'base' is ethereum-base, NOT base-mainnet: Gateway builds the id from the
    chain directory plus the network yml basename."""
    assert _resolve("base") == "ethereum-base"
    assert _resolve("bsc") == "ethereum-bsc"
    assert _resolve("arbitrum") == "ethereum-arbitrum"
    assert _resolve("mainnet") == "ethereum-mainnet"


def test_a_full_network_id_passes_through():
    assert _resolve("ethereum-base") == "ethereum-base"
    assert _resolve("solana-mainnet-beta") == "solana-mainnet-beta"


def test_an_unknown_network_fails_loudly_listing_what_gateway_offers():
    import pytest

    with pytest.raises(ValueError) as excinfo:
        _resolve("base-mainnet")
    assert "ethereum-base" in str(excinfo.value)


# ============================================
# formatters: never print a literal "None"
# ============================================


def test_a_pending_open_never_renders_the_word_none():
    out = format_clmm_result(
        "open",
        {
            "action": "open",
            "connector": "meteora",
            "network": "solana-mainnet-beta",
            "result": {
                "transaction_hash": "Tx1",
                "position_address": None,
                "trading_pair": "SOL-USDC",
                "pool_address": "Pool1",
                "lower_price": 100.0,
                "upper_price": 140.0,
                "status": "submitted",
            },
        },
    )
    assert "None" not in out
    assert "submitted" in out


def test_amm_write_results_read_the_signature_the_api_sends():
    """AMM write responses carry `signature`, not CLMM's `transaction_hash`."""
    out = format_amm_result(
        "add_liquidity",
        {
            "action": "add_liquidity",
            "connector": "meteora",
            "network": "solana-mainnet-beta",
            "result": {"signature": "Sig123", "status": 1, "data": None},
        },
    )
    assert "Sig123" in out
    assert "None" not in out


def test_amm_create_pool_result_reads_the_signature_too():
    out = format_amm_result(
        "create_pool",
        {
            "action": "create_pool",
            "connector": "meteora",
            "network": "solana-mainnet-beta",
            "result": {
                "signature": "Sig456",
                "status": 1,
                "pool_address": "Pool9",
                "price": 1.25,
            },
        },
    )
    assert "Sig456" in out and "Pool9" in out
    assert "None" not in out


# ============================================
# pool-info field names
# ============================================


def test_the_mcp_detailed_pool_table_only_has_columns_the_api_sends():
    """volume / fee_tvl_ratio / max_fee_percentage / protocol_fee_percentage are
    not CLMMPoolListItem fields — they rendered as a permanent wall of N/A."""
    from mcp_servers.hummingbot_api.tools.gateway_clmm import (
        format_pools_as_detailed_table,
    )

    table = format_pools_as_detailed_table(
        [
            {
                "address": "Pool1",
                "name": "SOL-USDC",
                "trading_pair": "SOL-USDC",
                "mint_x": "MintX",
                "mint_y": "MintY",
                "bin_step": 20,
                "current_price": 130.0,
                "liquidity": "1000000",
                "base_fee_percentage": "0.2",
                "apr": 12.0,
                "apy": 12.7,
                "volume_24h": 500000.0,
                "fees_24h": 1000.0,
            }
        ]
    )

    header = table.splitlines()[0]
    for gone in (
        "volume_hour_1",
        "fee_tvl_ratio",
        "max_fee_percentage",
        "protocol_fee_percentage",
    ):
        assert gone not in header, f"{gone} is not a field the API sends"
    assert "N/A" not in table.splitlines()[2]
