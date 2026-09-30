"""Trend-aware, quote-funded concentrated-liquidity trading session.

The controller supervises one LP on each configured pool, serializes mutations,
and keeps each allocation's inventory isolated. Executor history is the
attribution ledger; wallet balances are only a canonical readiness ceiling.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.gateway_utils import parse_provider
from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo
from pydantic import Field, field_validator, model_validator

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
PREPARE_ROLE = "trend_aware_lp:prepare"
CLEANUP_ROLE = "trend_aware_lp:cleanup"
SCHEMA_VERSION = 3
LP_DEPLOYMENT_DELAY_SECONDS = 3.0
LP_POSITION_REFRESH_INTERVAL_SECONDS = 15.0
LP_POOL_INFO_REFRESH_INTERVAL_SECONDS = 60.0
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class MarketTrend(str, Enum):
    UP = "UP"
    SIDEWAYS = "SIDEWAYS"
    DOWN = "DOWN"


class ExitReason(str, Enum):
    NONE = "none"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_LIMIT = "time_limit"
    OPERATOR = "operator"
    FAULT = "fault"


class LifecycleState(str, Enum):
    RECOVERING = "RECOVERING"
    PREPARING = "PREPARING"
    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    CLEANING = "CLEANING"
    WAITING_FOR_TREND_REFRESH = "WAITING_FOR_TREND_REFRESH"
    BLOCKED = "BLOCKED"
    EXITED = "EXITED"
    FAULTED = "FAULTED"


class ReadinessState(str, Enum):
    READY = "READY"
    SWAP_REQUIRED = "SWAP_REQUIRED"
    WAITING_FOR_SETTLEMENT = "WAITING_FOR_SETTLEMENT"
    BLOCKED_INSUFFICIENT_BALANCE = "BLOCKED_INSUFFICIENT_BALANCE"
    BLOCKED_BALANCE_UNAVAILABLE = "BLOCKED_BALANCE_UNAVAILABLE"
    BLOCKED_PRICE_UNAVAILABLE = "BLOCKED_PRICE_UNAVAILABLE"
    BLOCKED_TOKEN_REGISTRATION = "BLOCKED_TOKEN_REGISTRATION"
    BLOCKED_TOKEN_SYMBOL_MISMATCH = "BLOCKED_TOKEN_SYMBOL_MISMATCH"
    BLOCKED_TREND = "BLOCKED_TREND"
    BLOCKED_OWNERSHIP_UNAVAILABLE = "BLOCKED_OWNERSHIP_UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


UPDATABLE = {"is_updatable": True, "prompt_on_new": False, "prompt": None}
IMMUTABLE = {"is_updatable": False, "prompt_on_new": False, "prompt": None}


class LPPositionConfig(BaseClientModel):
    """Immutable pool allocation plus the four fields used for the next LP formation."""

    position_id: str = Field(min_length=1)
    trading_pair: str
    pool_address: str = Field(min_length=32, max_length=44)
    base_token_mint: str = Field(min_length=32, max_length=44)
    allocation_pct: Decimal = Field(gt=ZERO, le=ONE_HUNDRED)
    market_trend: MarketTrend
    position_width_pct: Decimal = Field(gt=ZERO, lt=ONE_HUNDRED)
    downside_offset_pct: Decimal = Field(ge=ZERO, lt=ONE_HUNDRED)
    rebalance_threshold_pct: Decimal = Field(gt=ZERO, le=ONE_HUNDRED)

    @field_validator(
        "allocation_pct",
        "position_width_pct",
        "downside_offset_pct",
        "rebalance_threshold_pct",
        mode="before",
    )
    @classmethod
    def finite_decimals(cls, value):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("must be a finite decimal") from exc
        if not parsed.is_finite():
            raise ValueError("must be a finite decimal")
        return parsed

    @field_validator("position_id", "pool_address", "base_token_mint")
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("trading_pair")
    @classmethod
    def validate_pair(cls, value: str) -> str:
        parts = value.split("-")
        if len(parts) != 2 or not all(parts):
            raise ValueError("trading_pair must be exactly BASE-QUOTE")
        if parts[1] != "USDC":
            raise ValueError("the first iteration supports USDC as quote token only")
        return value

    @model_validator(mode="after")
    def validate_contract(self):
        if not SOLANA_ADDRESS.fullmatch(self.pool_address):
            raise ValueError("pool_address must be a Solana base58 address")
        if not SOLANA_ADDRESS.fullmatch(self.base_token_mint):
            raise ValueError("base_token_mint must be a Solana base58 address")
        if self.market_trend in {MarketTrend.UP, MarketTrend.SIDEWAYS} and self.downside_offset_pct != ZERO:
            raise ValueError("downside_offset_pct must be 0 for UP and SIDEWAYS")
        if self.position_width_pct + self.downside_offset_pct >= ONE_HUNDRED:
            raise ValueError("position_width_pct + downside_offset_pct must be below 100")
        return self

    def immutable_identity(self) -> Tuple[str, str, str, str, Decimal]:
        return (
            self.position_id,
            self.trading_pair,
            self.pool_address,
            self.base_token_mint,
            self.allocation_pct,
        )


class TrendAwareLPRebalancerConfig(ControllerConfigBase):
    """Strict configuration for one trading session containing one or more unique pools."""

    id: str = Field(min_length=1, json_schema_extra=IMMUTABLE)
    controller_type: Literal["generic"] = Field(default="generic", json_schema_extra=IMMUTABLE)
    controller_name: Literal["trend_aware_lp_rebalancer"] = Field(
        default="trend_aware_lp_rebalancer", json_schema_extra=IMMUTABLE
    )
    candles_config: List[CandlesConfig] = Field(default_factory=list, json_schema_extra=IMMUTABLE)
    manual_kill_switch: bool = Field(default=False, json_schema_extra=IMMUTABLE)

    connector_name: Literal["solana-mainnet-beta"] = Field(
        default="solana-mainnet-beta", json_schema_extra=IMMUTABLE
    )
    lp_provider: Literal["orca/clmm"] = Field(default="orca/clmm", json_schema_extra=IMMUTABLE)
    swap_provider: Literal["jupiter/router"] = Field(default="jupiter/router", json_schema_extra=IMMUTABLE)
    quote_token_mint: str = Field(min_length=32, max_length=44, json_schema_extra=IMMUTABLE)
    total_amount_quote: Decimal = Field(gt=ZERO, json_schema_extra=IMMUTABLE)
    lp_positions: List[LPPositionConfig] = Field(min_length=1, json_schema_extra=UPDATABLE)
    lp_sizing_buffer_pct: Decimal = Field(
        default=Decimal("2"),
        ge=Decimal("2"),
        lt=Decimal("10"),
        json_schema_extra=IMMUTABLE,
    )

    min_sol_reserve: Decimal = Field(default=Decimal("0.02"), ge=ZERO, json_schema_extra=IMMUTABLE)
    cleanup_min_quote_value: Decimal = Field(default=Decimal("0.01"), ge=ZERO, json_schema_extra=IMMUTABLE)
    rebalance_cooldown_minutes: int = Field(default=5, ge=0, le=1440, json_schema_extra=IMMUTABLE)
    max_consecutive_controller_failures: int = Field(default=3, ge=1, le=10, json_schema_extra=IMMUTABLE)
    failure_retry_backoff_seconds: int = Field(default=30, ge=0, le=3600, json_schema_extra=IMMUTABLE)

    controller_take_profit_ratio: Decimal = Field(
        default=Decimal("0.05"), gt=ZERO, le=Decimal("1"), json_schema_extra=IMMUTABLE
    )
    controller_stop_loss_ratio: Decimal = Field(
        default=Decimal("0.05"), gt=ZERO, le=Decimal("1"), json_schema_extra=IMMUTABLE
    )
    controller_time_limit_minutes: int = Field(default=720, gt=0, le=525600, json_schema_extra=IMMUTABLE)
    controller_pnl_grace_period_minutes: int = Field(default=5, ge=0, le=525600, json_schema_extra=IMMUTABLE)
    exit_requested: bool = Field(default=False, json_schema_extra=UPDATABLE)
    exit_reason: ExitReason = Field(default=ExitReason.NONE, json_schema_extra=UPDATABLE)

    @field_validator(
        "total_amount_quote",
        "lp_sizing_buffer_pct",
        "min_sol_reserve",
        "cleanup_min_quote_value",
        "controller_take_profit_ratio",
        "controller_stop_loss_ratio",
        mode="before",
    )
    @classmethod
    def finite_decimals(cls, value):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("must be a finite decimal") from exc
        if not parsed.is_finite():
            raise ValueError("must be a finite decimal")
        return parsed

    @field_validator("quote_token_mint")
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_contract(self):
        if self.quote_token_mint != USDC_MINT:
            raise ValueError("the first iteration supports canonical USDC as quote mint only")
        if any(position.base_token_mint == self.quote_token_mint for position in self.lp_positions):
            raise ValueError("base and quote token mints must differ")
        position_ids = [position.position_id for position in self.lp_positions]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("position_id must be unique")
        pool_addresses = [position.pool_address for position in self.lp_positions]
        if len(pool_addresses) != len(set(pool_addresses)):
            raise ValueError("pool_address must be unique")
        if sum((position.allocation_pct for position in self.lp_positions), ZERO) != ONE_HUNDRED:
            raise ValueError("lp_positions allocation_pct must sum to 100")
        if self.controller_pnl_grace_period_minutes >= self.controller_time_limit_minutes:
            raise ValueError("controller_pnl_grace_period_minutes must be below controller_time_limit_minutes")
        if self.exit_requested and self.exit_reason == ExitReason.NONE:
            raise ValueError("exit_requested=true requires a non-none exit_reason")
        if not self.exit_requested and self.exit_reason != ExitReason.NONE:
            raise ValueError("exit_reason must be none until exit_requested=true")
        if self.initial_positions:
            raise ValueError("initial_positions are not supported; this controller begins from quote")
        return self

    def update_markets(self, markets: MarketDict) -> MarketDict:
        for position in self.lp_positions:
            markets.add_or_update(self.connector_name, position.trading_pair)
        return markets


class TrendAwareLPRebalancer(ControllerBase):
    """One trading session supervising one LP on each configured pool."""

    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    @staticmethod
    def _normalize_manual_kill_switch(
        config: TrendAwareLPRebalancerConfig,
    ) -> Tuple[TrendAwareLPRebalancerConfig, bool]:
        if not config.manual_kill_switch:
            return config, False
        return (
            config.model_copy(
                update={
                    "manual_kill_switch": False,
                    "exit_requested": True,
                    "exit_reason": ExitReason.OPERATOR,
                }
            ),
            True,
        )

    def __init__(self, config: TrendAwareLPRebalancerConfig, *args, **kwargs):
        config, kill_switch_translated = self._normalize_manual_kill_switch(config)
        super().__init__(config, *args, **kwargs)
        self.config: TrendAwareLPRebalancerConfig = config
        self._lp_dex_name, self._lp_trading_type = parse_provider(config.lp_provider)
        self._positions = {position.position_id: position for position in config.lp_positions}
        self._position_id_by_pool = {position.pool_address: position.position_id for position in config.lp_positions}
        self._base_tokens = {
            position.position_id: position.trading_pair.split("-")[0] for position in config.lp_positions
        }
        self._quote_token = "USDC"
        self._pool_prices: Dict[str, Optional[Decimal]] = dict.fromkeys(self._positions)
        self._last_pool_info_refresh_timestamp: Optional[float] = None
        self._wallet_bases: Dict[str, Optional[Decimal]] = dict.fromkeys(self._positions)
        self._ownership_checked_by_position = dict.fromkeys(self._positions, False)
        self._ownership_errors: Dict[str, Optional[str]] = dict.fromkeys(self._positions)
        self._pool_orientation_errors: Dict[str, Optional[str]] = dict.fromkeys(self._positions)
        self._owned_position_addresses: Dict[str, List[str]] = {position_id: [] for position_id in self._positions}
        self._current_formations: Dict[str, Optional[LPPositionConfig]] = dict.fromkeys(self._positions)
        self._rearm_source_lp_ids: Dict[str, Optional[str]] = dict.fromkeys(self._positions)
        self._last_position_addresses: Dict[str, Optional[str]] = dict.fromkeys(self._positions)
        self._wallet_quote: Optional[Decimal] = None
        self._wallet_sol: Optional[Decimal] = None
        self._capital_preflight_passed = False
        self._capital_preflight_failure: Optional[str] = None
        self._balance_refresh_required = True
        self._balance_observed_at: Optional[float] = None
        self._settled_order_ids: set[str] = set()
        self._observed_landed_lp_ids: set[str] = set()
        self._settled_lp_ids: set[str] = set()
        self._lp_close_directions: Dict[str, str] = {}
        self._pending_create_ids: set[str] = set()
        self._pending_create_positions: Dict[str, str] = {}
        self._stop_requested_ids: set[str] = set()
        self._runtime_exit_reason: ExitReason = ExitReason.NONE
        self._controller_started_at = self._now()
        self._fault_reason: Optional[str] = None
        self._terminal_position_pnl_quote: Optional[Dict[str, Optional[Decimal]]] = None
        self._terminal_pnl_quote: Optional[Decimal] = None
        self._tokens_ready = False
        self._token_registration_error: Optional[str] = None
        self._token_symbol_errors: Dict[str, Optional[str]] = dict.fromkeys(self._positions)
        self._token_registration_retry_after = 0.0
        self._last_logged_exit_cleanup_blockers: Tuple[Tuple[str, str, int], ...] = ()
        self._last_logged_session_status: Tuple[str, str, Optional[str]] = ("RUNNING", "none", None)
        self._restart_quarantined: Optional[bool] = None
        self._lifecycle_state = LifecycleState.BLOCKED
        self._readiness_state = ReadinessState.BLOCKED_BALANCE_UNAVAILABLE
        self._pending_failure_reconciliation: set[str] = set()
        self._reconciled_failure_ids: set[str] = set()
        self._manual_recovery_attempts: Dict[str, int] = {}
        self._manual_recovery_retry_after: Dict[str, float] = {}
        self._manual_recovery_snapshots: Dict[str, Dict] = {}
        self._manual_close_receipts: Dict[str, Dict] = {}
        self._manual_recovery_errors: Dict[str, str] = {}
        self._manual_recovery_completed: set[str] = set()
        self._gateway_read_retry_after = 0.0
        self._gateway_read_failure_count = 0
        self._gateway_read_error: Optional[str] = None
        self._gateway_outage_started_at: Optional[float] = None
        self._manual_kill_switch_translated = kill_switch_translated
        self.market_data_provider.initialize_rate_sources(
            [
                ConnectorPair(connector_name=config.connector_name, trading_pair=position.trading_pair)
                for position in config.lp_positions
            ]
        )
        if kill_switch_translated:
            self.logger().warning(
                "Translated manual_kill_switch=true into the controller-native operator exit path"
            )

    def update_config(self, new_config: ControllerConfigBase):
        if not isinstance(new_config, TrendAwareLPRebalancerConfig):
            self.logger().warning("Rejected controller reload with an incompatible config type")
            return
        new_config, kill_switch_translated = self._normalize_manual_kill_switch(new_config)
        mutable_fields = {"lp_positions", "exit_requested", "exit_reason"}
        if any(
            getattr(self.config, name) != getattr(new_config, name)
            for name in self.config.__class__.model_fields
            if name not in mutable_fields
        ):
            self.logger().warning("Rejected controller reload that changed immutable session fields")
            return

        current = {position.position_id: position for position in self.config.lp_positions}
        candidate = {position.position_id: position for position in new_config.lp_positions}
        if current.keys() != candidate.keys() or any(
            current[position_id].immutable_identity() != candidate[position_id].immutable_identity()
            for position_id in current
        ):
            self.logger().warning("Rejected controller reload that changed position identity or allocation")
            return

        formation_fields = (
            "market_trend",
            "position_width_pct",
            "downside_offset_pct",
            "rebalance_threshold_pct",
        )
        positions = [
            position.model_copy(
                update={field: getattr(candidate[position.position_id], field) for field in formation_fields}
            )
            for position in self.config.lp_positions
        ]
        self.config = self.config.model_copy(
            update={
                "lp_positions": positions,
                "exit_requested": new_config.exit_requested,
                "exit_reason": new_config.exit_reason,
            }
        )
        self._positions = {position.position_id: position for position in positions}
        if kill_switch_translated and not self._manual_kill_switch_translated:
            self.logger().warning(
                "Translated manual_kill_switch=true into the controller-native operator exit path"
            )
        self._manual_kill_switch_translated = self._manual_kill_switch_translated or kill_switch_translated
        self._latch_configured_exit()

    def _now(self) -> float:
        try:
            return float(self.market_data_provider.time())
        except Exception:
            return 0.0

    @staticmethod
    def _decimal(value, default: Decimal = ZERO) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return default
        return result if result.is_finite() else default

    def _latch_fault(self, reason: str):
        if self._fault_reason is None:
            self._fault_reason = reason
        if self._runtime_exit_reason == ExitReason.NONE:
            self._runtime_exit_reason = ExitReason.FAULT

    @staticmethod
    def _error_text(context: str, exc: Exception) -> str:
        return f"{context}:{type(exc).__name__}:{exc}"[:160]

    def _gateway_read_backoff_active(self) -> bool:
        return self._now() < self._gateway_read_retry_after

    def _pool_info_refresh_due(self) -> bool:
        now = self._now()
        last_refresh = self._last_pool_info_refresh_timestamp
        return (
            last_refresh is None
            or now < last_refresh
            or self._gateway_read_error is not None
            or now - last_refresh >= LP_POOL_INFO_REFRESH_INTERVAL_SECONDS
        )

    def _record_gateway_read_failure(self, context: str, exc: Exception):
        now = self._now()
        if self._gateway_outage_started_at is None:
            self._gateway_outage_started_at = now
        self._gateway_read_failure_count += 1
        self._gateway_read_error = self._error_text(context, exc)
        self._gateway_read_retry_after = now + self.config.failure_retry_backoff_seconds
        self.logger().warning(
            f"Gateway read failed during {context}; controller mutations are paused for "
            f"{self.config.failure_retry_backoff_seconds}s: {type(exc).__name__}: {exc}"
        )

    def _clear_gateway_read_failure(self):
        if self._gateway_read_failure_count:
            outage_seconds = (
                max(0.0, self._now() - self._gateway_outage_started_at)
                if self._gateway_outage_started_at is not None
                else 0.0
            )
            self.logger().info(
                f"Gateway reads recovered after {outage_seconds:.1f}s and "
                f"{self._gateway_read_failure_count} failed refresh attempt(s)"
            )
        self._gateway_read_retry_after = 0.0
        self._gateway_read_failure_count = 0
        self._gateway_read_error = None
        self._gateway_outage_started_at = None

    async def _register_tokens(self, connector) -> bool:
        if self._tokens_ready:
            return True
        if self._now() < self._token_registration_retry_after:
            return False
        try:
            gateway = connector._get_gateway_instance()
            addresses = [
                self.config.quote_token_mint,
                *(position.base_token_mint for position in self.config.lp_positions),
            ]
            for address in addresses:
                await gateway.add_token_by_address(address, connector.chain, connector.network)
            await connector.load_token_data()
            quote_info = connector.get_token_by_address(self.config.quote_token_mint)
            quote_symbol = quote_info.get("symbol") if isinstance(quote_info, dict) else None
            if not isinstance(quote_symbol, str) or not quote_symbol:
                raise ValueError(
                    f"configured quote mint has no canonical Gateway symbol: {self.config.quote_token_mint}"
                )
            canonical_base_tokens: Dict[str, str] = {}
            symbol_errors: Dict[str, Optional[str]] = {}
            for position_id, position in self._positions.items():
                base_info = connector.get_token_by_address(position.base_token_mint)
                base_symbol = base_info.get("symbol") if isinstance(base_info, dict) else None
                if not isinstance(base_symbol, str) or not base_symbol:
                    raise ValueError(
                        f"configured base mint has no canonical Gateway symbol: {position.base_token_mint}"
                    )
                canonical_base_tokens[position_id] = base_symbol
                configured_base, configured_quote = position.trading_pair.split("-")
                canonical_pair = f"{base_symbol}-{quote_symbol}"
                symbol_errors[position_id] = (
                    None
                    if (configured_base, configured_quote) == (base_symbol, quote_symbol)
                    else f"token_symbol_mismatch:{position.trading_pair}:{canonical_pair}"
                )
            # Keep the configured pairs unchanged for traceability, but always
            # read wallet balances by Gateway's canonical, case-sensitive symbols.
            # This keeps exit cleanup available for exposure created before a
            # symbol mismatch was detected.
            self._base_tokens = canonical_base_tokens
            self._quote_token = quote_symbol
            self._token_symbol_errors = symbol_errors
            self._tokens_ready = True
            self._token_registration_error = None
            mismatches = [f"{position_id}:{error}" for position_id, error in symbol_errors.items() if error is not None]
            if mismatches:
                self._readiness_state = ReadinessState.BLOCKED_TOKEN_SYMBOL_MISMATCH
                self.logger().warning(
                    f"Controller {self.config.id} detected configured token symbol mismatch; "
                    f"new exposure is blocked but exit cleanup remains enabled: " + ",".join(mismatches)
                )
            else:
                self.logger().info(
                    f"Controller {self.config.id} refreshed Gateway registration for {len(addresses)} token mint(s)"
                )
            return True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if error != self._token_registration_error:
                self.logger().warning(f"Controller {self.config.id} could not register configured tokens: {error}")
            self._token_registration_error = error
            self._token_registration_retry_after = self._now() + self.config.failure_retry_backoff_seconds
            self._readiness_state = ReadinessState.BLOCKED_TOKEN_REGISTRATION
            return False

    @staticmethod
    def _executor_type(executor: ExecutorInfo) -> str:
        return getattr(executor.config, "type", getattr(executor, "type", ""))

    def _lp_executors(self) -> List[ExecutorInfo]:
        return sorted(
            [executor for executor in self.executors_info if self._executor_type(executor) == "lp_executor"],
            key=lambda executor: (executor.timestamp, executor.id),
        )

    def _position_id_for_lp(self, executor: ExecutorInfo) -> Optional[str]:
        return self._position_id_by_pool.get(str(getattr(executor.config, "pool_address", "")))

    @staticmethod
    def _position_id_for_order(executor: ExecutorInfo) -> Optional[str]:
        level_id = TrendAwareLPRebalancer._order_role(executor)
        for role in (PREPARE_ROLE, CLEANUP_ROLE):
            prefix = f"{role}:"
            if level_id.startswith(prefix):
                return level_id[len(prefix):] or None
        return None

    def _position_lp_executors(self, position_id: str) -> List[ExecutorInfo]:
        return [executor for executor in self._lp_executors() if self._position_id_for_lp(executor) == position_id]

    def _position_order_executors(self, position_id: str) -> List[ExecutorInfo]:
        return [executor for executor in self._order_executors() if self._position_id_for_order(executor) == position_id]

    def _order_executors(self) -> List[ExecutorInfo]:
        return sorted(
            [executor for executor in self.executors_info if self._executor_type(executor) == "order_executor"],
            key=lambda executor: (executor.timestamp, executor.id),
        )

    @staticmethod
    def _order_role(executor: ExecutorInfo) -> str:
        return str(getattr(executor.config, "level_id", None) or executor.custom_info.get("level_id") or "")

    def _active_lps(self) -> List[ExecutorInfo]:
        return [executor for executor in self._lp_executors() if executor.is_active]

    def _in_flight_lps(self) -> List[ExecutorInfo]:
        return [executor for executor in self._lp_executors() if not executor.is_done]

    def _active_orders(self) -> List[ExecutorInfo]:
        return [executor for executor in self._order_executors() if executor.is_active]

    @staticmethod
    def _lp_failed(executor: ExecutorInfo) -> bool:
        return executor.close_type == CloseType.FAILED or str(executor.custom_info.get("state", "")) == "FAILED"

    @staticmethod
    def _close_retries_exhausted(executor: ExecutorInfo) -> bool:
        return (
            executor.close_type == CloseType.POSITION_HOLD
            and str(executor.custom_info.get("hold_reason", "")) == "close_retries_exhausted"
        )

    @staticmethod
    def _order_failed(executor: ExecutorInfo) -> bool:
        return executor.close_type in {CloseType.FAILED, CloseType.INSUFFICIENT_BALANCE, CloseType.EARLY_STOP}

    @staticmethod
    def _landed_lp(executor: ExecutorInfo) -> bool:
        custom = executor.custom_info
        if custom.get("position_address"):
            return True
        # LPExecutor falls back to configured initial amounts in custom_info before
        # an open lands.  filled_amount_quote, however, remains zero until actual
        # deposited amounts have been recorded, including a create that landed and
        # was then recovered under CloseType.FAILED.
        if TrendAwareLPRebalancer._decimal(getattr(executor, "filled_amount_quote", ZERO)) > 0:
            return True
        return False

    def _recovered_failed_lp(self, executor: ExecutorInfo) -> bool:
        return executor.id in self._manual_recovery_completed or (
            executor.is_done
            and TrendAwareLPRebalancer._lp_failed(executor)
            and str(executor.custom_info.get("state", "")) == "COMPLETE"
            and not executor.custom_info.get("position_address")
            and TrendAwareLPRebalancer._decimal(getattr(executor, "filled_amount_quote", ZERO)) > 0
        )

    def _lp_assets_returned(self, executor: ExecutorInfo) -> bool:
        return self._recovered_failed_lp(executor) or (
            not self._lp_failed(executor) and not self._close_retries_exhausted(executor)
        )

    def _inventory_ledger(self, position_id: str) -> Tuple[Decimal, Decimal]:
        """Replay only the executions mapped to one immutable position allocation."""
        position = self._current_formations[position_id] or self._positions[position_id]
        base = ZERO
        quote = self.config.total_amount_quote * position.allocation_pct / ONE_HUNDRED
        for executor in sorted(self.executors_info, key=lambda item: (item.timestamp, item.id)):
            executor_type = self._executor_type(executor)
            custom = executor.custom_info or {}
            if executor_type == "order_executor":
                if self._position_id_for_order(executor) != position_id:
                    continue
                role = self._order_role(executor)
                if not executor.is_done or self._order_failed(executor):
                    continue
                amount = self._decimal(custom.get("executed_amount_base"))
                price = self._decimal(custom.get("average_executed_price"))
                if amount <= 0 or price <= 0:
                    continue
                if role.startswith(PREPARE_ROLE):
                    base += amount
                    quote -= amount * price
                elif role.startswith(CLEANUP_ROLE):
                    base -= amount
                    quote += amount * price
            elif (
                executor_type == "lp_executor"
                and self._position_id_for_lp(executor) == position_id
                and self._landed_lp(executor)
            ):
                initial_base = self._decimal(custom.get("initial_base_amount"))
                initial_quote = self._decimal(custom.get("initial_quote_amount"))
                base -= initial_base
                quote -= initial_quote
                if executor.is_done and self._lp_assets_returned(executor):
                    returned = self._manual_close_receipts.get(executor.id, custom)
                    base += self._decimal(returned.get("base_amount")) + self._decimal(returned.get("base_fee"))
                    quote += self._decimal(returned.get("quote_amount")) + self._decimal(returned.get("quote_fee"))
        epsilon = Decimal("0.000000000001")
        if abs(base) < epsilon:
            base = ZERO
        if abs(quote) < epsilon:
            quote = ZERO
        return base, quote

    def _canonical_balance(self, token: str) -> Optional[Decimal]:
        try:
            value = self._decimal(
                self.market_data_provider.get_balance(self.config.connector_name, token), default=Decimal("NaN")
            )
            return value if value.is_finite() and value >= 0 else None
        except Exception:
            return None

    def _available_wallet_base(self, position_id: str) -> Optional[Decimal]:
        wallet_base = self._wallet_bases[position_id]
        if wallet_base is None:
            return None
        if self._base_tokens[position_id] == "SOL":
            return max(ZERO, wallet_base - self.config.min_sol_reserve)
        return wallet_base

    def _sol_reserve_ready(self) -> bool:
        if self._wallet_sol is None:
            self._readiness_state = ReadinessState.BLOCKED_BALANCE_UNAVAILABLE
            return False
        if self._wallet_sol < self.config.min_sol_reserve:
            self._readiness_state = ReadinessState.BLOCKED_INSUFFICIENT_BALANCE
            return False
        return True

    def _capital_preflight_ready(self) -> bool:
        """Latch proof that the wallet funded the session before its first exposure-increasing mutation."""
        if self._capital_preflight_passed:
            return True
        if self._wallet_quote is None or self._wallet_sol is None:
            failure = "balance_unavailable"
            self._readiness_state = ReadinessState.BLOCKED_BALANCE_UNAVAILABLE
            detail = "wallet USDC or SOL balance is unavailable"
        else:
            quote_short = self._wallet_quote < self.config.total_amount_quote
            sol_short = self._wallet_sol < self.config.min_sol_reserve
            if not quote_short and not sol_short:
                self._capital_preflight_passed = True
                self._capital_preflight_failure = None
                self.logger().info(
                    f"Controller {self.config.id} passed capital preflight with "
                    f"{self._wallet_quote} USDC and {self._wallet_sol} SOL"
                )
                return True
            failure = (
                "insufficient_quote_and_sol"
                if quote_short and sol_short
                else "insufficient_quote"
                if quote_short
                else "insufficient_sol"
            )
            self._readiness_state = ReadinessState.BLOCKED_INSUFFICIENT_BALANCE
            detail = (
                f"wallet has {self._wallet_quote} USDC and {self._wallet_sol} SOL; "
                f"requires {self.config.total_amount_quote} USDC and {self.config.min_sol_reserve} SOL"
            )
        if failure != self._capital_preflight_failure:
            self.logger().warning(f"Controller {self.config.id} capital preflight blocked: {detail}")
        self._capital_preflight_failure = failure
        return False

    def _successful_cleanup_in_current_generation(self, position_id: str) -> bool:
        latest_lp_timestamp = max(
            (executor.timestamp for executor in self._position_lp_executors(position_id)),
            default=float("-inf"),
        )
        return any(
            order.timestamp >= latest_lp_timestamp
            and order.is_done
            and not self._order_failed(order)
            and self._decimal(order.custom_info.get("executed_amount_base")) > 0
            for order in self._position_order_executors(position_id)
            if self._order_role(order).startswith(CLEANUP_ROLE)
        )

    def _executable_cleanup_base(self, attributed_base: Decimal, position_id: str) -> Optional[Decimal]:
        wallet_available = self._available_wallet_base(position_id)
        if wallet_available is None:
            return None
        return self._quantized_base_amount(
            min(max(attributed_base, ZERO), wallet_available),
            position_id,
        )

    def _material_base(self, attributed_base: Decimal, position_id: str) -> bool:
        price = self._pool_prices[position_id]
        amount = self._quantized_base_amount(attributed_base, position_id)
        if self._successful_cleanup_in_current_generation(position_id):
            executable_amount = self._executable_cleanup_base(attributed_base, position_id)
            if executable_amount is not None:
                amount = executable_amount
        return (
            amount > 0
            and price is not None
            and (amount * price > self.config.cleanup_min_quote_value)
        )

    def _quantized_base_amount(self, amount: Decimal, position_id: str) -> Decimal:
        connector = self.market_data_provider.get_connector(self.config.connector_name)
        trading_pair = self._positions[position_id].trading_pair
        return max(self._decimal(connector.quantize_order_amount(trading_pair, max(amount, ZERO))), ZERO)

    def _trigger_balance_update(self):
        self._balance_refresh_required = True

    def _stored_executor_history(self) -> List[ExecutorInfo]:
        # The orchestrator exposes active Executors to controllers but keeps
        # stored same-generation records only in Hummingbot's native recorder.
        from hummingbot.connector.markets_recorder import MarketsRecorder

        recorder = MarketsRecorder._shared_instance
        if recorder is None:
            raise RuntimeError("MarketsRecorder is unavailable")
        return recorder.get_executors_by_controller(self.config.id)

    def _check_restart_history(self):
        if self._restart_quarantined is not None:
            return
        try:
            self._restart_quarantined = bool(self._stored_executor_history())
            if self._restart_quarantined:
                self._latch_fault("reused_controller_id")
        except Exception as exc:
            self._restart_quarantined = True
            self.logger().warning(f"Could not verify controller Executor history: {exc}")
            self._latch_fault("restart_history_unavailable")

    async def _fetch_owned_positions(self, connector) -> Dict[str, List[str]]:
        response = await connector._get_gateway_instance().clmm_positions_owned(
            network=connector.network,
            wallet_address=connector.address,
            dex=self._lp_dex_name,
            trading_type=self._lp_trading_type,
            fail_silently=False,
            chain=connector.chain,
        )
        if isinstance(response, list):
            positions = response
        elif isinstance(response, dict) and isinstance(response.get("positions"), list):
            positions = response["positions"]
        else:
            raise ValueError("Gateway returned an invalid positions-owned payload")

        owned = {position_id: [] for position_id in self._positions}
        for raw_position in positions:
            pool = raw_position.get("poolAddress", raw_position.get("pool_address"))
            position_id = self._position_id_by_pool.get(str(pool))
            if position_id is not None:
                address = raw_position.get(
                    "address",
                    raw_position.get("positionAddress", raw_position.get("position_address", "unknown")),
                )
                owned[position_id].append(str(address))
        return owned

    def _needs_manual_close_recovery(self, executor: ExecutorInfo) -> bool:
        recoverable_terminal = self._lp_failed(executor) or (
            self._close_retries_exhausted(executor) and executor.id in self._stop_requested_ids
        )
        return (
            executor.is_done
            and recoverable_terminal
            and self._landed_lp(executor)
            and bool(executor.custom_info.get("position_address"))
            and executor.id not in self._manual_recovery_completed
        )

    def _manual_close_recovery_candidate(self) -> Optional[ExecutorInfo]:
        return next(
            (
                executor
                for executor in self._lp_executors()
                if self._needs_manual_close_recovery(executor)
            ),
            None,
        )

    def _snapshot_receipt(self, position_info) -> Dict:
        return {
            "base_amount": self._decimal(getattr(position_info, "base_token_amount", ZERO)),
            "quote_amount": self._decimal(getattr(position_info, "quote_token_amount", ZERO)),
            "base_fee": self._decimal(getattr(position_info, "base_fee_amount", ZERO)),
            "quote_fee": self._decimal(getattr(position_info, "quote_fee_amount", ZERO)),
            "data_unavailable": True,
        }

    async def _classify_onchain_position(self, connector, executor: ExecutorInfo) -> Tuple[str, Optional[Dict]]:
        position_id = self._position_id_for_lp(executor)
        if position_id is None or not self._ownership_checked_by_position[position_id]:
            return "unknown", None
        position = self._positions[position_id]
        address = str(executor.custom_info["position_address"])
        try:
            position_info = await connector.get_position_info_fresh(
                trading_pair=position.trading_pair,
                dex_name=self._lp_dex_name,
                trading_type=self._lp_trading_type,
                position_address=address,
            )
        except Exception as exc:
            self._ownership_checked_by_position[position_id] = False
            self._ownership_errors[position_id] = f"{type(exc).__name__}: {exc}"
            self._record_gateway_read_failure("position_info", exc)
            return "unknown", None

        owners = [
            owner_id
            for owner_id, addresses in self._owned_position_addresses.items()
            if address in addresses
        ]
        if owners and owners != [position_id]:
            return "conflict", None
        if position_info is None:
            return ("unknown", None) if owners else ("absent", None)

        pool_address = str(getattr(position_info, "pool_address", ""))
        if pool_address and pool_address != position.pool_address:
            return "conflict", None
        if not pool_address:
            return "unknown", None
        snapshot = self._snapshot_receipt(position_info)
        return ("present", snapshot) if owners == [position_id] else ("unknown", snapshot)

    async def _submit_manual_close(self, connector, executor: ExecutorInfo) -> Dict:
        position_id = self._position_id_for_lp(executor)
        if position_id is None:
            raise ValueError(f"Cannot recover unmapped LP Executor {executor.id}")
        position = self._positions[position_id]
        order_id = connector.create_market_order_id(TradeType.RANGE, position.trading_pair)
        await connector._clmm_close_position(
            trade_type=TradeType.RANGE,
            order_id=order_id,
            trading_pair=position.trading_pair,
            position_address=str(executor.custom_info["position_address"]),
            max_retries=0,
            dex_name=self._lp_dex_name,
            trading_type=self._lp_trading_type,
            slippage_pct=float(getattr(executor.config, "max_slippage_pct", Decimal("5"))),
        )
        # The connector owns this metadata until its order-status poll emits the
        # native liquidity-removed event and retires the tracked RANGE order.
        metadata = dict(connector._lp_orders_metadata.get(order_id, {}))
        has_amounts = any(
            key in metadata for key in ("base_amount", "quote_amount", "base_fee", "quote_fee")
        )
        return {
            "base_amount": self._decimal(metadata.get("base_amount")),
            "quote_amount": self._decimal(metadata.get("quote_amount")),
            "base_fee": self._decimal(metadata.get("base_fee")),
            "quote_fee": self._decimal(metadata.get("quote_fee")),
            "data_unavailable": bool(metadata.get("data_unavailable")) or not has_amounts,
        }

    def _apply_manual_close_receipt(self, executor: ExecutorInfo):
        receipt = self._manual_close_receipts.get(executor.id) or self._manual_recovery_snapshots.get(executor.id)
        if receipt is not None and receipt.get("data_unavailable"):
            receipt = self._manual_recovery_snapshots.get(executor.id, receipt)
        if receipt is None:
            custom = executor.custom_info
            receipt = {
                "base_amount": self._decimal(custom.get("base_amount")),
                "quote_amount": self._decimal(custom.get("quote_amount")),
                "base_fee": self._decimal(custom.get("base_fee")),
                "quote_fee": self._decimal(custom.get("quote_fee")),
                "data_unavailable": True,
            }
        self._manual_close_receipts[executor.id] = receipt
        self._manual_recovery_errors.pop(executor.id, None)
        self._manual_recovery_completed.add(executor.id)
        self.logger().info(
            f"Manual LP recovery reconciled position {executor.custom_info.get('position_address')} as closed"
        )
        self._trigger_balance_update()

    async def _recover_failed_lp(self, connector):
        executor = self._manual_close_recovery_candidate()
        lp_mutation_in_flight = any(
            self._lp_open_is_unresolved(active)
            or str(active.custom_info.get("state", "")) in {"CLOSING", "SWAPPING"}
            or active.id in self._stop_requested_ids
            for active in self._active_lps()
        )
        if executor is None or self._active_orders() or self._pending_create_ids or lp_mutation_in_flight:
            return
        position_id = self._position_id_for_lp(executor)
        if position_id is None or self._now() < self._manual_recovery_retry_after.get(executor.id, 0):
            return

        classification, snapshot = await self._classify_onchain_position(connector, executor)
        if snapshot is not None:
            self._manual_recovery_snapshots[executor.id] = snapshot
        if classification == "absent":
            self._apply_manual_close_receipt(executor)
            return
        if classification == "conflict":
            self._latch_fault(f"manual_recovery_conflict:{executor.id}")
            return
        if classification != "present":
            return

        attempts = self._manual_recovery_attempts.get(executor.id, 0)
        if attempts >= self.config.max_consecutive_controller_failures:
            if self._fault_reason == f"manual_recovery_exhausted:{executor.id}":
                return
            self._manual_recovery_errors[executor.id] = "manual_close_exhausted"
            self.logger().error(
                f"Manual LP recovery exhausted for {executor.id}; position "
                f"{executor.custom_info.get('position_address')} still requires operator intervention"
            )
            self._latch_fault(f"manual_recovery_exhausted:{executor.id}")
            return
        self._manual_recovery_attempts[executor.id] = attempts + 1
        self._manual_recovery_retry_after[executor.id] = self._now() + self.config.failure_retry_backoff_seconds
        self.logger().warning(
            f"Submitting manual LP recovery attempt {attempts + 1}/"
            f"{self.config.max_consecutive_controller_failures} for position "
            f"{executor.custom_info.get('position_address')}"
        )
        try:
            self._manual_close_receipts[executor.id] = await self._submit_manual_close(connector, executor)
            self._manual_recovery_errors.pop(executor.id, None)
        except Exception as exc:
            self._manual_recovery_errors[executor.id] = "manual_close_uncertain"
            self.logger().warning(
                f"Manual LP recovery result is uncertain for {executor.id}: {type(exc).__name__}: {exc}. "
                "Ownership evidence was invalidated; no new close will be submitted before reconciliation."
            )
        self._ownership_checked_by_position[position_id] = False
        self._trigger_balance_update()

    async def update_processed_data(self):
        self._check_restart_history()
        if self._balance_refresh_required:
            self._wallet_bases = dict.fromkeys(self._positions)
            self._wallet_quote = None
            self._wallet_sol = None
        if self._gateway_read_backoff_active():
            return
        recovery_candidate = self._manual_close_recovery_candidate()
        if (
            recovery_candidate is not None
            and self._now() < self._manual_recovery_retry_after.get(recovery_candidate.id, 0)
        ):
            return
        for position_id in self._positions:
            position_lps = self._position_lp_executors(position_id)
            if any(
                executor.is_done and self._landed_lp(executor) and executor.id not in self._settled_lp_ids
                for executor in position_lps
            ):
                # A terminal Executor is not proof that its position left the pool.
                self._ownership_checked_by_position[position_id] = False
                self._trigger_balance_update()
        balances_refreshed = False
        try:
            connector = self.market_data_provider.get_connector(self.config.connector_name)
            if not await self._register_tokens(connector):
                return
            recovery_candidate = self._manual_close_recovery_candidate()
            if (
                recovery_candidate is not None
                and self._now() >= self._manual_recovery_retry_after.get(recovery_candidate.id, 0)
            ):
                position_id = self._position_id_for_lp(recovery_candidate)
                if position_id is not None:
                    self._ownership_checked_by_position[position_id] = False
            if not all(self._ownership_checked_by_position.values()):
                try:
                    owned = await self._fetch_owned_positions(connector)
                    self._owned_position_addresses = owned
                    self._ownership_errors = dict.fromkeys(self._positions)
                    self._ownership_checked_by_position = dict.fromkeys(self._positions, True)
                    for position_id, addresses in owned.items():
                        if not addresses:
                            reconciled = {
                                executor_id
                                for executor_id in self._pending_failure_reconciliation
                                if any(
                                    executor.id == executor_id
                                    for executor in self._position_lp_executors(position_id)
                                )
                            }
                            self._reconciled_failure_ids.update(reconciled)
                            self._pending_failure_reconciliation.difference_update(reconciled)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    self._ownership_errors = dict.fromkeys(self._positions, error)
                    self._ownership_checked_by_position = dict.fromkeys(self._positions, False)
                    self._record_gateway_read_failure("positions_owned", exc)
                    return
            await self._recover_failed_lp(connector)
            if self._gateway_read_backoff_active():
                return
            if self._balance_refresh_required:
                update = getattr(connector, "update_balances", None)
                if update is not None:
                    result = update()
                    if hasattr(result, "__await__"):
                        await result
                self._balance_refresh_required = False
                balances_refreshed = True
            self._wallet_quote = self._canonical_balance(self._quote_token)
            self._wallet_sol = self._canonical_balance("SOL")
            self._wallet_bases = {
                position_id: self._canonical_balance(base_token)
                for position_id, base_token in self._base_tokens.items()
            }
            if balances_refreshed:
                self._balance_observed_at = self._now()
            if self._pool_info_refresh_due():
                # Count the attempt before awaiting it so a failed batch cannot fall
                # back to the one-second controller cadence. The existing Gateway
                # error state blocks mutations until its bounded retry succeeds.
                self._last_pool_info_refresh_timestamp = self._now()
                self._pool_prices = dict.fromkeys(self._positions)
                for position_id, position in self._positions.items():
                    pool_info = await connector.get_pool_info_by_address(
                        position.pool_address,
                        dex_name=self._lp_dex_name,
                        trading_type=self._lp_trading_type,
                    )
                    if pool_info is None:
                        continue
                    pool_base_mint = str(getattr(pool_info, "base_token_address", ""))
                    pool_quote_mint = str(getattr(pool_info, "quote_token_address", ""))
                    if not pool_base_mint or not pool_quote_mint:
                        continue
                    if (pool_base_mint, pool_quote_mint) != (
                        position.base_token_mint,
                        self.config.quote_token_mint,
                    ):
                        self._pool_orientation_errors[position_id] = (
                            f"pool_token_orientation_mismatch:{pool_base_mint or 'missing'}:"
                            f"{pool_quote_mint or 'missing'}"
                        )
                        continue
                    self._pool_orientation_errors[position_id] = None
                    price = self._decimal(getattr(pool_info, "price", None), default=Decimal("NaN"))
                    self._pool_prices[position_id] = price if price.is_finite() and price > 0 else None
                self._clear_gateway_read_failure()
        except Exception as exc:
            self._record_gateway_read_failure("market_data", exc)

    def _observe_close_direction(self, executor: ExecutorInfo, controller_stop: bool = False):
        if controller_stop:
            self._lp_close_directions[executor.id] = "controller_stop"
            return
        current = self._decimal(executor.custom_info.get("current_price"), Decimal("NaN"))
        lower = getattr(executor.config, "lower_limit_price", None)
        upper = getattr(executor.config, "upper_limit_price", None)
        if current.is_finite() and lower is not None and current <= Decimal(str(lower)):
            self._lp_close_directions[executor.id] = "lower"
        elif current.is_finite() and upper is not None and current >= Decimal(str(upper)):
            self._lp_close_directions[executor.id] = "upper"

    def _close_direction(self, executor: Optional[ExecutorInfo]) -> Optional[str]:
        if executor is None or not executor.is_done:
            return None
        observed = self._lp_close_directions.get(executor.id)
        if self._lp_failed(executor):
            return observed if self._recovered_failed_lp(executor) else None
        if observed is not None:
            return observed
        current = self._decimal(executor.custom_info.get("current_price"), Decimal("NaN"))
        lower = getattr(executor.config, "lower_limit_price", None)
        upper = getattr(executor.config, "upper_limit_price", None)
        if current.is_finite() and lower is not None and current <= Decimal(str(lower)):
            return "lower"
        if current.is_finite() and upper is not None and current >= Decimal(str(upper)):
            return "upper"
        return "unknown"

    def _cleanup_completed_at(self, lp_executor: Optional[ExecutorInfo]) -> Optional[float]:
        if lp_executor is None:
            return None
        position_id = self._position_id_for_lp(lp_executor)
        if position_id is None:
            return None
        if not self._successful_preparation_in_current_generation(position_id):
            base, _ = self._inventory_ledger(position_id)
            remaining_base = max(base, ZERO)
            if remaining_base > 0:
                if self._pool_prices[position_id] is None or self._material_base(remaining_base, position_id):
                    return None
        cleanup = [
            order
            for order in self._position_order_executors(position_id)
            if self._order_role(order).startswith(CLEANUP_ROLE)
            and order.timestamp >= lp_executor.timestamp
            and order.is_done
            and not self._order_failed(order)
        ]
        if cleanup:
            latest = cleanup[-1]
            return float(latest.close_timestamp or latest.timestamp)
        return float(lp_executor.close_timestamp or lp_executor.timestamp)

    def _consecutive_failures(self, position_id: str) -> Tuple[int, Optional[str], Optional[float]]:
        count = 0
        reason = None
        retry_after = None
        for executor in sorted(self.executors_info, key=lambda item: (item.timestamp, item.id)):
            if self._executor_type(executor) == "lp_executor":
                if self._position_id_for_lp(executor) != position_id:
                    continue
                if (
                    executor.is_done
                    and self._lp_failed(executor)
                    and not executor.custom_info.get("position_address")
                ):
                    count += 1
                    reason = f"lp_create_failed:{executor.id}"
                    retry_after = (
                        float(executor.close_timestamp or executor.timestamp)
                        + self.config.failure_retry_backoff_seconds
                    )
                elif self._landed_lp(executor) and not self._lp_failed(executor):
                    count = 0
                    reason = None
                    retry_after = None
            elif self._executor_type(executor) == "order_executor" and executor.is_done:
                if self._position_id_for_order(executor) != position_id:
                    continue
                role = self._order_role(executor)
                if role.startswith(PREPARE_ROLE) and self._order_failed(executor):
                    count += 1
                    reason = f"preparation_failed:{executor.id}"
                    retry_after = (
                        float(executor.close_timestamp or executor.timestamp)
                        + self.config.failure_retry_backoff_seconds
                    )
        return count, reason, retry_after

    def _cleanup_failures(self, position_id: str) -> Tuple[int, Optional[float]]:
        count = 0
        retry_after = None
        for order in self._position_order_executors(position_id):
            if not self._order_role(order).startswith(CLEANUP_ROLE) or not order.is_done:
                continue
            if self._order_failed(order):
                count += 1
                retry_after = (
                    float(order.close_timestamp or order.timestamp) + self.config.failure_retry_backoff_seconds
                )
            else:
                count = 0
                retry_after = None
        return count, retry_after

    def _consecutive_unlanded_lp_failures(self, position_id: str) -> int:
        """Count only confirmed LP creates that failed without landing a position."""
        count = 0
        for executor in self._position_lp_executors(position_id):
            if executor.is_done and self._lp_failed(executor) and not executor.custom_info.get("position_address"):
                count += 1
            elif self._landed_lp(executor):
                count = 0
        return count

    def _hard_fault(self) -> Optional[str]:
        active_lps = self._active_lps()
        active_orders = self._active_orders()
        if any(
            len([executor for executor in active_lps if self._position_id_for_lp(executor) == position_id]) > 1
            for position_id in self._positions
        ):
            return "multiple_active_lp_executors_for_position"
        if len(active_orders) > 1:
            return "multiple_active_order_executors"
        if any(self._position_id_for_lp(executor) is None for executor in self._lp_executors()):
            return "unmapped_lp_executor"
        if any(self._position_id_for_order(executor) not in self._positions for executor in self._order_executors()):
            return "unmapped_order_executor"
        for position_id, orientation_error in self._pool_orientation_errors.items():
            if orientation_error:
                return f"{position_id}:{orientation_error}"
            managed_addresses = {
                str(executor.custom_info.get("position_address"))
                for executor in self._position_lp_executors(position_id)
                if (not executor.is_done or self._lp_failed(executor) or self._close_retries_exhausted(executor))
                and executor.custom_info.get("position_address")
            }
            last_address = self._last_position_addresses[position_id]
            if last_address:
                managed_addresses.add(last_address)
            unmatched_addresses = [
                address
                for address in self._owned_position_addresses[position_id]
                if address not in managed_addresses
            ]
            if unmatched_addresses:
                return f"unmatched_onchain_position:{position_id}:" + ",".join(unmatched_addresses)
        return None

    def _stopped_lp_still_owned(self) -> bool:
        for executor in self._lp_executors():
            if executor.id not in self._stop_requested_ids:
                continue
            position_id = self._position_id_for_lp(executor)
            if position_id is None:
                continue
            address = executor.custom_info.get("position_address") or self._last_position_addresses[position_id]
            if address and str(address) in self._owned_position_addresses[position_id]:
                return True
        return False

    def _failure_limit_reached(self, position_id: str) -> bool:
        failures, _, _ = self._consecutive_failures(position_id)
        return failures >= self.config.max_consecutive_controller_failures

    def _position_value_quote(self, position_id: str) -> Optional[Decimal]:
        if not self._capital_preflight_passed:
            return None
        if not self._ownership_checked_by_position[position_id] or self._ownership_errors[position_id]:
            return None
        price = self._pool_prices[position_id]
        if any(self._position_id_for_order(order) == position_id for order in self._active_orders()):
            return None
        if position_id in self._pending_create_positions.values():
            return None
        if any(
            self._position_id_for_lp(executor) == position_id
            and self._manual_close_receipts.get(executor.id, {}).get("data_unavailable")
            for executor in self._lp_executors()
        ):
            return None

        base, quote = self._inventory_ledger(position_id)
        active_lps = [executor for executor in self._active_lps() if self._position_id_for_lp(executor) == position_id]
        if active_lps:
            executor = active_lps[0]
            if self._lp_open_is_unresolved(executor):
                return None
            if price is None:
                return None
            custom = executor.custom_info or {}
            base += self._decimal(custom.get("base_amount")) + self._decimal(custom.get("base_fee"))
            quote += self._decimal(custom.get("quote_amount")) + self._decimal(custom.get("quote_fee"))
        elif self._successful_cleanup_in_current_generation(position_id):
            # Executor receipts remain the audit ledger, but after cleanup the
            # wallet is the upper bound on base inventory that still exists.
            wallet_available = self._available_wallet_base(position_id)
            if wallet_available is not None:
                base = min(base, wallet_available)
        if base == ZERO:
            return quote
        if price is None:
            return None
        return base * price + quote

    def _lp_position_value_quote(self, position_id: str) -> Optional[Decimal]:
        """Return the current quote value held inside one managed LP position."""
        active_lps = [
            executor
            for executor in self._active_lps()
            if self._position_id_for_lp(executor) == position_id
        ]
        if len(active_lps) > 1:
            return None
        if active_lps:
            executor = active_lps[0]
            price = self._pool_prices[position_id]
            if self._lp_open_is_unresolved(executor) or price is None:
                return None
            custom = executor.custom_info or {}
            base = self._decimal(custom.get("base_amount")) + self._decimal(custom.get("base_fee"))
            quote = self._decimal(custom.get("quote_amount")) + self._decimal(custom.get("quote_fee"))
            if base < ZERO or quote < ZERO:
                return None
            return base * price + quote

        unresolved_open = any(
            not executor.is_done and self._lp_open_is_unresolved(executor)
            for executor in self._position_lp_executors(position_id)
        )
        if unresolved_open:
            return None
        return ZERO if self._ownership_state(position_id) == "ABSENT" else None

    def _position_capital_values(
        self, position_id: str
    ) -> Tuple[Decimal, Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        position = self._positions[position_id]
        allocated = self.config.total_amount_quote * position.allocation_pct / ONE_HUNDRED
        pnl = self._reported_position_pnl_quote(position_id)
        current_total = allocated + pnl if pnl is not None else None
        lp_position = self._lp_position_value_quote(position_id)
        wallet_residual = None
        if current_total is not None and lp_position is not None:
            residual = current_total - lp_position
            epsilon = Decimal("0.000000000001")
            if residual >= -epsilon:
                wallet_residual = max(residual, ZERO)
        return allocated, current_total, lp_position, wallet_residual

    def _pnl_quote(self) -> Optional[Decimal]:
        position_pnls = [self._position_pnl_quote(position_id) for position_id in self._positions]
        if any(pnl is None for pnl in position_pnls):
            return None
        return sum(position_pnls, ZERO)

    def _position_pnl_quote(self, position_id: str) -> Optional[Decimal]:
        value = self._position_value_quote(position_id)
        if value is None:
            return None
        allocation = self.config.total_amount_quote * self._positions[position_id].allocation_pct / ONE_HUNDRED
        return value - allocation

    def _capture_terminal_pnl(self):
        if (
            self._lifecycle_state not in {LifecycleState.EXITED, LifecycleState.FAULTED}
            or self._terminal_position_pnl_quote is not None
        ):
            return
        self._terminal_position_pnl_quote = {
            position_id: self._position_pnl_quote(position_id) for position_id in self._positions
        }
        position_pnls = list(self._terminal_position_pnl_quote.values())
        self._terminal_pnl_quote = (
            None if any(pnl is None for pnl in position_pnls) else sum(position_pnls, ZERO)
        )

    def _reported_position_pnl_quote(self, position_id: str) -> Optional[Decimal]:
        if self._terminal_position_pnl_quote is not None:
            return self._terminal_position_pnl_quote[position_id]
        return self._position_pnl_quote(position_id)

    def _reported_pnl_quote(self) -> Optional[Decimal]:
        if self._terminal_position_pnl_quote is not None:
            return self._terminal_pnl_quote
        return self._pnl_quote()

    @staticmethod
    def _lp_open_is_unresolved(executor: ExecutorInfo) -> bool:
        state = str(executor.custom_info.get("state", ""))
        return not executor.custom_info.get("position_address") or state in {"NOT_ACTIVE", "OPENING"}

    def _latch_configured_exit(self):
        if self._runtime_exit_reason == ExitReason.NONE and self.config.exit_requested:
            self._runtime_exit_reason = self.config.exit_reason

    def _effective_exit_reason(self) -> ExitReason:
        self._latch_configured_exit()
        if self._runtime_exit_reason != ExitReason.NONE:
            return self._runtime_exit_reason
        now = self._now()
        lifetime = now - self._controller_started_at
        if lifetime >= self.config.controller_time_limit_minutes * 60:
            self._runtime_exit_reason = ExitReason.TIME_LIMIT
            return self._runtime_exit_reason
        grace = self.config.controller_pnl_grace_period_minutes * 60
        if lifetime < grace:
            return ExitReason.NONE
        pnl = self._pnl_quote()
        if pnl is None:
            return ExitReason.NONE
        pnl_ratio = pnl / self.config.total_amount_quote
        if pnl_ratio >= self.config.controller_take_profit_ratio:
            self._runtime_exit_reason = ExitReason.TAKE_PROFIT
        elif pnl_ratio <= -self.config.controller_stop_loss_ratio:
            self._runtime_exit_reason = ExitReason.STOP_LOSS
        return self._runtime_exit_reason

    def _create_order_action(
        self,
        side: TradeType,
        amount: Decimal,
        role: str,
        position_id: str,
    ) -> CreateExecutorAction:
        position = self._positions[position_id]
        config = OrderExecutorConfig(
            timestamp=self._now(),
            connector_name=self.config.connector_name,
            trading_pair=position.trading_pair,
            side=side,
            amount=amount,
            execution_strategy=ExecutionStrategy.MARKET,
            level_id=f"{role}:{position_id}",
        )
        self._pending_create_ids.add(config.id)
        self._pending_create_positions[config.id] = position_id
        return CreateExecutorAction(controller_id=self.config.id, executor_config=config)

    def _position_bounds(
        self,
        side: TradeType,
        position_id: str,
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        position = self._current_formations[position_id] or self._positions[position_id]
        price = self._pool_prices[position_id]
        if price is None:
            raise ValueError("pool price is unavailable")
        width = position.position_width_pct / ONE_HUNDRED
        threshold = position.rebalance_threshold_pct / ONE_HUNDRED
        if side == TradeType.RANGE:
            lower = price * (Decimal("1") - width / Decimal("2"))
            upper = price * (Decimal("1") + width / Decimal("2"))
            lower_limit = lower * (Decimal("1") - threshold)
            upper_limit = upper * (Decimal("1") + threshold)
        else:
            offset = position.downside_offset_pct / ONE_HUNDRED
            upper = price * (Decimal("1") - offset)
            lower = upper * (Decimal("1") - width)
            lower_limit = lower * (Decimal("1") - threshold)
            # A downside BUY is intentionally above its range at creation.  Its
            # upper limit therefore must sit above the entry reference price.
            upper_limit = max(upper * (Decimal("1") + threshold), price * (Decimal("1") + threshold))
        return lower, upper, lower_limit, upper_limit

    def _create_lp_action(
        self,
        side: TradeType,
        base_amount: Decimal,
        quote_amount: Decimal,
        position_id: str,
    ) -> CreateExecutorAction:
        position = self._positions[position_id]
        lower, upper, lower_limit, upper_limit = self._position_bounds(side, position_id)
        config = LPExecutorConfig(
            timestamp=self._now(),
            connector_name=self.config.connector_name,
            lp_provider=self.config.lp_provider,
            swap_provider=self.config.swap_provider,
            trading_pair=position.trading_pair,
            pool_address=position.pool_address,
            lower_price=lower,
            upper_price=upper,
            base_amount=base_amount,
            quote_amount=quote_amount,
            position_refresh_interval=LP_POSITION_REFRESH_INTERVAL_SECONDS,
            side=side,
            lower_limit_price=lower_limit,
            upper_limit_price=upper_limit,
            keep_position=True,
        )
        self._pending_create_ids.add(config.id)
        self._pending_create_positions[config.id] = position_id
        return CreateExecutorAction(controller_id=self.config.id, executor_config=config)

    def _successful_preparation_in_current_generation(self, position_id: str) -> bool:
        landed = [
            executor.timestamp for executor in self._position_lp_executors(position_id) if self._landed_lp(executor)
        ]
        latest_lp_timestamp = max(landed, default=float("-inf"))
        return any(
            order.timestamp > latest_lp_timestamp
            and self._order_role(order).startswith(PREPARE_ROLE)
            and order.is_done
            and not self._order_failed(order)
            and self._decimal(order.custom_info.get("executed_amount_base")) > 0
            for order in self._position_order_executors(position_id)
        )

    def _cleanup_action(
        self,
        attributed_base: Decimal,
        position_id: str,
    ) -> Optional[CreateExecutorAction]:
        price = self._pool_prices[position_id]
        if not self._sol_reserve_ready():
            return None
        if price is None:
            self._readiness_state = ReadinessState.BLOCKED_PRICE_UNAVAILABLE
            return None
        amount = self._executable_cleanup_base(attributed_base, position_id)
        if amount is None:
            self._readiness_state = ReadinessState.BLOCKED_BALANCE_UNAVAILABLE
            return None
        if amount * price <= self.config.cleanup_min_quote_value:
            return None
        self._readiness_state = ReadinessState.SWAP_REQUIRED
        return self._create_order_action(TradeType.SELL, amount, CLEANUP_ROLE, position_id)

    def _exit_cleanup_blocker(
        self,
        position_id: str,
        attributed_base: Decimal,
        blocker: str,
        cleanup_failures: int = 0,
        retry_after: Optional[float] = None,
    ) -> Dict:
        wallet_available = self._available_wallet_base(position_id)
        price = self._pool_prices[position_id]
        return {
            "position_id": position_id,
            "trading_pair": self._positions[position_id].trading_pair,
            "wallet_symbol": self._base_tokens[position_id],
            "blocker": blocker,
            "attributed_base": attributed_base,
            "wallet_available": wallet_available,
            "pool_price": price,
            "estimated_quote_value": attributed_base * price if price is not None else None,
            "cleanup_failures": cleanup_failures,
            "retry_after": retry_after,
            "symbol_error": self._token_symbol_errors[position_id],
        }

    def _log_exit_cleanup_blockers(self, blockers: List[Dict]):
        signature = tuple(
            (str(item["position_id"]), str(item["blocker"]), int(item["cleanup_failures"]))
            for item in blockers
        )
        if not signature or signature == self._last_logged_exit_cleanup_blockers:
            return
        details = "; ".join(
            (
                f"position={item['position_id']}, pair={item['trading_pair']}, "
                f"wallet_symbol={item['wallet_symbol']}, blocker={item['blocker']}, "
                f"attributed_base={self._format_pnl(item['attributed_base'])}, "
                f"wallet_available={self._format_pnl(item['wallet_available'])}, "
                f"pool_price={self._format_pnl(item['pool_price'])}, "
                f"estimated_quote_value={self._format_pnl(item['estimated_quote_value'])}, "
                f"cleanup_failures={item['cleanup_failures']}, retry_after={item['retry_after']}, "
                f"symbol_error={item['symbol_error'] or 'none'}"
            )
            for item in blockers
        )
        self.logger().warning(
            f"Controller {self.config.id} exit cleanup blocked: "
            f"exit_reason={self._effective_exit_reason().value}; {details}"
        )
        self._last_logged_exit_cleanup_blockers = signature

    def _open_action(self, position_id: str) -> Optional[CreateExecutorAction]:
        if self._token_symbol_errors[position_id] is not None:
            self._readiness_state = ReadinessState.BLOCKED_TOKEN_SYMBOL_MISMATCH
            return None
        position = self._current_formations[position_id] or self._positions[position_id]
        price = self._pool_prices[position_id]
        if price is None:
            self._readiness_state = ReadinessState.BLOCKED_PRICE_UNAVAILABLE
            return None
        if not self._sol_reserve_ready():
            return None
        if self._wallet_bases[position_id] is None or self._wallet_quote is None:
            self._readiness_state = ReadinessState.BLOCKED_BALANCE_UNAVAILABLE
            return None

        attributed_base, attributed_quote = self._inventory_ledger(position_id)
        attributed_base = max(attributed_base, ZERO)
        attributed_quote = max(attributed_quote, ZERO)
        wallet_base = self._available_wallet_base(position_id)
        if wallet_base is None:
            self._readiness_state = ReadinessState.BLOCKED_BALANCE_UNAVAILABLE
            return None
        available_base = min(attributed_base, wallet_base)
        position_budget = self.config.total_amount_quote * position.allocation_pct / ONE_HUNDRED
        available_quote = min(attributed_quote, self._wallet_quote, position_budget)
        # Orca's open quote can debit up to its configured slippage allowance.
        # The first attempt reserves the configured safety buffer; each exact,
        # reconciled unlanded failure compounds that same buffer so a retry is
        # materially smaller instead of repeating an unchanged transaction.
        attempt_number = self._consecutive_unlanded_lp_failures(position_id) + 1
        buffer_multiplier = (Decimal("1") - self.config.lp_sizing_buffer_pct / ONE_HUNDRED) ** attempt_number

        if position.market_trend == MarketTrend.DOWN:
            if available_quote <= 0:
                self._readiness_state = ReadinessState.BLOCKED_INSUFFICIENT_BALANCE
                return None
            self._readiness_state = ReadinessState.READY
            if self._current_formations[position_id] is None:
                self._current_formations[position_id] = position.model_copy()
            return self._create_lp_action(
                TradeType.BUY, ZERO, available_quote * buffer_multiplier, position_id
            )

        if position.market_trend not in {MarketTrend.UP, MarketTrend.SIDEWAYS}:
            self._readiness_state = ReadinessState.BLOCKED_TREND
            return None

        capital = min(
            position_budget,
            available_quote + available_base * price,
        )
        target_side_value = capital / Decimal("2")
        base_deficit = max(ZERO, target_side_value / price - available_base)
        if (
            base_deficit * price > self.config.cleanup_min_quote_value
            and not self._successful_preparation_in_current_generation(position_id)
        ):
            if available_quote < base_deficit * price:
                self._readiness_state = ReadinessState.BLOCKED_INSUFFICIENT_BALANCE
                return None
            self._readiness_state = ReadinessState.SWAP_REQUIRED
            if self._current_formations[position_id] is None:
                self._current_formations[position_id] = position.model_copy()
            return self._create_order_action(TradeType.BUY, base_deficit, PREPARE_ROLE, position_id)

        deploy_side_value = min(available_quote, available_base * price) * buffer_multiplier
        if deploy_side_value <= 0:
            self._readiness_state = ReadinessState.BLOCKED_INSUFFICIENT_BALANCE
            return None
        self._readiness_state = ReadinessState.READY
        if self._current_formations[position_id] is None:
            self._current_formations[position_id] = position.model_copy()
        return self._create_lp_action(
            TradeType.RANGE,
            deploy_side_value / price,
            deploy_side_value,
            position_id,
        )

    def _newly_settled_order_requires_refresh(self) -> bool:
        settled = [
            order for order in self._order_executors() if order.is_done and order.id not in self._settled_order_ids
        ]
        if not settled:
            return False
        self._settled_order_ids.update(order.id for order in settled)
        self._trigger_balance_update()
        return True

    def _lp_balance_transition_requires_refresh(self) -> bool:
        landed = {executor.id for executor in self._lp_executors() if executor.is_active and self._landed_lp(executor)}
        settled_executors = [executor for executor in self._lp_executors() if executor.is_done]
        settled = {executor.id for executor in settled_executors}
        newly_landed = landed.difference(self._observed_landed_lp_ids)
        newly_settled = [executor for executor in settled_executors if executor.id not in self._settled_lp_ids]
        settlement_after_balance = any(
            self._balance_observed_at is None
            or float(executor.close_timestamp or executor.timestamp) >= self._balance_observed_at
            for executor in newly_settled
        )
        changed = bool(newly_landed or settlement_after_balance)
        changed_executors = [executor for executor in self._lp_executors() if executor.id in newly_landed]
        changed_executors.extend(newly_settled)
        for executor in changed_executors:
            position_id = self._position_id_for_lp(executor)
            if position_id is not None:
                self._ownership_checked_by_position[position_id] = False
                address = executor.custom_info.get("position_address")
                if address:
                    self._last_position_addresses[position_id] = str(address)
        self._observed_landed_lp_ids.update(landed)
        self._settled_lp_ids.update(settled)
        if changed:
            self._trigger_balance_update()
        return changed

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions = self._determine_executor_actions()
        self._capture_terminal_pnl()
        self._log_session_transition()
        return actions

    def _determine_executor_actions(self) -> List[ExecutorAction]:
        """Serialize controller mutations while allowing one live LP per configured pool."""
        self._latch_configured_exit()
        if not self._tokens_ready:
            self._lifecycle_state = LifecycleState.BLOCKED
            self._readiness_state = ReadinessState.BLOCKED_TOKEN_REGISTRATION
            return []
        if self._gateway_read_error is not None:
            self._lifecycle_state = LifecycleState.RECOVERING
            self._readiness_state = ReadinessState.BLOCKED_OWNERSHIP_UNAVAILABLE
            return []
        self._pending_create_ids.difference_update(executor.id for executor in self.executors_info)
        for executor in self.executors_info:
            self._pending_create_positions.pop(executor.id, None)
        for executor in self._active_lps():
            self._observe_close_direction(executor)

        unresolved_failed_creates = {
            executor.id
            for executor in self._lp_executors()
            if executor.is_done
            and self._lp_failed(executor)
            and not executor.custom_info.get("position_address")
            and executor.id not in self._reconciled_failure_ids
        }
        if unresolved_failed_creates:
            self._pending_failure_reconciliation.update(unresolved_failed_creates)
            for executor in self._lp_executors():
                if executor.id in unresolved_failed_creates:
                    position_id = self._position_id_for_lp(executor)
                    if position_id is not None:
                        self._ownership_checked_by_position[position_id] = False
            self._lifecycle_state = LifecycleState.RECOVERING
            self._readiness_state = ReadinessState.BLOCKED_OWNERSHIP_UNAVAILABLE
            return []

        if not all(self._ownership_checked_by_position.values()):
            self._lifecycle_state = LifecycleState.RECOVERING
            self._readiness_state = ReadinessState.BLOCKED_OWNERSHIP_UNAVAILABLE
            return []

        hard_fault = self._hard_fault()
        if hard_fault:
            self._latch_fault(hard_fault)

        lp_balance_changed = self._lp_balance_transition_requires_refresh()
        settled_order_changed = self._newly_settled_order_requires_refresh()

        active_orders = self._active_orders()
        if active_orders:
            role = self._order_role(active_orders[0])
            self._lifecycle_state = (
                LifecycleState.CLEANING if role.startswith(CLEANUP_ROLE) else LifecycleState.PREPARING
            )
            self._readiness_state = ReadinessState.SWAP_REQUIRED
            return []
        if self._pending_create_ids:
            self._lifecycle_state = LifecycleState.OPENING
            self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
            return []

        exit_reason = self._effective_exit_reason()
        active_lps = self._active_lps()
        if exit_reason == ExitReason.NONE and self._manual_close_recovery_candidate() is not None:
            self._lifecycle_state = LifecycleState.RECOVERING
            self._readiness_state = ReadinessState.BLOCKED_OWNERSHIP_UNAVAILABLE
            return []
        if exit_reason != ExitReason.NONE:
            closing_lps = [
                executor
                for executor in self._in_flight_lps()
                if executor.id in self._stop_requested_ids
                or str(executor.custom_info.get("state", "")) in {"CLOSING", "SWAPPING"}
                or not executor.is_active
            ]
            if closing_lps:
                self._lifecycle_state = LifecycleState.CLOSING
                self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
                return []

            if self._stopped_lp_still_owned():
                self._lifecycle_state = LifecycleState.RECOVERING
                self._readiness_state = ReadinessState.BLOCKED_OWNERSHIP_UNAVAILABLE
                return []

            for executor in active_lps:
                state = str(executor.custom_info.get("state", ""))
                if self._lp_open_is_unresolved(executor):
                    self._lifecycle_state = LifecycleState.OPENING
                    self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
                    return []
                if state in {"CLOSING", "SWAPPING"}:
                    self._lifecycle_state = LifecycleState.CLOSING
                    self._readiness_state = ReadinessState.NOT_APPLICABLE
                    return []
                if executor.id not in self._stop_requested_ids:
                    self._observe_close_direction(executor, controller_stop=True)
                    self._stop_requested_ids.add(executor.id)
                    self._lifecycle_state = LifecycleState.CLOSING
                    self._readiness_state = ReadinessState.NOT_APPLICABLE
                    return [
                        StopExecutorAction(
                            controller_id=self.config.id,
                            executor_id=executor.id,
                            keep_position=True,
                        )
                    ]
                self._lifecycle_state = LifecycleState.CLOSING
                return []

            if not self._fault_reason and (
                settled_order_changed or lp_balance_changed or self._balance_refresh_required
            ):
                latest_order = self._order_executors()[-1] if self._order_executors() else None
                self._lifecycle_state = (
                    LifecycleState.CLEANING
                    if latest_order is not None and self._order_role(latest_order).startswith(CLEANUP_ROLE)
                    else LifecycleState.CLOSING
                )
                self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
                return []

            blocked_cleanup_readiness: Optional[ReadinessState] = None
            cleanup_retry_pending = False
            cleanup_blockers: List[Dict] = []
            for position_id in self._positions:
                attributed_base, _ = self._inventory_ledger(position_id)
                attributed_base = max(attributed_base, ZERO)
                cleanup_needed = attributed_base > 0 and (
                    self._pool_prices[position_id] is None
                    or self._material_base(attributed_base, position_id)
                )
                if cleanup_needed:
                    cleanup_failures, cleanup_retry_after = self._cleanup_failures(position_id)
                    if cleanup_failures >= self.config.max_consecutive_controller_failures:
                        self._latch_fault(f"cleanup_failure_limit_reached:{position_id}")
                        blocked_cleanup_readiness = ReadinessState.NOT_APPLICABLE
                        cleanup_blockers.append(
                            self._exit_cleanup_blocker(
                                position_id,
                                attributed_base,
                                "FAILURE_LIMIT",
                                cleanup_failures=cleanup_failures,
                            )
                        )
                        continue
                    if cleanup_retry_after is not None and self._now() < cleanup_retry_after:
                        cleanup_retry_pending = True
                        cleanup_blockers.append(
                            self._exit_cleanup_blocker(
                                position_id,
                                attributed_base,
                                "RETRY_BACKOFF",
                                cleanup_failures=cleanup_failures,
                                retry_after=cleanup_retry_after,
                            )
                        )
                        continue
                cleanup = self._cleanup_action(attributed_base, position_id)
                if cleanup is not None:
                    self._last_logged_exit_cleanup_blockers = ()
                    self._lifecycle_state = LifecycleState.CLEANING
                    return [cleanup]
                if attributed_base > 0 and (
                    self._pool_prices[position_id] is None
                    or self._material_base(attributed_base, position_id)
                ):
                    readiness = self._readiness_state
                    if readiness not in {
                        ReadinessState.BLOCKED_BALANCE_UNAVAILABLE,
                        ReadinessState.BLOCKED_PRICE_UNAVAILABLE,
                        ReadinessState.BLOCKED_TOKEN_SYMBOL_MISMATCH,
                    }:
                        readiness = ReadinessState.BLOCKED_INSUFFICIENT_BALANCE
                    blocked_cleanup_readiness = blocked_cleanup_readiness or readiness
                    if self._wallet_sol is None or self._wallet_bases[position_id] is None:
                        blocker = "BALANCE_UNAVAILABLE"
                    elif self._wallet_sol < self.config.min_sol_reserve:
                        blocker = "SOL_RESERVE"
                    elif self._pool_prices[position_id] is None:
                        blocker = "PRICE_UNAVAILABLE"
                    else:
                        blocker = "INSUFFICIENT_WALLET_BALANCE"
                    cleanup_blockers.append(
                        self._exit_cleanup_blocker(position_id, attributed_base, blocker)
                    )

            if cleanup_retry_pending:
                self._log_exit_cleanup_blockers(cleanup_blockers)
                self._lifecycle_state = LifecycleState.CLEANING
                self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
                return []
            if blocked_cleanup_readiness is not None:
                self._log_exit_cleanup_blockers(cleanup_blockers)
                self._lifecycle_state = LifecycleState.FAULTED if self._fault_reason else LifecycleState.BLOCKED
                self._readiness_state = blocked_cleanup_readiness
                return []
            self._last_logged_exit_cleanup_blockers = ()

            if any(self._ownership_state(position_id) != "ABSENT" for position_id in self._positions):
                self._lifecycle_state = LifecycleState.FAULTED if self._fault_reason else LifecycleState.RECOVERING
                self._readiness_state = ReadinessState.BLOCKED_OWNERSHIP_UNAVAILABLE
                return []

            self._lifecycle_state = LifecycleState.FAULTED if self._fault_reason else LifecycleState.EXITED
            self._readiness_state = ReadinessState.NOT_APPLICABLE
            return []

        if settled_order_changed or lp_balance_changed or self._balance_refresh_required:
            self._lifecycle_state = LifecycleState.PREPARING
            self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
            return []

        for position_id in self._positions:
            if self._failure_limit_reached(position_id):
                self._latch_fault(f"consecutive_failure_limit_reached:{position_id}")
                # The fault is terminal only after the shared exit path has
                # closed every healthy peer and reconciled all exact pools.
                self._lifecycle_state = LifecycleState.CLOSING
                self._readiness_state = ReadinessState.NOT_APPLICABLE
                return []

        if any(error is not None for error in self._token_symbol_errors.values()):
            self._lifecycle_state = LifecycleState.BLOCKED
            self._readiness_state = ReadinessState.BLOCKED_TOKEN_SYMBOL_MISMATCH
            return []

        if not self._capital_preflight_ready():
            self._lifecycle_state = LifecycleState.BLOCKED
            return []

        unresolved_lp_opens = [
            executor for executor in self._in_flight_lps() if self._lp_open_is_unresolved(executor)
        ]
        if unresolved_lp_opens:
            self._lifecycle_state = LifecycleState.OPENING
            self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
            return []

        latest_lp_created_at = max(
            (float(executor.timestamp) for executor in self._lp_executors()),
            default=None,
        )
        if (
            latest_lp_created_at is not None
            and self._now() < latest_lp_created_at + LP_DEPLOYMENT_DELAY_SECONDS
        ):
            self._lifecycle_state = LifecycleState.OPENING
            self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
            return []

        active_position_ids = {self._position_id_for_lp(executor) for executor in active_lps}
        for position_id in self._positions:
            if position_id in active_position_ids:
                continue
            position_lps = self._position_lp_executors(position_id)
            latest_lp = position_lps[-1] if position_lps else None
            close_direction = self._close_direction(latest_lp)
            attributed_base, _ = self._inventory_ledger(position_id)

            cleanup_required = (
                latest_lp is not None
                and latest_lp.is_done
                and (not self._lp_failed(latest_lp) or self._recovered_failed_lp(latest_lp))
                and not self._successful_preparation_in_current_generation(position_id)
            )
            if cleanup_required:
                cleanup_needed = attributed_base > 0 and (
                    self._pool_prices[position_id] is None
                    or self._material_base(max(attributed_base, ZERO), position_id)
                )
                if cleanup_needed:
                    cleanup_failures, cleanup_retry_after = self._cleanup_failures(position_id)
                    if cleanup_failures >= self.config.max_consecutive_controller_failures:
                        self._latch_fault(f"cleanup_failure_limit_reached:{position_id}")
                        self._lifecycle_state = LifecycleState.CLOSING if active_lps else LifecycleState.FAULTED
                        return []
                    if cleanup_retry_after is not None and self._now() < cleanup_retry_after:
                        self._lifecycle_state = LifecycleState.CLEANING
                        self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
                        return []
                cleanup = self._cleanup_action(max(attributed_base, ZERO), position_id)
                if cleanup is not None:
                    self._lifecycle_state = LifecycleState.CLEANING
                    return [cleanup]
                if attributed_base > 0 and (
                    self._pool_prices[position_id] is None
                    or self._material_base(max(attributed_base, ZERO), position_id)
                ):
                    self._lifecycle_state = LifecycleState.BLOCKED
                    return []

            if latest_lp is not None and close_direction == "unknown":
                self._latch_fault(f"unclassified_lp_close:{latest_lp.id}")
                self._lifecycle_state = LifecycleState.FAULTED
                return []
            if latest_lp is not None and close_direction == "controller_stop":
                self._latch_fault(f"unexpected_controller_stop:{latest_lp.id}")
                self._lifecycle_state = LifecycleState.FAULTED
                return []
            if latest_lp is not None and close_direction in {"lower", "upper"}:
                cleanup_at = self._cleanup_completed_at(latest_lp)
                cooldown_until = (cleanup_at or float("inf")) + self.config.rebalance_cooldown_minutes * 60
                if cleanup_at is None or self._now() < cooldown_until:
                    self._lifecycle_state = LifecycleState.WAITING_FOR_TREND_REFRESH
                    self._readiness_state = ReadinessState.BLOCKED_TREND
                    continue
                if self._rearm_source_lp_ids[position_id] != latest_lp.id:
                    self._current_formations[position_id] = None

            failures, _, retry_after = self._consecutive_failures(position_id)
            if failures and retry_after is not None and self._now() < retry_after:
                self._lifecycle_state = LifecycleState.BLOCKED
                self._readiness_state = ReadinessState.WAITING_FOR_SETTLEMENT
                continue

            action = self._open_action(position_id=position_id)
            if action is not None:
                if latest_lp is not None and close_direction in {"lower", "upper"}:
                    self._rearm_source_lp_ids[position_id] = latest_lp.id
                self._lifecycle_state = (
                    LifecycleState.PREPARING
                    if self._executor_type_from_action(action) == "order_executor"
                    else LifecycleState.OPENING
                )
                return [action]

        self._lifecycle_state = LifecycleState.ACTIVE if active_lps else LifecycleState.BLOCKED
        self._readiness_state = ReadinessState.NOT_APPLICABLE if active_lps else self._readiness_state
        return []

    @staticmethod
    def _executor_type_from_action(action: CreateExecutorAction) -> str:
        return getattr(action.executor_config, "type", "")

    @staticmethod
    def _formation_payload(position: Optional[LPPositionConfig]) -> Optional[Dict]:
        if position is None:
            return None
        return {
            "market_trend": position.market_trend.value,
            "position_width_pct": float(position.position_width_pct),
            "downside_offset_pct": float(position.downside_offset_pct),
            "rebalance_threshold_pct": float(position.rebalance_threshold_pct),
        }

    def _ownership_state(self, position_id: str) -> str:
        if not self._ownership_checked_by_position[position_id] or self._ownership_errors[position_id]:
            return "UNKNOWN"
        addresses = self._owned_position_addresses[position_id]
        last_address = self._last_position_addresses[position_id]
        if last_address and last_address in addresses:
            return "PRESENT"
        if addresses:
            return "CONFLICT"
        return "ABSENT"

    def _position_error(self, position_id: str) -> Optional[str]:
        if self._restart_quarantined:
            return (
                "REUSED_CONTROLLER_ID"
                if self._fault_reason == "reused_controller_id"
                else "RESTART_HISTORY_UNAVAILABLE"
            )
        if self._token_symbol_errors[position_id] is not None:
            return "TOKEN_SYMBOL_MISMATCH"
        if self._ownership_errors[position_id]:
            return "OWNERSHIP_UNAVAILABLE"
        if self._pool_orientation_errors[position_id]:
            return "POOL_IDENTITY_MISMATCH"
        if (self._fault_reason or "").startswith(f"unmatched_onchain_position:{position_id}:"):
            return "ORPHAN_POSITION"
        if self._ownership_state(position_id) == "CONFLICT":
            return "ORPHAN_POSITION"
        cleanup_failures, _ = self._cleanup_failures(position_id)
        if cleanup_failures:
            return "CLEANUP_FAILED"
        failed_lp = next(
            (
                executor
                for executor in self._position_lp_executors(position_id)
                if (self._lp_failed(executor) or self._close_retries_exhausted(executor))
                and not self._recovered_failed_lp(executor)
                and executor.id not in self._reconciled_failure_ids
            ),
            None,
        )
        if failed_lp is not None:
            return "CLOSE_FAILED" if failed_lp.custom_info.get("position_address") else "CREATE_FAILED"
        if self._fault_reason and position_id in self._fault_reason:
            return self._fault_reason.upper()
        return None

    def _position_lifecycle(self, position_id: str, exit_reason: ExitReason) -> str:
        error = self._position_error(position_id)
        if error == "TOKEN_SYMBOL_MISMATCH" and exit_reason == ExitReason.NONE:
            return "BLOCKED"
        if error and self._fault_reason:
            return "FAULTED"
        active_orders = [
            order for order in self._active_orders() if self._position_id_for_order(order) == position_id
        ]
        if active_orders:
            return "CLEANING" if self._order_role(active_orders[0]).startswith(CLEANUP_ROLE) else "PREPARING"
        active_lps = [executor for executor in self._active_lps() if self._position_id_for_lp(executor) == position_id]
        if active_lps:
            state = str(active_lps[0].custom_info.get("state", ""))
            if state in {"NOT_ACTIVE", "OPENING"}:
                return "OPENING"
            if state in {"CLOSING", "SWAPPING"} or exit_reason != ExitReason.NONE:
                return "CLOSING"
            return "ACTIVE"
        if not self._ownership_checked_by_position[position_id]:
            return "RECOVERING"

        base, _ = self._inventory_ledger(position_id)
        material_base = base > 0 and (
            self._pool_prices[position_id] is None or self._material_base(base, position_id)
        )
        if exit_reason != ExitReason.NONE:
            if self._ownership_state(position_id) == "ABSENT" and not material_base:
                return "FAULTED" if self._runtime_exit_reason == ExitReason.FAULT else "EXITED"
            return "CLEANING" if material_base else "RECOVERING"

        lps = self._position_lp_executors(position_id)
        latest_lp = lps[-1] if lps else None
        if latest_lp is not None and latest_lp.is_done:
            if material_base:
                return "CLEANING"
            direction = self._close_direction(latest_lp)
            if direction in {"lower", "upper"}:
                cleanup_at = self._cleanup_completed_at(latest_lp)
                if cleanup_at is None or self._now() < cleanup_at + self.config.rebalance_cooldown_minutes * 60:
                    return "COOLDOWN"
        if self._pool_prices[position_id] is None:
            return "BLOCKED"
        return "PENDING"

    def _session_lifecycle_state(self, exit_reason: ExitReason) -> str:
        if self._lifecycle_state == LifecycleState.EXITED:
            return "EXITED"
        if self._lifecycle_state == LifecycleState.FAULTED:
            return "FAULTED"
        if exit_reason != ExitReason.NONE:
            return "EXITING"
        return "RUNNING"

    def _log_session_transition(self):
        exit_reason = self._runtime_exit_reason
        lifecycle_state = self._session_lifecycle_state(exit_reason)
        status = (lifecycle_state, exit_reason.value, self._fault_reason)
        if status == self._last_logged_session_status:
            return
        if lifecycle_state == "EXITING":
            message = f"Controller {self.config.id} is exiting: reason={exit_reason.value}"
            if self._fault_reason:
                self.logger().warning(f"{message}, root_cause={self._fault_reason}")
            else:
                self.logger().info(message)
        elif lifecycle_state in {"EXITED", "FAULTED"}:
            outcome = "success" if lifecycle_state == "EXITED" else "faulted"
            positions_closed = sum(
                self._ownership_state(position_id) == "ABSENT" for position_id in self._positions
            )
            cleanup_complete = not self._active_orders() and all(
                not (
                    (base := self._inventory_ledger(position_id)[0]) > 0
                    and (
                        self._pool_prices[position_id] is None
                        or self._material_base(base, position_id)
                    )
                )
                for position_id in self._positions
            )
            message = (
                f"Controller {self.config.id} stopped trading: outcome={outcome}, "
                f"exit_reason={exit_reason.value}, positions_closed={positions_closed}/{len(self._positions)}, "
                f"cleanup_complete={str(cleanup_complete).lower()}"
            )
            residuals = []
            for position_id in self._positions:
                ledger_base = max(self._inventory_ledger(position_id)[0], ZERO)
                executable_base = self._executable_cleanup_base(ledger_base, position_id)
                if (
                    ledger_base > 0
                    and self._successful_cleanup_in_current_generation(position_id)
                    and executable_base is not None
                    and not self._material_base(ledger_base, position_id)
                ):
                    residuals.append(
                        f"{position_id}:ledger={self._format_pnl(ledger_base)},"
                        f"wallet={self._format_pnl(self._available_wallet_base(position_id))},"
                        f"executable={self._format_pnl(executable_base)}"
                    )
            if residuals:
                message += f", unexecutable_cleanup_residuals={{{';'.join(residuals)}}}"
            pnl = self._reported_pnl_quote()
            pnl_ratio = pnl / self.config.total_amount_quote if pnl is not None else None
            position_pnl = ",".join(
                f"{position_id}:{self._format_pnl(self._reported_position_pnl_quote(position_id))}"
                for position_id in self._positions
            )
            message += (
                f", pnl_quote={self._format_pnl(pnl)} USDC, pnl_ratio={self._format_pnl(pnl_ratio)}, "
                f"position_pnl_quote={{{position_pnl}}}"
            )
            if self._fault_reason:
                message += f", fault_reason={self._fault_reason}"
            log = self.logger().info if lifecycle_state == "EXITED" else self.logger().error
            log(message)
        self._last_logged_session_status = status

    @staticmethod
    def _format_pnl(value: Optional[Decimal]) -> str:
        return "null" if value is None else format(value, "f")

    def _recovery_payload(self) -> Dict:
        executor = self._manual_close_recovery_candidate()
        executor_id = executor.id if executor is not None else None
        position_id = self._position_id_for_lp(executor) if executor is not None else None
        manual_retry_at = self._manual_recovery_retry_after.get(executor_id, 0.0) if executor_id else 0.0
        next_retry_at = max(self._gateway_read_retry_after, manual_retry_at)
        manual_error = self._manual_recovery_errors.get(executor_id) if executor_id else None

        if (self._fault_reason or "").startswith("manual_recovery_exhausted:"):
            state = "MANUAL_CLOSE_EXHAUSTED"
        elif self._gateway_read_error is not None:
            state = "WAITING_FOR_GATEWAY"
        elif executor is not None:
            state = "RECONCILING_POSITION"
        else:
            state = "IDLE"

        return {
            "state": state,
            "position_id": position_id,
            "executor_id": executor_id,
            "manual_close_attempts": self._manual_recovery_attempts.get(executor_id, 0) if executor_id else 0,
            "next_retry_at": next_retry_at if next_retry_at > self._now() else None,
            "last_error": self._gateway_read_error or manual_error,
        }

    def get_custom_info(self) -> dict:
        exit_reason = self._runtime_exit_reason
        lifecycle_state = self._session_lifecycle_state(exit_reason)

        pnl = self._reported_pnl_quote()
        positions = []
        position_capital_values = []
        for position_id, position in self._positions.items():
            active_executor = next(
                (
                    executor
                    for executor in [*self._active_orders(), *self._active_lps()]
                    if (
                        self._position_id_for_order(executor)
                        if self._executor_type(executor) == "order_executor"
                        else self._position_id_for_lp(executor)
                    ) == position_id
                ),
                None,
            )
            lps = self._position_lp_executors(position_id)
            latest_lp = next((executor for executor in reversed(lps) if self._landed_lp(executor)), None)
            custom = latest_lp.custom_info if latest_lp is not None else {}
            ownership_state = self._ownership_state(position_id)
            latest_address = custom.get("position_address")
            position_address = self._last_position_addresses[position_id] or (
                str(latest_address) if latest_address else None
            )
            if ownership_state == "CONFLICT" and len(self._owned_position_addresses[position_id]) == 1:
                position_address = self._owned_position_addresses[position_id][0]

            position_pnl = self._reported_position_pnl_quote(position_id)
            position_allocation = self.config.total_amount_quote * position.allocation_pct / ONE_HUNDRED
            capital_values = self._position_capital_values(position_id)
            position_capital_values.append(capital_values)
            allocated, current_total, lp_position, wallet_residual = capital_values

            def optional_float(value) -> Optional[float]:
                parsed = self._decimal(value, Decimal("NaN"))
                return float(parsed) if parsed.is_finite() else None

            positions.append(
                {
                    "position_id": position_id,
                    "pool_address": position.pool_address,
                    "lifecycle_state": self._position_lifecycle(position_id, exit_reason),
                    "executor_id": active_executor.id if active_executor is not None else None,
                    "position_address": position_address,
                    "ownership_state": ownership_state,
                    "lower_price": optional_float(custom.get("lower_price")),
                    "upper_price": optional_float(custom.get("upper_price")),
                    "lower_limit_price": optional_float(
                        getattr(latest_lp.config, "lower_limit_price", None) if latest_lp is not None else None
                    ),
                    "upper_limit_price": optional_float(
                        getattr(latest_lp.config, "upper_limit_price", None) if latest_lp is not None else None
                    ),
                    "pnl_quote": float(position_pnl) if position_pnl is not None else None,
                    "pnl_ratio": float(position_pnl / position_allocation) if position_pnl is not None else None,
                    "capital": {
                        "allocated_quote": float(allocated),
                        "current_total_value_quote": optional_float(current_total),
                        "lp_position_value_quote": optional_float(lp_position),
                        "wallet_residual_value_quote": optional_float(wallet_residual),
                    },
                    "formation": {
                        "current": self._formation_payload(self._current_formations[position_id]),
                        "next": self._formation_payload(position),
                    },
                    "error": self._position_error(position_id),
                }
            )

        def complete_sum(values: List[Optional[Decimal]]) -> Optional[Decimal]:
            return None if any(value is None for value in values) else sum(values, ZERO)

        allocated_total = self.config.total_amount_quote
        current_total = complete_sum([values[1] for values in position_capital_values])
        lp_position_total = complete_sum([values[2] for values in position_capital_values])
        wallet_residual_total = complete_sum([values[3] for values in position_capital_values])

        return {
            "schema_version": SCHEMA_VERSION,
            "reported_at": self._now(),
            "controller_id": self.config.id,
            "controller_started_at": self._controller_started_at,
            "lifecycle_state": lifecycle_state,
            "pnl_quote": float(pnl) if pnl is not None else None,
            "pnl_ratio": float(pnl / self.config.total_amount_quote) if pnl is not None else None,
            "capital": {
                "allocated_quote": float(allocated_total),
                "current_total_value_quote": optional_float(current_total),
                "lp_position_value_quote": optional_float(lp_position_total),
                "wallet_residual_value_quote": optional_float(wallet_residual_total),
            },
            "recovery": self._recovery_payload(),
            "positions": positions,
            "exit_reason": exit_reason.value,
            "fault_reason": self._fault_reason,
        }

    def to_format_status(self) -> List[str]:
        info = self.get_custom_info()
        lines = [
            f"Trend-aware LP session | {info['lifecycle_state']} | positions={len(info['positions'])} | "
            f"pnl={info['pnl_quote']} USDC"
        ]
        lines.extend(
            f"{position['position_id']} | {position['lifecycle_state']} | "
            f"ownership={position['ownership_state']} | pool={position['pool_address']}"
            for position in info["positions"]
        )
        return lines
