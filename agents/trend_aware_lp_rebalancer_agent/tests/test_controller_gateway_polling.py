# Purpose: Verify the controller's polling guard against local Hummingbot methods
# with in-memory orders and mocked Gateway responses. No network or live bot writes.
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("hummingbot", reason="Polling tests require the native Hummingbot environment")

from hummingbot.connector.gateway.gateway import Gateway
from hummingbot.connector.gateway.gateway_base import TransactionStatus
from hummingbot.connector.gateway.gateway_in_flight_order import GatewayInFlightOrder
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
from hummingbot.strategy_v2.models.executors import CloseType
from pydantic import ValidationError

try:
    from hummingbot.core.gateway.gateway_error import GatewayError
except ImportError:  # Older local Hummingbot checkout; newer installed image provides it.
    GatewayError = None

from agents.trend_aware_lp_rebalancer_agent.controllers.trend_aware_lp_rebalancer.trend_aware_lp_rebalancer import (
    PREPARE_ROLE,
    ExitReason,
    LifecycleState,
    ReadinessState,
    TrendAwareLPRebalancer,
    TrendAwareLPRebalancerConfig,
)


class MemoryTracker:
    """Keep real in-flight order state, without recorder/event/database side effects."""

    def __init__(self, orders):
        self.orders = {order.client_order_id: order for order in orders}
        self.order_updates = []
        self.trade_updates = []

    def fetch_order(self, order_id):
        return self.orders.get(order_id)

    def process_order_update(self, update):
        self.order_updates.append(update)
        self.orders[update.client_order_id].update_with_order_update(update)

    def process_trade_update(self, update):
        self.trade_updates.append(update)
        self.orders[update.client_order_id].update_with_trade_update(update)


class OfflineGateway(Gateway):
    """Run the native polling/receipt implementations without initializing network IO."""

    def __init__(self, orders=()):
        self._connector_name = "solana-mainnet-beta"
        self._chain = "solana"
        self._network = "mainnet-beta"
        self._native_currency = "SOL"
        self._order_tracker = MemoryTracker(orders)
        self._order_failure_reasons = {}
        self._lp_orders_metadata = {}
        self.rpc = SimpleNamespace(get_transaction_status=AsyncMock(side_effect=self.confirmed))
        self._trigger_lp_events_if_needed = Mock()
        self.update_balances = AsyncMock()

    @property
    def current_timestamp(self):
        return 1000

    def _get_gateway_instance(self):
        return self.rpc

    @staticmethod
    async def confirmed(chain, network, signature):
        return {"signature": signature, "txStatus": TransactionStatus.CONFIRMED.value, "fee": 0}


def make_order(order_id="swap", *, side=TradeType.BUY, signature="swap-signature", price=Decimal("NaN")):
    return GatewayInFlightOrder(
        client_order_id=order_id,
        trading_pair="SOL-USDC",
        order_type=OrderType.AMM_REMOVE if side == TradeType.RANGE else OrderType.MARKET,
        trade_type=side,
        creation_timestamp=900,
        amount=Decimal("0") if side == TradeType.RANGE else Decimal("0.008219"),
        price=price,
        exchange_order_id=signature,
        initial_state=OrderState.OPEN,
    )


def make_controller(connector):
    provider = SimpleNamespace(
        time=lambda: 1000,
        initialize_rate_sources=Mock(),
        get_connector=lambda name: connector,
        get_balance=lambda name, token: Decimal("100"),
    )
    config = TrendAwareLPRebalancerConfig(
        id="offline-controller",
        quote_token_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        total_amount_quote=Decimal("10"),
        lp_positions=[{
            "position_id": "sol",
            "trading_pair": "SOL-USDC",
            "pool_address": "A" * 32,
            "base_token_mint": "So11111111111111111111111111111111111111112",
            "allocation_pct": "100",
            "market_trend": "SIDEWAYS",
            "position_width_pct": "2",
            "downside_offset_pct": "0",
            "rebalance_threshold_pct": "0.3",
        }],
    )
    return TrendAwareLPRebalancer(config, provider, asyncio.Queue())


def position_summary_from_order(order):
    position = PositionHold("solana-mainnet-beta", order.trading_pair, None)
    position.add_orders_from_executor(SimpleNamespace(
        id="offline-executor", config=SimpleNamespace(side=order.trade_type),
        custom_info={"held_position_orders": [{
            "client_order_id": order.client_order_id,
            "trade_type": order.trade_type.name,
            "executed_amount_base": str(order.executed_amount_base),
            "executed_amount_quote": str(order.executed_amount_quote),
            "cumulative_fee_paid_quote": "0",
        }]},
    ))
    return position.get_position_summary(Decimal("150"))


def test_unguarded_native_poll_reproduces_wrong_order_and_nan_summary():
    missing = make_order(signature=None)
    missing.get_exchange_order_id = AsyncMock(side_effect=TimeoutError("no hash"))
    lp = make_order("lp", side=TradeType.RANGE, signature="lp-signature", price=Decimal("0"))
    connector = OfflineGateway([missing, lp])
    asyncio.run(connector.update_order_status([missing, lp]))
    assert missing.is_filled
    assert lp.current_state == OrderState.OPEN
    fill, = connector._order_tracker.trade_updates
    assert fill.client_order_id == "swap"
    assert fill.fill_price.is_nan() and fill.fill_quote_amount.is_nan()
    with pytest.raises(ValidationError) as error:
        position_summary_from_order(missing)
    assert {item["loc"][0] for item in error.value.errors()} == {
        "volume_traded_quote", "breakeven_price", "unrealized_pnl_quote",
    }


def test_start_installs_guard_before_control_loop_and_is_idempotent(monkeypatch):
    connector = OfflineGateway()
    controller = make_controller(connector)
    starts = []
    monkeypatch.setattr(ControllerBase, "start", lambda self: starts.append(connector.update_order_status))
    controller.start()
    controller.start()
    assert len(starts) == 2
    assert starts[0] is starts[1]
    assert starts[0]._trend_aware_lp_polling_owner is controller
    assert "update_order_status" not in OfflineGateway().__dict__


def test_guard_remains_installed_during_controller_shutdown():
    order = make_order()
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    controller.stop()
    asyncio.run(connector.update_order_status([order]))
    assert connector.update_order_status._trend_aware_lp_polling_owner is controller
    assert not connector._order_tracker.trade_updates


def test_another_controller_cannot_reuse_guard():
    connector = OfflineGateway()
    make_controller(connector)._install_gateway_polling_guard(connector)
    with pytest.raises(RuntimeError, match="another controller"):
        make_controller(connector)._install_gateway_polling_guard(connector)


def test_unsupported_connector_fails_before_start(monkeypatch):
    controller = make_controller(SimpleNamespace())
    start = Mock()
    monkeypatch.setattr(ControllerBase, "start", start)
    with pytest.raises(RuntimeError, match="does not support"):
        controller.start()
    start.assert_not_called()


@pytest.mark.parametrize("missing_side", [TradeType.BUY, TradeType.RANGE])
def test_missing_hash_cannot_receive_peer_lp_confirmation(missing_side):
    missing = make_order("missing", side=missing_side, signature=None)
    missing.get_exchange_order_id = AsyncMock(side_effect=TimeoutError("no hash"))
    lp = make_order("lp", side=TradeType.RANGE, signature="lp-signature", price=Decimal("0"))
    connector = OfflineGateway([missing, lp])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([missing, lp]))
    missing.get_exchange_order_id.assert_not_awaited()
    assert missing.current_state == OrderState.OPEN
    assert lp.is_filled
    assert [update.client_order_id for update in connector._order_tracker.order_updates] == ["lp"]
    assert [update.client_order_id for update in connector._order_tracker.trade_updates] == ["lp"]
    connector._trigger_lp_events_if_needed.assert_called_once_with("lp", "lp-signature")
    connector.rpc.get_transaction_status.assert_awaited_once_with("solana", "mainnet-beta", "lp-signature")


def test_multiple_hashed_lp_orders_keep_their_own_confirmations():
    orders = [make_order(str(i), side=TradeType.RANGE, signature=f"lp-{i}", price=Decimal("0")) for i in range(2)]
    connector = OfflineGateway(orders)
    make_controller(connector)._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status(orders))
    assert [(u.client_order_id, u.exchange_order_id) for u in connector._order_tracker.trade_updates] == [
        ("0", "lp-0"), ("1", "lp-1"),
    ]
    assert connector._trigger_lp_events_if_needed.call_count == 2


@pytest.mark.parametrize("price", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), None])
def test_non_finite_swap_never_gets_synthetic_confirmation_fill(price):
    order = make_order(price=price)
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._balance_refresh_required = False
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([order]))
    connector.rpc.get_transaction_status.assert_not_awaited()
    assert not order.is_done
    assert not connector._order_tracker.trade_updates
    assert controller._balance_refresh_required
    assert controller._recovery_payload()["state"] == "WAITING_FOR_SWAP_RECEIPT"
    assert controller._recovery_payload()["pending_swaps"] == [{
        "order_id": "swap", "trading_pair": "SOL-USDC", "transaction_hash": "swap-signature",
        "trade_type": "BUY",
        "first_seen_at": 1000, "last_error_code": None,
    }]
    assert controller._position_value_quote("sol") is None


@pytest.mark.parametrize("side,amount_in,amount_out", [
    (TradeType.BUY, "1.234", "0.008"),
    (TradeType.SELL, "0.008", "1.234"),
])
def test_native_realized_receipt_releases_pending_and_refreshes_balances(side, amount_in, amount_out):
    order = make_order(side=side)
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([order]))
    controller._balance_refresh_required = False
    connector._store_swap_result(
        order.client_order_id, side, order.trading_pair, order.amount,
        {"data": {"amountIn": amount_in, "amountOut": amount_out, "fee": "0"}},
        order.exchange_order_id,
    )
    # Filled orders can already be absent from the next active polling batch.
    asyncio.run(connector.update_order_status([]))
    assert order.is_filled
    assert not controller._gateway_pending_swap_receipts
    assert controller._balance_refresh_required
    fill, = connector._order_tracker.trade_updates
    assert fill.fill_base_amount == Decimal("0.008")
    assert fill.fill_quote_amount == Decimal("1.234")
    assert fill.fill_price == Decimal("154.25")
    summary = position_summary_from_order(order)
    assert summary.volume_traded_quote == Decimal("1.234")
    assert summary.breakeven_price == Decimal("154.25")
    assert summary.unrealized_pnl_quote.is_finite()


@pytest.mark.parametrize("receipt", [{}, {"data": {"amountIn": "0", "amountOut": "0"}}])
def test_missing_native_receipt_remains_pending(receipt):
    order = make_order()
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    connector._store_swap_result("swap", order.trade_type, order.trading_pair, order.amount, receipt, "swap-signature")
    asyncio.run(connector.update_order_status([order]))
    assert not order.is_done
    assert not connector._order_tracker.trade_updates
    assert "swap" in controller._gateway_pending_swap_receipts


@pytest.mark.parametrize("base,quote", [("NaN", "1"), ("1", "Infinity"), ("0", "0")])
def test_filled_state_without_valid_amounts_cannot_release_pending(base, quote):
    order = make_order()
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([order]))
    order.current_state = OrderState.FILLED
    order.executed_amount_base = Decimal(base)
    order.executed_amount_quote = Decimal(quote)
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts


@pytest.mark.parametrize("terminal_state", [None, OrderState.FAILED, OrderState.CANCELED])
def test_missing_or_failed_order_and_wallet_delta_do_not_release_pending(terminal_state):
    order = make_order()
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([order]))
    controller._wallet_quote = Decimal("200")
    controller._wallet_bases["sol"] = Decimal("1")
    if terminal_state is None:
        connector._order_tracker.orders.clear()
    else:
        order.current_state = terminal_state
    asyncio.run(connector.update_order_status([]))
    controller._tokens_ready = True
    controller._ownership_checked_by_position["sol"] = True
    assert controller.determine_executor_actions() == []
    assert controller._readiness_state == ReadinessState.WAITING_FOR_SETTLEMENT
    assert controller._lifecycle_state == LifecycleState.RECOVERING
    assert controller._position_value_quote("sol") is None
    assert "swap" in controller._gateway_pending_swap_receipts


@pytest.mark.parametrize("error", [RuntimeError("poll error"), asyncio.CancelledError()])
def test_delegate_error_or_cancellation_is_not_hidden(error):
    lp = make_order(side=TradeType.RANGE, price=Decimal("0"))
    connector = OfflineGateway([lp])

    async def failing_poll(orders):
        raise error

    connector.update_order_status = failing_poll
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    with pytest.raises(type(error)):
        asyncio.run(connector.update_order_status([lp]))


def test_invalid_completed_executor_receipt_blocks_new_actions():
    controller = make_controller(OfflineGateway())
    controller._tokens_ready = True
    controller._ownership_checked_by_position["sol"] = True
    executor = SimpleNamespace(
        id="bad-executor", timestamp=950, is_done=True, is_active=False,
        config=SimpleNamespace(type="order_executor", level_id=f"{PREPARE_ROLE}:sol"),
        custom_info={"executed_amount_base": "0.008", "average_executed_price": "NaN"},
        close_type=CloseType.POSITION_HOLD,
    )
    controller.executors_info = [executor]
    assert controller.determine_executor_actions() == []
    assert controller._recovery_payload()["state"] == "WAITING_FOR_SWAP_RECEIPT"
    assert controller._position_error("sol") == "SWAP_RECEIPT_UNAVAILABLE"
    assert controller._position_value_quote("sol") is None


def test_uncertain_swap_allows_balance_refresh_but_no_lp_recovery_close(monkeypatch):
    order = make_order()
    connector = OfflineGateway([order])
    controller = make_controller(connector)
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([order]))
    controller._tokens_ready = True
    controller._ownership_checked_by_position["sol"] = True
    register = AsyncMock(return_value=True)
    recover = AsyncMock()
    monkeypatch.setattr(controller, "_register_tokens", register)
    monkeypatch.setattr(controller, "_recover_failed_lp", recover)
    monkeypatch.setattr(controller, "_fetch_owned_positions", AsyncMock(return_value={"sol": []}))
    monkeypatch.setattr(controller, "_manual_close_recovery_candidate", lambda: SimpleNamespace(
        id="failed-lp", config=SimpleNamespace(pool_address="A" * 32),
    ))
    monkeypatch.setattr(controller, "_pool_info_refresh_due", lambda: False)
    asyncio.run(controller.update_processed_data())
    connector.update_balances.assert_awaited_once()
    recover.assert_not_awaited()
    assert controller._wallet_quote == Decimal("100")
    assert "swap" in controller._gateway_pending_swap_receipts
    assert controller._recovery_payload()["state"] == "WAITING_FOR_SWAP_RECEIPT"


def ready_controller(connector):
    connector._amount_quantum_dict = {}
    controller = make_controller(connector)
    controller._tokens_ready = True
    controller._restart_quarantined = False
    controller._ownership_checked_by_position["sol"] = True
    controller._balance_refresh_required = False
    controller._balance_observed_at = 1000
    controller._capital_preflight_passed = True
    controller._pool_prices["sol"] = Decimal("100")
    controller._wallet_quote = Decimal("10")
    controller._wallet_sol = Decimal("1")
    controller._wallet_bases["sol"] = Decimal("0")
    return controller


def lp_executor(*, exhausted=True):
    return SimpleNamespace(
        id="owned-lp", timestamp=900, is_done=exhausted, is_active=not exhausted,
        close_type=CloseType.POSITION_HOLD if exhausted else None,
        filled_amount_quote=Decimal("9.8"),
        config=SimpleNamespace(type="lp_executor", pool_address="A" * 32, side=TradeType.RANGE),
        custom_info={"state": "HOLD" if exhausted else "IN_RANGE",
                     "hold_reason": "close_retries_exhausted" if exhausted else None,
                     "position_address": "exact-owned-position"},
    )


def install_failure_guard(order):
    connector = OfflineGateway([order])
    # Preserve the native callback contract while modeling delayed FAILED publication.
    callback = Mock()
    connector._handle_operation_failure = callback
    controller = ready_controller(connector)
    controller._install_gateway_polling_guard(connector)
    return connector, controller, callback


def structured_error(code):
    if GatewayError is not None and isinstance(code, str):
        return GatewayError("offline Gateway rejection", code=code)
    error = RuntimeError("offline Gateway rejection")
    error.code = code
    return error


@pytest.mark.parametrize("code", ["TRANSACTION_FAILED", "SIMULATION_FAILED", "INSUFFICIENT_BALANCE",
                                  "SLIPPAGE_EXCEEDED", "NO_ROUTE_FOUND"])
@pytest.mark.parametrize("poll_first", [False, True])
def test_definitive_failure_releases_exact_order_even_before_failed_state_publication(code, poll_first):
    order = make_order()
    connector, controller, callback = install_failure_guard(order)
    if poll_first:
        asyncio.run(connector.update_order_status([order]))
    error = structured_error(code)
    connector._handle_operation_failure("swap", "SOL-USDC", "swap", error)
    callback.assert_called_once_with("swap", "SOL-USDC", "swap", error)
    assert order.current_state == OrderState.OPEN
    controller._balance_refresh_required = False
    asyncio.run(connector.update_order_status([order]))
    assert not controller._gateway_pending_swap_receipts
    assert controller._balance_refresh_required
    connector.rpc.get_transaction_status.assert_not_awaited()
    assert not connector._order_tracker.trade_updates


@pytest.mark.parametrize("code", ["TRANSACTION_TIMEOUT", "UNKNOWN", None, [], {}])
def test_uncertain_or_malformed_failure_remains_blocked_and_delegates(code):
    order = make_order()
    connector, controller, callback = install_failure_guard(order)
    error = structured_error(code)
    connector._handle_operation_failure("swap", "SOL-USDC", "swap", error)
    callback.assert_called_once()
    order.current_state = OrderState.FAILED
    connector._order_tracker.orders.clear()
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts
    assert not controller._gateway_terminal_swap_failures


def test_error_text_is_not_definitive_failure_evidence():
    connector, controller, callback = install_failure_guard(make_order())
    connector._handle_operation_failure("swap", "SOL-USDC", "swap", RuntimeError("[code:TRANSACTION_FAILED]"))
    controller._reconcile_gateway_swap_receipts(connector)
    callback.assert_called_once()
    assert "swap" in controller._gateway_pending_swap_receipts


@pytest.mark.parametrize("base,quote", [("0.01", "1"), ("NaN", "0"), ("0", "Infinity")])
def test_failure_with_execution_or_invalid_amounts_remains_uncertain(base, quote):
    order = make_order()
    order.executed_amount_base = Decimal(base)
    order.executed_amount_quote = Decimal(quote)
    connector, controller, callback = install_failure_guard(order)
    connector._handle_operation_failure("swap", "SOL-USDC", "swap", structured_error("TRANSACTION_FAILED"))
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts
    callback.assert_called_once()


def test_failed_attempt_then_native_replacement_fill_releases_both_holds():
    failed = make_order("failed", signature=None)
    connector, controller, _ = install_failure_guard(failed)
    asyncio.run(connector.update_order_status([failed]))
    connector._handle_operation_failure("failed", "SOL-USDC", "swap", structured_error("TRANSACTION_FAILED"))
    failed.current_state = OrderState.FAILED
    replacement = make_order("replacement", signature="replacement-signature")
    connector._order_tracker.orders["replacement"] = replacement
    asyncio.run(connector.update_order_status([replacement]))
    assert set(controller._gateway_pending_swap_receipts) == {"replacement"}
    connector._store_swap_result("replacement", TradeType.BUY, "SOL-USDC", replacement.amount,
                                 {"data": {"amountIn": "1.234", "amountOut": "0.008", "fee": "0"}},
                                 "replacement-signature")
    controller._reconcile_gateway_swap_receipts(connector)
    assert not controller._gateway_pending_swap_receipts
    assert len(connector._order_tracker.trade_updates) == 1


def cache_expired_receipt():
    order = make_order()
    connector = OfflineGateway([order])
    controller = ready_controller(connector)
    controller._install_gateway_polling_guard(connector)
    asyncio.run(connector.update_order_status([order]))
    connector._store_swap_result("swap", TradeType.BUY, "SOL-USDC", order.amount,
                                 {"data": {"amountIn": "1.234", "amountOut": "0.008", "fee": "0"}},
                                 "swap-signature")
    receipt = order.to_json()
    executor = SimpleNamespace(
        id="filled-executor", controller_id=controller.config.id, timestamp=950,
        is_done=True, is_active=False, close_type=CloseType.POSITION_HOLD,
        config=SimpleNamespace(type="order_executor", level_id=f"{PREPARE_ROLE}:sol",
                               connector_name=controller.config.connector_name, trading_pair="SOL-USDC",
                               side=TradeType.BUY),
        custom_info={"held_position_orders": [receipt], "executed_amount_base": "0.008",
                     "average_executed_price": "154.25"},
    )
    controller.executors_info = [executor]
    connector._order_tracker.orders.clear()
    return connector, controller, executor, receipt


def test_exact_native_executor_receipt_releases_hold_after_cache_expiry():
    connector, controller, _, _ = cache_expired_receipt()
    controller._balance_refresh_required = False
    controller._reconcile_gateway_swap_receipts(connector)
    assert not controller._gateway_pending_swap_receipts
    assert controller._balance_refresh_required


@pytest.mark.parametrize("field,value", [
    ("client_order_id", "replacement"), ("exchange_order_id", "other-signature"),
    ("exchange_order_id", None), ("trading_pair", "OTHER-USDC"),
    ("trade_type", "SELL"),
    ("last_state", str(OrderState.FAILED.value)), ("executed_amount_base", "NaN"),
    ("executed_amount_quote", "0"),
])
def test_cache_expired_receipt_rejects_mismatch_or_invalid_execution(field, value):
    connector, controller, _, receipt = cache_expired_receipt()
    receipt[field] = value
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts


@pytest.mark.parametrize("field,value", [("controller_id", "foreign"), ("is_done", False),
                                       ("close_type", CloseType.FAILED)])
def test_cache_expired_receipt_requires_completed_owned_executor(field, value):
    connector, controller, executor, _ = cache_expired_receipt()
    setattr(executor, field, value)
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts


@pytest.mark.parametrize("receipts", [None, {}, [], [None], [{"executed_amount_quote": "1.234"}]])
def test_aggregate_or_malformed_receipt_cannot_replace_exact_native_order(receipts):
    connector, controller, executor, _ = cache_expired_receipt()
    executor.custom_info["held_position_orders"] = receipts
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts


def test_missing_creation_ack_is_diagnosed_without_automatic_resubmission():
    controller = ready_controller(OfflineGateway())
    action = controller._create_order_action(TradeType.BUY, Decimal("0.005"), PREPARE_ROLE, "sol")
    assert controller._recovery_payload()["state"] == "WAITING_FOR_EXECUTOR_ACK"
    controller.market_data_provider.time = lambda: 1000 + controller.config.failure_retry_backoff_seconds
    assert controller.determine_executor_actions() == []
    recovery = controller._recovery_payload()
    assert recovery["state"] == "EXECUTOR_CREATION_RECONCILIATION_REQUIRED"
    assert recovery["pending_creates"][0]["executor_id"] == action.executor_config.id
    assert recovery["pending_creates"][0]["position_id"] == "sol"
    assert action.executor_config.id in controller._pending_create_ids
    # A delayed acknowledgment clears the hold even when an unrelated gate is closed.
    controller._tokens_ready = False
    controller.executors_info = [SimpleNamespace(id=action.executor_config.id, controller_id=controller.config.id)]
    assert controller.determine_executor_actions() == []
    assert not controller._pending_create_ids
    assert not controller._pending_create_started_at


@pytest.mark.parametrize("reason", [ExitReason.OPERATOR, ExitReason.TIME_LIMIT])
def test_exit_latches_while_swap_is_uncertain_and_reports_exact_blocker(reason):
    connector, controller, _ = install_failure_guard(make_order())
    asyncio.run(connector.update_order_status([make_order()]))
    if reason == ExitReason.OPERATOR:
        controller.config = controller.config.model_copy(update={"exit_requested": True, "exit_reason": reason})
    else:
        controller._controller_started_at = 1000 - controller.config.controller_time_limit_minutes * 60
    assert controller.determine_executor_actions() == []
    assert controller._runtime_exit_reason == reason
    assert {"kind": "SWAP_RECEIPT", "order_id": "swap", "trading_pair": "SOL-USDC"} in controller._recovery_payload()["exit_blockers"]
    assert not controller._stop_requested_ids


def test_autonomous_close_exhaustion_enters_recovery_without_controller_stop_id(monkeypatch):
    controller = ready_controller(OfflineGateway())
    executor = lp_executor()
    controller.executors_info = [executor]
    assert executor.id not in controller._stop_requested_ids
    assert controller._manual_close_recovery_candidate() is executor
    assert controller._recovery_payload()["state"] == "RECONCILING_POSITION"
    classify = AsyncMock(return_value=("present", None))
    submit = AsyncMock(return_value={"data_unavailable": True})
    monkeypatch.setattr(controller, "_classify_onchain_position", classify)
    monkeypatch.setattr(controller, "_submit_manual_close", submit)
    asyncio.run(controller._recover_failed_lp(OfflineGateway()))
    submit.assert_awaited_once()
    assert not controller._ownership_checked_by_position["sol"]


@pytest.mark.parametrize("classification", ["unknown", "conflict", "absent"])
def test_autonomous_close_recovery_reconciles_before_submitting(classification, monkeypatch):
    controller = ready_controller(OfflineGateway())
    executor = lp_executor()
    controller.executors_info = [executor]
    monkeypatch.setattr(controller, "_classify_onchain_position", AsyncMock(return_value=(classification, None)))
    submit = AsyncMock()
    monkeypatch.setattr(controller, "_submit_manual_close", submit)
    asyncio.run(controller._recover_failed_lp(OfflineGateway()))
    submit.assert_not_awaited()
    if classification == "absent":
        assert executor.id in controller._manual_recovery_completed
    elif classification == "conflict":
        assert controller._fault_reason == f"manual_recovery_conflict:{executor.id}"


def test_normal_position_hold_is_not_a_failed_close():
    controller = ready_controller(OfflineGateway())
    executor = lp_executor()
    executor.custom_info["hold_reason"] = "keep_position"
    controller.executors_info = [executor]
    assert controller._manual_close_recovery_candidate() is None


def test_positive_amount_without_explicit_filled_state_does_not_release_hold():
    order = make_order()
    connector, controller, _ = install_failure_guard(order)
    asyncio.run(connector.update_order_status([order]))
    order.executed_amount_base = order.amount
    order.executed_amount_quote = Decimal("1.234")
    assert order.is_filled  # Native amount-based property is not terminal evidence.
    assert order.current_state == OrderState.OPEN
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts


def test_definitive_failure_does_not_ignore_later_contradictory_amounts():
    order = make_order()
    connector, controller, _ = install_failure_guard(order)
    connector._handle_operation_failure("swap", "SOL-USDC", "swap", structured_error("TRANSACTION_FAILED"))
    order.executed_amount_base = Decimal("0.001")
    order.executed_amount_quote = Decimal("0.1")
    controller._reconcile_gateway_swap_receipts(connector)
    assert "swap" in controller._gateway_pending_swap_receipts


def test_foreign_executor_report_cannot_acknowledge_owned_create():
    controller = ready_controller(OfflineGateway())
    action = controller._create_order_action(TradeType.BUY, Decimal("0.005"), PREPARE_ROLE, "sol")
    controller.executors_info = [SimpleNamespace(id=action.executor_config.id, controller_id="foreign")]
    controller._reconcile_pending_creates()
    assert action.executor_config.id in controller._pending_create_ids


def test_lp_create_also_records_acknowledgment_age_and_exit_blocker():
    controller = ready_controller(OfflineGateway())
    action = controller._create_lp_action(TradeType.RANGE, Decimal("0.04"), Decimal("4"), "sol")
    controller.config = controller.config.model_copy(update={"exit_requested": True, "exit_reason": ExitReason.OPERATOR})
    controller.market_data_provider.time = lambda: 1000 + controller.config.failure_retry_backoff_seconds
    assert controller.determine_executor_actions() == []
    assert controller._position_error("sol") == "EXECUTOR_CREATION_RECONCILIATION_REQUIRED"
    assert {"kind": "EXECUTOR_ACK", "executor_id": action.executor_config.id,
            "position_id": "sol"} in controller._recovery_payload()["exit_blockers"]


def test_acknowledgment_clears_even_during_gateway_read_backoff():
    controller = ready_controller(OfflineGateway())
    action = controller._create_order_action(TradeType.BUY, Decimal("0.005"), PREPARE_ROLE, "sol")
    controller.executors_info = [SimpleNamespace(id=action.executor_config.id, controller_id=controller.config.id)]
    controller._gateway_read_retry_after = 2000
    asyncio.run(controller.update_processed_data())
    assert not controller._pending_create_ids
    assert not controller._pending_create_positions
    assert not controller._pending_create_started_at


def test_exit_telemetry_latches_time_limit_before_any_readiness_gate():
    controller = make_controller(OfflineGateway())
    controller._controller_started_at = 1000 - controller.config.controller_time_limit_minutes * 60
    info = controller.get_custom_info()
    assert info["schema_version"] == 3
    assert info["lifecycle_state"] == "EXITING"
    assert info["exit_reason"] == "time_limit"
    assert {"kind": "TOKEN_REGISTRATION"} in info["recovery"]["exit_blockers"]


def test_uncertain_swap_cannot_trigger_profit_or_loss_exit_from_stale_pnl(monkeypatch):
    connector, controller, _ = install_failure_guard(make_order())
    asyncio.run(connector.update_order_status([make_order()]))
    controller._controller_started_at = 1000 - controller.config.controller_pnl_grace_period_minutes * 60
    pnl = Mock(return_value=Decimal("100"))
    monkeypatch.setattr(controller, "_pnl_quote", pnl)
    assert controller._determine_executor_actions() == []
    assert controller._runtime_exit_reason == ExitReason.NONE
    pnl.assert_not_called()


def test_autonomous_close_timeout_rechecks_ownership_and_does_not_retry_immediately(monkeypatch):
    controller = ready_controller(OfflineGateway())
    executor = lp_executor()
    controller.executors_info = [executor]
    classify = AsyncMock(return_value=("present", None))
    submit = AsyncMock(side_effect=TimeoutError("offline close result unknown"))
    monkeypatch.setattr(controller, "_classify_onchain_position", classify)
    monkeypatch.setattr(controller, "_submit_manual_close", submit)
    connector = OfflineGateway()
    asyncio.run(controller._recover_failed_lp(connector))
    asyncio.run(controller._recover_failed_lp(connector))
    submit.assert_awaited_once()
    assert controller._manual_recovery_errors[executor.id] == "manual_close_uncertain"
    controller.market_data_provider.time = lambda: 1000 + controller.config.failure_retry_backoff_seconds
    classify.return_value = ("unknown", None)
    asyncio.run(controller._recover_failed_lp(connector))
    assert classify.await_count == 2
    submit.assert_awaited_once()


def test_autonomous_close_recovery_stops_at_configured_attempt_limit(monkeypatch):
    controller = ready_controller(OfflineGateway())
    executor = lp_executor()
    controller.executors_info = [executor]
    controller._manual_recovery_attempts[executor.id] = controller.config.max_consecutive_controller_failures
    monkeypatch.setattr(controller, "_classify_onchain_position", AsyncMock(return_value=("present", None)))
    submit = AsyncMock()
    monkeypatch.setattr(controller, "_submit_manual_close", submit)
    asyncio.run(controller._recover_failed_lp(OfflineGateway()))
    submit.assert_not_awaited()
    assert controller._recovery_payload()["state"] == "MANUAL_CLOSE_EXHAUSTED"


@pytest.mark.parametrize("code,remains_pending", [("TRANSACTION_FAILED", False), ("TRANSACTION_TIMEOUT", True)])
def test_guard_preserves_native_failure_state_update(code, remains_pending):
    order = make_order(signature=None)
    connector = OfflineGateway([order])
    controller = ready_controller(connector)
    controller._install_gateway_polling_guard(connector)
    connector._handle_operation_failure("swap", "SOL-USDC", "swap", structured_error(code))
    assert order.current_state == OrderState.FAILED
    assert len(connector._order_tracker.order_updates) == 1
    controller._reconcile_gateway_swap_receipts(connector)
    assert ("swap" in controller._gateway_pending_swap_receipts) is remains_pending
    assert not connector._order_tracker.trade_updates


def test_lp_failure_delegates_to_native_handler_without_creating_swap_hold():
    order = make_order("lp", side=TradeType.RANGE, price=Decimal("0"))
    connector = OfflineGateway([order])
    controller = ready_controller(connector)
    controller._install_gateway_polling_guard(connector)
    connector._handle_operation_failure("lp", "SOL-USDC", "close", structured_error("TRANSACTION_FAILED"))
    assert order.current_state == OrderState.FAILED
    assert not controller._gateway_pending_swap_receipts
