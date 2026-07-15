"""Condor-native executors — the write path of condor-simple (docs/condor-simple.md).

An executor is a declarative trade intent run by a deterministic state machine
against native connectors. The composite ``type`` is ``{kind}_{instrument}``:
the **kind** (order | position) picks the executor class, and **(instrument,
venue)** picks the connector + instrument adapter. Condor owns the contract
(config schema, lifecycle, risk declaration, reporting); the connector owns the
keys.
"""

from condor.executors.adapters import (
    HyperliquidPredAdapter,
    InstrumentAdapter,
    PerpAdapter,
    PolymarketPredAdapter,
    SpotAdapter,
    make_adapter,
)
from condor.executors.base import (
    BarrierFields,
    ExecutorBase,
    ExecutorConfig,
    ExecutorStatus,
    RiskDeclaration,
)
from condor.executors.hyperliquid import HyperliquidClient
from condor.executors.hyperliquid_outcome import HyperliquidOutcomeClient
from condor.executors.hyperliquid_spot import HyperliquidSpotClient
from condor.executors.jupiter import JupiterConnector, JupiterSwap
from condor.executors.log import ExecutorLog
from condor.executors.order import (
    OrderExecutor,
    OrderPredConfig,
    OrderPerpConfig,
    OrderSpotConfig,
    OrderState,
    OrderStates,
)
from condor.executors.polymarket import PolymarketClient
from condor.executors.position import (
    PositionExecutor,
    PositionPredConfig,
    PositionPerpConfig,
    PositionSpotConfig,
    PositionState,
    PositionStates,
)
from condor.executors.records import ExecutorRecord
from condor.executors.runtime import ExecutorRuntime
from condor.executors.solana import SolanaConnector

__all__ = [
    "BarrierFields",
    "ExecutorBase",
    "ExecutorConfig",
    "ExecutorLog",
    "ExecutorRecord",
    "ExecutorRuntime",
    "ExecutorStatus",
    "HyperliquidClient",
    "HyperliquidOutcomeClient",
    "HyperliquidPredAdapter",
    "HyperliquidSpotClient",
    "InstrumentAdapter",
    "JupiterConnector",
    "JupiterSwap",
    "OrderExecutor",
    "OrderPredConfig",
    "OrderPerpConfig",
    "OrderSpotConfig",
    "OrderState",
    "OrderStates",
    "PerpAdapter",
    "PolymarketClient",
    "PolymarketPredAdapter",
    "PositionExecutor",
    "PositionPredConfig",
    "PositionPerpConfig",
    "PositionSpotConfig",
    "PositionState",
    "PositionStates",
    "RiskDeclaration",
    "SolanaConnector",
    "SpotAdapter",
    "make_adapter",
]
