from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import (
    POOL,
    SOL_MINT,
    WALLET,
    executor_row,
    pool_record,
    prior_close,
    strategy_config,
    terminal_row,
)

from agents.lp_expert.core import orca, planner, portfolio, runtime


def test_resolve_runtime_uses_in_process_engine_without_self_http(
    monkeypatch, fake_engine
):
    calls = []
    monkeypatch.setattr(
        "condor.agents.engine.get_engine",
        lambda controller_id: calls.append(controller_id) or fake_engine,
    )

    scope = runtime.resolve_runtime("lp_expert.orca_1")

    assert calls == ["lp_expert.orca_1"]
    assert scope.controller_id == "lp_expert.orca_1"
    assert scope.tick_number == 2
    assert scope.config["max_open_executors"] == 3
    source = inspect.getsource(runtime.resolve_runtime)
    assert "get_engine" in source
    assert not any(
        marker in source.casefold()
        for marker in ("http://", "https://", "localhost", "127.0.0.1")
    )


@pytest.mark.parametrize(
    ("controller_id", "mode"),
    [
        ("lp_expert.orca_e1", "dry_run"),
        ("lp_expert.orca_e7", "run_once"),
    ],
)
def test_resolve_runtime_supports_experiment_modes(
    monkeypatch, fake_engine, controller_id, mode
):
    fake_engine.agent_id = controller_id
    fake_engine.config = strategy_config(execution_mode=mode)
    fake_engine.get_info = lambda: {
        "agent_id": controller_id,
        "strategy_slug": "orca",
        "status": "running",
        "server_name": "local",
    }
    monkeypatch.setattr("condor.agents.engine.get_engine", lambda _: fake_engine)

    scope = runtime.resolve_runtime(controller_id)

    assert scope.execution_mode == mode
    assert scope.tick_number == 1
    assert scope.session_dir is None


@pytest.mark.parametrize(
    "controller_id",
    [
        "lp_expert.orca_0",
        "lp_expert.orca_e0",
        "lp_expert.orca",
        "other.orca_1",
        "lp_expert.orca_1/../../x",
    ],
)
def test_resolve_runtime_rejects_invalid_controller_before_lookup(
    monkeypatch, controller_id
):
    monkeypatch.setattr(
        "condor.agents.engine.get_engine",
        lambda _: pytest.fail("invalid identity must not reach get_engine"),
    )
    with pytest.raises(ValueError, match="controller identity"):
        runtime.resolve_runtime(controller_id)


def test_runtime_enforces_three_executor_and_one_deployment_contract():
    assert (
        runtime._validate_config(strategy_config(), "loop")["max_open_executors"] == 3
    )
    with pytest.raises(ValueError):
        runtime._validate_config(strategy_config(max_open_executors=4), "loop")
    with pytest.raises(ValueError):
        runtime._validate_config(
            strategy_config(max_slot_deployments_per_tick=2), "loop"
        )


def test_hummingbot_binding_and_balance_reads_are_exactly_scoped(tmp_path):
    calls = []

    class Client:
        gateway = SimpleNamespace(
            get_network_config=lambda network: asyncio.sleep(
                0, result={"default_wallet": WALLET}
            )
        )
        portfolio = SimpleNamespace(
            get_state=lambda **kwargs: calls.append(kwargs)
            or asyncio.sleep(
                0,
                result={
                    "master_account": {
                        "solana-mainnet-beta": [{"symbol": "USDC", "available": "12"}]
                    }
                },
            )
        )

    scope = runtime.RuntimeScope(
        controller_id="lp_expert.orca_1",
        strategy_slug="orca",
        execution_mode="loop",
        server_name="local",
        account_name="master_account",
        network="solana-mainnet-beta",
        swap_connector="jupiter",
        quote_symbol="USDC",
        quote_mint=orca.USDC_MINT,
        quote_decimals=6,
        tick_count=1,
        current_tick=2,
        tick_started_at=1_000.0,
        last_tick_at=1_000.0,
        session_dir=tmp_path,
        config=strategy_config(),
    ).with_wallet(WALLET)

    rows = asyncio.run(runtime.refresh_balances(scope, Client()))

    assert rows == [{"symbol": "USDC", "available": "12"}]
    assert calls == [
        {
            "account_names": ["master_account"],
            "connector_names": ["solana-mainnet-beta"],
            "refresh": True,
        }
    ]


def test_orca_discovery_uses_exactly_four_bounded_lenses(monkeypatch):
    calls = []
    monkeypatch.setattr(
        orca,
        "fetch_json",
        lambda url: calls.append(url) or {"data": [pool_record()]},
    )

    result = asyncio.run(orca.scan_pools(3))

    assert tuple(orca.DISCOVERY_LENSES) == (
        "yieldovertvl24h",
        "yieldovertvl7d",
        "volume24h",
        "volume7d",
    )
    assert len(calls) == 4
    assert result["source_coverage"]["required_requests"] == 4
    assert result["source_coverage"]["completed_requests"] == 4
    assert result["deployable"] is True


def test_orca_incomplete_discovery_fails_closed(monkeypatch):
    def fetch(url):
        if "sortBy=volume7d" in url:
            raise TimeoutError("unavailable")
        return {"data": [pool_record()]}

    monkeypatch.setattr(orca, "fetch_json", fetch)

    result = asyncio.run(orca.scan_pools(3))

    assert result["status"] == "incomplete"
    assert result["deployable"] is False
    assert result["source_coverage"]["completed_requests"] == 3


def test_orca_registered_token_identity_is_exact():
    candidate, error = orca.normalize_record(pool_record(), "all", "volume24h", 1)
    assert error is None
    accepted, rejected = orca.filter_registered_tokens(
        [candidate],
        {"tokens": [{"address": SOL_MINT, "symbol": "SOL", "decimals": 9}]},
    )
    assert accepted == [candidate]
    assert rejected == []

    accepted, rejected = orca.filter_registered_tokens(
        [candidate],
        {"tokens": [{"address": SOL_MINT, "symbol": "SOL", "decimals": 8}]},
    )
    assert accepted == []
    assert rejected[0]["reason"] == "registered_token_identity_conflict"


def test_plan_is_double_sided_bounded_and_digest_stable():
    request = planner.PlanRequest(
        pool_address=POOL,
        base_symbol="SOL",
        base_mint=SOL_MINT,
        base_decimals=9,
        current_price="180",
        tick_spacing=64,
        amount_quote="4",
        range_half_width_pct="10",
        minimum_range_half_width_pct="0.5",
        maximum_range_half_width_pct="20",
        rebalance_threshold_pct="1",
        max_slippage_pct="1",
    )

    first = planner.build_plan(request)
    second = planner.build_plan(request)

    assert first == second
    assert Decimal(first["inventory"]["base_amount"]) > 0
    assert Decimal(first["inventory"]["quote_amount"]) > 0
    assert Decimal(first["inventory"]["maximum_total_usdc"]) <= Decimal("4")
    assert first["plan_digest"] == second["plan_digest"]


def test_fetch_all_executors_follows_every_page_and_rejects_cursor_loops():
    class Executors:
        def __init__(self):
            self.calls = []

        async def search_executors(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["cursor"] is None:
                return {"data": [executor_row("one")], "next_cursor": "page-2"}
            return {"data": [executor_row("two")], "next_cursor": None}

    client = SimpleNamespace(executors=Executors())
    rows = asyncio.run(portfolio.fetch_all_executors(client, "master_account"))
    assert [row["executor_id"] for row in rows] == ["one", "two"]
    assert [call["cursor"] for call in client.executors.calls] == [None, "page-2"]
    assert all(call["limit"] == 1000 for call in client.executors.calls)

    class Looping:
        async def search_executors(self, **kwargs):
            return {"data": [], "next_cursor": "again"}

    with pytest.raises(ValueError, match="repeated a cursor"):
        asyncio.run(
            portfolio.fetch_all_executors(
                SimpleNamespace(executors=Looping()), "master_account"
            )
        )


def _portfolio(monkeypatch, rows, *, tick=2, close=None, closes=None, **config):
    monkeypatch.setattr(portfolio.time, "time", lambda: 2_000)
    return portfolio.build_portfolio(
        rows=rows,
        controller_id="lp_expert.orca_1",
        strategy_config=strategy_config(**config),
        session_started_at=1_900,
        current_tick=tick,
        prior_closes=(
            list(closes)
            if closes is not None
            else ([close] if close is not None else [])
        ),
    )


def test_portfolio_flat_healthy_triggered_and_reconciling(monkeypatch):
    flat = _portfolio(monkeypatch, [])
    assert flat["active_count"] == 0
    assert flat["available_slots"] == 3
    assert flat["deployment_blocked"] is False

    healthy = _portfolio(monkeypatch, [executor_row()])
    assert healthy["active_count"] == 1
    assert healthy["available_slots"] == 2
    assert healthy["close_required_executor_ids"] == []

    triggered = _portfolio(
        monkeypatch,
        [executor_row(net_pnl_pct="0.06")],
    )
    assert triggered["close_required_executor_ids"] == ["executor-1"]

    reconciling = _portfolio(
        monkeypatch,
        [executor_row(state="CLOSING", net_pnl_pct="0.06")],
    )
    assert reconciling["close_required_executor_ids"] == []
    assert reconciling["reconcile_required_executor_ids"] == ["executor-1"]


def test_portfolio_foreign_executor_is_observation_only_and_blocks_deploy(
    monkeypatch,
):
    result = _portfolio(
        monkeypatch,
        [executor_row("foreign", controller_id="other.agent_1")],
    )
    assert result["active_count"] == 0
    assert result["foreign_active_executor_ids"] == ["foreign"]
    assert result["deployment_blocked"] is True


def test_portfolio_supports_three_independent_executors(monkeypatch):
    rows = [
        executor_row(f"executor-{index}", pool_address=f"pool-{index}")
        for index in range(1, 4)
    ]
    result = _portfolio(monkeypatch, rows)
    assert result["active_count"] == 3
    assert result["available_slots"] == 0
    assert result["occupied_pools"] == ["pool-1", "pool-2", "pool-3"]
    assert len(result["executors"]) == 3


def test_cleanup_is_forbidden_on_close_tick_and_priority_on_following_tick(
    monkeypatch,
):
    row = terminal_row(residual="0.01", native_status="FAILED")
    close = prior_close(closed_tick=2)
    with pytest.raises(ValueError, match="requires a later tick"):
        _portfolio(monkeypatch, [row], tick=2, close=close)

    result = _portfolio(monkeypatch, [row], tick=3, close=close)
    assert result["cleanups"][0]["status"] == "cleanup_required"
    assert result["cleanups"][0]["capacity_quarantined"] is True
    assert result["available_slots"] == 2
    assert result["deployment_blocked"] is True


@pytest.mark.parametrize(
    ("residual", "native_status", "expected", "quarantined"),
    [
        ("0", "CONFIRMED", "complete", False),
        ("0.00001", "FAILED", "dust", False),
        ("0.01", "PENDING", "pending", True),
        ("0.01", "FAILED", "cleanup_required", True),
    ],
)
def test_cleanup_classification_is_exact(
    monkeypatch, residual, native_status, expected, quarantined
):
    result = _portfolio(
        monkeypatch,
        [terminal_row(residual=residual, native_status=native_status)],
        tick=2,
        close=prior_close(),
    )
    assert result["cleanups"][0]["status"] == expected
    assert result["cleanups"][0]["capacity_quarantined"] is quarantined
    assert result["available_slots"] == (2 if quarantined else 3)


def test_cleanup_identity_conflict_and_ambiguity_require_manual_review(
    monkeypatch,
):
    conflict = _portfolio(
        monkeypatch,
        [terminal_row(pool_address="another-pool")],
        close=prior_close(),
    )
    assert conflict["cleanups"][0]["status"] == "manual_review"
    assert conflict["deployment_blocked"] is True

    missing = terminal_row()
    del missing["custom_info"]["close_transaction_hash"]
    ambiguous = _portfolio(
        monkeypatch,
        [missing],
        close=prior_close(),
    )
    assert ambiguous["cleanups"][0]["status"] == "manual_review"
    assert ambiguous["cleanups"][0]["capacity_quarantined"] is True


def test_foreign_executor_cannot_satisfy_cleanup_identity(monkeypatch):
    result = _portfolio(
        monkeypatch,
        [
            terminal_row(
                controller_id="foreign.agent_1",
            )
        ],
        close=prior_close(),
    )
    assert result["cleanups"][0]["status"] == "manual_review"
    assert (
        "did not match one current-session executor" in result["cleanups"][0]["reason"]
    )
    assert result["cleanups"][0]["capacity_quarantined"] is True


def test_same_base_mint_executors_remain_independently_attributed(monkeypatch):
    rows = [
        executor_row("one", pool_address="pool-one"),
        executor_row("two", pool_address="pool-two"),
        terminal_row("closed", pool_address="pool-closed", residual="0.01"),
    ]
    result = _portfolio(
        monkeypatch,
        rows,
        close=prior_close("closed", pool_address="pool-closed"),
    )
    assert result["active_count"] == 2
    assert result["occupied_pools"] == ["pool-one", "pool-two"]
    assert result["cleanups"][0]["executor_id"] == "closed"
    assert result["cleanups"][0]["evidence"]["residual_base_amount"] == Decimal("0.01")


def test_multiple_prior_closes_are_classified_independently(monkeypatch):
    rows = [
        terminal_row(
            "clean",
            pool_address="pool-clean",
            residual="0",
            native_status="CONFIRMED",
        ),
        terminal_row(
            "residual",
            pool_address="pool-residual",
            residual="0.01",
            native_status="FAILED",
        ),
    ]
    result = _portfolio(
        monkeypatch,
        rows,
        closes=[
            prior_close("clean", pool_address="pool-clean"),
            prior_close("residual", pool_address="pool-residual"),
        ],
    )
    assert [item["executor_id"] for item in result["cleanups"]] == [
        "clean",
        "residual",
    ]
    assert [item["status"] for item in result["cleanups"]] == [
        "complete",
        "cleanup_required",
    ]
    assert result["quarantined_cleanup_executor_ids"] == ["residual"]
    assert result["available_slots"] == 2
    assert result["deployment_blocked"] is True
