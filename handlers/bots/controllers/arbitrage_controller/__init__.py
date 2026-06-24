"""Arbitrage Controller Module - CEX/DEX arbitrage with historical analysis."""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import (
    DEFAULTS, EDITABLE_FIELDS, FIELD_ORDER, FIELDS,
    generate_id, validate_config
)
from .analysis import analyze_historical_spread, ArbitrageAnalyzer


class ArbitrageControllerController(BaseController):
    controller_type = "arbitrage_controller"
    display_name = "Arbitrage"
    description = "CEX/DEX arbitrage with historical spread analysis"

    @classmethod
    def get_defaults(cls) -> Dict[str, Any]:
        return DEFAULTS.copy()

    @classmethod
    def get_fields(cls) -> Dict[str, ControllerField]:
        return FIELDS

    @classmethod
    def get_field_order(cls) -> List[str]:
        return FIELD_ORDER

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate configuration synchronously."""
        return validate_config(config)

    @classmethod
    def generate_chart(
        cls,
        config: Dict[str, Any],
        candles_data: List[Dict[str, Any]],
        current_price: Optional[float] = None,
        grid_analysis: Optional[Dict[str, Any]] = None
    ) -> io.BytesIO:
        """Generate chart with optional grid analysis overlay."""
        return generate_chart(config, candles_data, current_price, grid_analysis)

    @classmethod
    def generate_preview_chart(
        cls,
        config: Dict[str, Any],
        candles_data: List[Dict[str, Any]],
        current_price: Optional[float] = None
    ) -> io.BytesIO:
        """Generate preview chart."""
        return generate_preview_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
        """Generate unique ID for the configuration."""
        return generate_id(config, existing_configs)

    @classmethod
    async def analyze_historical_spread(
        cls,
        candles1: List[Dict[str, Any]],
        candles2: List[Dict[str, Any]],
        config: Dict[str, Any],
        fee_1: Optional[float] = None,
        fee_2: Optional[float] = None
    ) -> Dict[str, Any]:
        """Run historical spread analysis for fee and profitability assessment."""
        return await analyze_historical_spread(candles1, candles2, config, fee_1, fee_2)


__all__ = [
    "ArbitrageControllerController",
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "EDITABLE_FIELDS",
    "validate_config",
    "generate_id",
    "generate_chart",
    "generate_preview_chart",
    "analyze_historical_spread",
    "ArbitrageAnalyzer"
]
