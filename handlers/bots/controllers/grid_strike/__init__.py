"""
Grid Strike Controller Module

Provides configuration, validation, and visualization for grid strike controllers.

Grid Strike is a grid trading strategy that:
- Places orders within a defined price range (start_price to end_price)
- Has a limit_price as stop loss
- Can be LONG (buy low, sell high) or SHORT (sell high, buy low)
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .config import (
    DEFAULTS,
    FIELDS,
    FIELD_ORDER,
    WIZARD_STEPS,
    SIDE_LONG,
    SIDE_SHORT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_LABELS,
    validate_config,
    calculate_auto_prices,
    generate_id,
)
from .chart import generate_chart, generate_preview_chart


class GridStrikeController(BaseController):
    """Grid Strike controller implementation."""

    controller_type = "grid_strike"
    display_name = "Grid Strike"
    description = "Grid trading with stop-limit orders"

    @classmethod
    def get_defaults(cls) -> Dict[str, Any]:
        """Get default configuration values."""
        # Return a deep copy to prevent mutation
        defaults = DEFAULTS.copy()
        if "triple_barrier_config" in defaults:
            defaults["triple_barrier_config"] = defaults["triple_barrier_config"].copy()
        return defaults

    @classmethod
    def get_fields(cls) -> Dict[str, ControllerField]:
        """Get field definitions."""
        return FIELDS

    @classmethod
    def get_field_order(cls) -> List[str]:
        """Get field display order."""
        return FIELD_ORDER

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate configuration."""
        return validate_config(config)

    @classmethod
    def generate_chart(
        cls,
        config: Dict[str, Any],
        candles_data: List[Dict[str, Any]],
        current_price: Optional[float] = None
    ) -> io.BytesIO:
        """Generate visualization chart."""
        return generate_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(
        cls,
        config: Dict[str, Any],
        existing_configs: List[Dict[str, Any]]
    ) -> str:
        """Generate unique ID with sequence number."""
        return generate_id(config, existing_configs)


# Export commonly used items at module level
__all__ = [
    # Controller class
    "GridStrikeController",
    # Config
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "WIZARD_STEPS",
    "SIDE_LONG",
    "SIDE_SHORT",
    "ORDER_TYPE_MARKET",
    "ORDER_TYPE_LIMIT",
    "ORDER_TYPE_LIMIT_MAKER",
    "ORDER_TYPE_LABELS",
    # Functions
    "validate_config",
    "calculate_auto_prices",
    "generate_id",
    "generate_chart",
    "generate_preview_chart",
]
