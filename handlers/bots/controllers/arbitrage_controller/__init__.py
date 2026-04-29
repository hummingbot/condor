"""Arbitrage Controller Module - CEX/DEX arbitrage with grid analysis and auto-optimization."""

import asyncio
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import (
    DEFAULTS, EDITABLE_FIELDS, FIELD_ORDER, FIELDS,
    generate_id, validate_config, auto_optimize_config, validate_config_dynamic
)
from .grid_analysis import run_grid_analysis, GridAnalyzer

logger = logging.getLogger(__name__)


class ArbitrageControllerController(BaseController):
    controller_type = "arbitrage_controller"
    display_name = "Arbitrage"
    description = "CEX/DEX arbitrage with grid analysis and auto-optimization"

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
    async def validate_config_async(cls, config: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Validate configuration asynchronously with exchange queries."""
        return await validate_config_dynamic(config)

    @classmethod
    async def auto_optimize(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-optimize configuration based on market conditions."""
        return await auto_optimize_config(config)

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
    async def analyze_grid(
        cls,
        config: Dict[str, Any],
        price1: float,
        price2: float,
        order_book1: List,
        order_book2: List
    ) -> Dict[str, Any]:
        """Run grid analysis for optimal entry points."""
        return await run_grid_analysis(config, price1, price2, order_book1, order_book2)


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
    "GridAnalyzer",
    "run_grid_analysis"
]
