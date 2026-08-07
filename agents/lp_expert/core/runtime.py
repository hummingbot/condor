"""Exact, in-process runtime authority for the LP Expert Agent.

This module deliberately does not call Condor's authenticated HTTP API. Agent
routines already execute in the Condor process, so the live TickEngine registry
is the narrowest authoritative source for the current controller.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

AGENT_SLUG = "lp_expert"
NETWORK = "solana-mainnet-beta"
SWAP_CONNECTOR = "jupiter"
QUOTE_SYMBOL = "USDC"
QUOTE_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
QUOTE_DECIMALS = 6

_CONTROLLER = re.compile(
    r"^lp_expert\.(?P<strategy>[a-z][a-z0-9_-]*)_(?P<suffix>e?[1-9]\d*)$"
)
_REQUIRED_CONFIG = {
    "execution_mode",
    "server_name",
    "account_name",
    "default_risk_posture",
    "total_amount_quote",
    "max_open_executors",
    "max_slot_deployments_per_tick",
    "candidate_scan_limit",
    "min_quote_per_executor",
    "max_quote_per_executor",
    "max_slippage_pct",
    "min_sol_reserve",
    "residual_base_dust_quote",
    "minimum_range_half_width_pct",
    "maximum_range_half_width_pct",
    "rebalance_threshold_pct",
    "executor_max_age_minutes",
    "executor_take_profit_net_pnl_ratio",
    "executor_stop_loss_net_pnl_ratio",
    "session_max_age_minutes",
    "session_take_profit_net_pnl_ratio",
    "session_stop_loss_net_pnl_ratio",
    "risk_limits",
}


@dataclass(frozen=True, slots=True)
class RuntimeScope:
    """Frozen authority and external scope for one active LP Expert controller."""

    controller_id: str
    strategy_slug: str
    execution_mode: str
    server_name: str
    account_name: str
    network: str
    swap_connector: str
    quote_symbol: str
    quote_mint: str
    quote_decimals: int
    tick_count: int
    current_tick: int
    tick_started_at: float | None
    last_tick_at: float | None
    session_dir: Path | None
    config: Mapping[str, Any]
    wallet_address: str | None = None

    @property
    def tick_number(self) -> int:
        """Compatibility alias for the current in-progress tick."""

        return self.current_tick

    def with_wallet(self, wallet_address: str) -> "RuntimeScope":
        if not isinstance(wallet_address, str) or not wallet_address.strip():
            raise ValueError("current Solana default wallet is unavailable")
        return RuntimeScope(
            controller_id=self.controller_id,
            strategy_slug=self.strategy_slug,
            execution_mode=self.execution_mode,
            server_name=self.server_name,
            account_name=self.account_name,
            network=self.network,
            swap_connector=self.swap_connector,
            quote_symbol=self.quote_symbol,
            quote_mint=self.quote_mint,
            quote_decimals=self.quote_decimals,
            tick_count=self.tick_count,
            current_tick=self.current_tick,
            tick_started_at=self.tick_started_at,
            last_tick_at=self.last_tick_at,
            session_dir=self.session_dir,
            config=self.config,
            wallet_address=wallet_address.strip(),
        )


def decimal_config(
    config: Mapping[str, Any],
    key: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    """Read one finite Decimal from frozen Strategy config."""

    try:
        result = Decimal(str(config[key]))
    except Exception as exc:
        raise ValueError(f"configured {key} must be a decimal") from exc
    if (
        not result.is_finite()
        or (positive and result <= 0)
        or (non_negative and result < 0)
    ):
        raise ValueError(f"configured {key} is outside its valid range")
    return result


def integer_config(
    config: Mapping[str, Any], key: str, *, minimum: int = 1, maximum: int | None = None
) -> int:
    """Read one exact bounded integer from frozen Strategy config."""

    number = decimal_config(config, key)
    if (
        isinstance(config[key], bool)
        or number != number.to_integral_value()
        or number < minimum
        or (maximum is not None and number > maximum)
    ):
        raise ValueError(f"configured {key} is outside its valid range")
    return int(number)


def _validate_config(value: Any, mode: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not _REQUIRED_CONFIG <= set(value):
        raise ValueError("frozen LP Strategy config is incomplete")
    if value.get("execution_mode") != mode:
        raise ValueError("frozen config conflicts with active execution mode")
    for key in ("server_name", "account_name"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"configured {key} is unavailable")

    total = decimal_config(value, "total_amount_quote", positive=True)
    minimum = decimal_config(value, "min_quote_per_executor", positive=True)
    maximum = decimal_config(value, "max_quote_per_executor", positive=True)
    max_open = integer_config(value, "max_open_executors")
    deployments = integer_config(value, "max_slot_deployments_per_tick")
    candidate_scan_limit = integer_config(value, "candidate_scan_limit")
    slippage = decimal_config(value, "max_slippage_pct", positive=True)
    reserve = decimal_config(value, "min_sol_reserve", positive=True)
    dust = decimal_config(value, "residual_base_dust_quote", non_negative=True)
    range_minimum = decimal_config(value, "minimum_range_half_width_pct", positive=True)
    range_maximum = decimal_config(value, "maximum_range_half_width_pct", positive=True)
    rebalance = decimal_config(value, "rebalance_threshold_pct", positive=True)
    executor_age = integer_config(value, "executor_max_age_minutes")
    executor_take_profit = decimal_config(
        value, "executor_take_profit_net_pnl_ratio", positive=True
    )
    executor_stop_loss = decimal_config(
        value, "executor_stop_loss_net_pnl_ratio", positive=True
    )
    session_age = integer_config(value, "session_max_age_minutes")
    session_take_profit = decimal_config(
        value, "session_take_profit_net_pnl_ratio", positive=True
    )
    session_stop_loss = decimal_config(
        value, "session_stop_loss_net_pnl_ratio", positive=True
    )
    risks = value.get("risk_limits")
    if not isinstance(risks, dict):
        raise ValueError("configured risk_limits are unavailable")
    try:
        risk_position = Decimal(str(risks.get("max_position_size_quote")))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(
            "configured position or open-executor risk is invalid"
        ) from exc
    risk_open = integer_config(risks, "max_open_executors")
    try:
        soft_drawdown = Decimal(str(risks.get("max_drawdown_pct")))
        shutdown_drawdown = Decimal(str(risks.get("shutdown_drawdown_pct", -1)))
    except Exception as exc:
        raise ValueError("configured drawdown limits are invalid") from exc
    drawdowns_valid = all(
        item.is_finite()
        and (item == Decimal("-1") or Decimal("0") <= item <= Decimal("100"))
        for item in (soft_drawdown, shutdown_drawdown)
    )
    drawdown_ordered = (
        soft_drawdown == -1
        or shutdown_drawdown == -1
        or shutdown_drawdown >= soft_drawdown
    )
    if (
        value.get("default_risk_posture")
        not in {"steady", "balanced", "opportunistic", "exploratory"}
        or deployments > max_open
        or candidate_scan_limit < deployments
        or minimum > maximum
        or maximum > total
        or not risk_position.is_finite()
        or risk_position <= 0
        or total > risk_position
        or risk_open < max_open
        or not Decimal("0") < slippage <= Decimal("100")
        or reserve <= 0
        or dust >= minimum
        or not Decimal("0") < range_minimum <= range_maximum < Decimal("100")
        or not Decimal("0") < rebalance < Decimal("100")
        or executor_age < 1
        or session_age < 1
        or any(
            item <= 0
            for item in (
                executor_take_profit,
                executor_stop_loss,
                session_take_profit,
                session_stop_loss,
            )
        )
        or not drawdowns_valid
        or not drawdown_ordered
    ):
        raise ValueError("frozen LP Strategy limits are inconsistent")
    return copy.deepcopy(value)


def _loop_config(engine: Any, strategy_slug: str, number: str) -> dict[str, Any]:
    session_dir = getattr(engine, "session_dir", None)
    if not isinstance(session_dir, Path):
        raise ValueError("current loop session directory is unavailable")
    strategy_dir = Path(engine.strategy.dir).resolve()
    expected = strategy_dir / "sessions" / f"session_{number}"
    if (
        session_dir.is_symlink()
        or session_dir.resolve() != expected.resolve()
        or not session_dir.resolve().is_relative_to(strategy_dir)
    ):
        raise ValueError("current loop session directory is unsafe")
    path = session_dir / "config.yml"
    if not path.is_file() or path.is_symlink():
        raise ValueError("current loop session config is unavailable")
    before = path.stat()
    value = yaml.safe_load(path.read_text())
    after = path.stat()
    if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
        raise ValueError("current loop session config changed while being read")
    if strategy_slug != Path(engine.strategy.dir).name:
        raise ValueError("active Strategy directory identity is inconsistent")
    return value


def resolve_runtime(controller_id: str) -> RuntimeScope:
    """Resolve and validate one exact running LP Expert TickEngine."""

    from condor.agents.engine import get_engine

    match = _CONTROLLER.fullmatch(str(controller_id))
    if match is None:
        raise ValueError("invalid lp_expert controller identity")
    engine = get_engine(controller_id)
    if engine is None:
        raise ValueError("active Condor instance was not found")
    strategy_slug = match.group("strategy")
    mode = (
        engine.config.get("execution_mode") if isinstance(engine.config, dict) else None
    )
    expected_suffix = r"[1-9]\d*" if mode == "loop" else r"e[1-9]\d*"
    if (
        getattr(getattr(engine, "agent", None), "slug", None) != AGENT_SLUG
        or getattr(engine, "agent_id", None) != controller_id
        or getattr(getattr(engine, "strategy", None), "slug", None) != strategy_slug
        or mode not in {"dry_run", "run_once", "loop"}
        or re.fullmatch(expected_suffix, match.group("suffix")) is None
        or getattr(engine, "status", None) != "running"
    ):
        raise ValueError("active Condor identity, mode, or lifecycle is invalid")

    if mode == "loop":
        number = match.group("suffix")
        source = _loop_config(engine, strategy_slug, number)
        session_dir = Path(engine.session_dir).resolve()
        journal = getattr(engine, "journal", None)
        if journal is None or not isinstance(getattr(journal, "tick_count", None), int):
            raise ValueError("current loop tick authority is unavailable")
        tick_count = journal.tick_count
        current_tick = tick_count + 1
    else:
        source = engine.config
        session_dir = None
        tick_count = 0
        current_tick = 1
    raw_tick_at = getattr(engine, "_last_tick_at", None)
    tick_started_at = (
        float(raw_tick_at)
        if isinstance(raw_tick_at, (int, float)) and not isinstance(raw_tick_at, bool)
        else None
    )

    frozen = _validate_config(source, mode)
    engine_frozen = _validate_config(engine.config, mode)
    for key in _REQUIRED_CONFIG:
        if frozen.get(key) != engine_frozen.get(key):
            raise ValueError(f"current session config conflicts on {key}")
    info = engine.get_info()
    if (
        not isinstance(info, dict)
        or info.get("agent_id") != controller_id
        or info.get("strategy_slug") != strategy_slug
        or info.get("status") != "running"
        or info.get("server_name") != frozen["server_name"]
    ):
        raise ValueError("active engine summary conflicts with frozen authority")

    return RuntimeScope(
        controller_id=controller_id,
        strategy_slug=strategy_slug,
        execution_mode=mode,
        server_name=frozen["server_name"].strip(),
        account_name=frozen["account_name"].strip(),
        network=NETWORK,
        swap_connector=SWAP_CONNECTOR,
        quote_symbol=QUOTE_SYMBOL,
        quote_mint=QUOTE_MINT,
        quote_decimals=QUOTE_DECIMALS,
        tick_count=tick_count,
        current_tick=current_tick,
        tick_started_at=tick_started_at,
        last_tick_at=tick_started_at,
        session_dir=session_dir,
        config=MappingProxyType(frozen),
    )


async def get_hummingbot_client(scope: RuntimeScope) -> Any:
    """Return the Hummingbot client for the frozen server binding."""

    from config_manager import get_config_manager

    return await get_config_manager().get_client(scope.server_name)


async def bind_wallet(scope: RuntimeScope, client: Any) -> RuntimeScope:
    """Bind the exact configured network's current default wallet."""

    network = await client.gateway.get_network_config(scope.network)
    wallet = network.get("default_wallet") if isinstance(network, dict) else None
    if not isinstance(wallet, str) or not wallet.strip():
        raise ValueError("current Solana default wallet binding is unavailable")
    return scope.with_wallet(wallet)


async def refresh_balances(scope: RuntimeScope, client: Any) -> list[dict[str, Any]]:
    """Refresh balances for only the frozen account/network scope."""

    state = await client.portfolio.get_state(
        account_names=[scope.account_name],
        connector_names=[scope.network],
        refresh=True,
    )
    rows = (
        state.get(scope.account_name, {}).get(scope.network)
        if isinstance(state, dict) and isinstance(state.get(scope.account_name), dict)
        else None
    )
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("scoped portfolio balances are unavailable")
    return rows
