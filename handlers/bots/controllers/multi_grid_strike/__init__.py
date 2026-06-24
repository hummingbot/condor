"""
Multi Grid Strike Controller Module

Provides configuration, validation, and visualization for multi grid strike controllers.

MultiGridStrike is a strategy that runs multiple independent grids on the same
trading pair, each covering a different price range. Each grid:
- Has its own start_price / end_price / limit_price
- Allocates a percentage of total_amount_quote (amount_quote_pct)
- Can be LONG or SHORT independently
- Is activated only when the market price enters its range

This allows building layered grid strategies (e.g. a tight grid near current price
+ a wider catch grid below/above) with a single bot instance.
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import (
    DEFAULTS,
    EDITABLE_FIELDS,
    FIELD_ORDER,
    FIELDS,
    GRID_TYPES,
    MGS_WIZARD_STEPS,
    ORDER_TYPE_LABELS,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_MARKET,
    SIDE_LONG,
    SIDE_SHORT,
    WIZARD_STEPS,
    calculate_auto_prices_for_grid,
    generate_id,
    validate_config,
)
from .grid_analysis import (
    analyze_all_grids,
    calculate_natr,
    calculate_price_stats,
    calculate_optimal_multi_grids,
    format_multi_grid_summary,
    generate_theoretical_grid,
    suggest_multi_grid_params,
)


class MultiGridStrikeController(BaseController):
    """Multi Grid Strike controller implementation."""

    controller_type = "multi_grid_strike"
    display_name = "Multi Grid Strike"
    description = "Multiple independent grids on the same pair"

    @classmethod
    def get_defaults(cls) -> Dict[str, Any]:
        """Get default configuration values."""
        defaults = DEFAULTS.copy()
        if "triple_barrier_config" in defaults:
            defaults["triple_barrier_config"] = defaults["triple_barrier_config"].copy()
        defaults["grids"] = []
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
        current_price: Optional[float] = None,
    ) -> io.BytesIO:
        """Generate visualization chart."""
        return generate_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(
        cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]
    ) -> str:
        """Generate unique ID with sequence number."""
        return generate_id(config, existing_configs)


__all__ = [
    # Controller class
    "MultiGridStrikeController",
    # Config
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "WIZARD_STEPS",
    "MGS_WIZARD_STEPS",
    "EDITABLE_FIELDS",
    "GRID_TYPES",
    "SIDE_LONG",
    "SIDE_SHORT",
    "ORDER_TYPE_MARKET",
    "ORDER_TYPE_LIMIT",
    "ORDER_TYPE_LIMIT_MAKER",
    "ORDER_TYPE_LABELS",
    # Functions
    "validate_config",
    "calculate_auto_prices_for_grid",
    "generate_id",
    "generate_chart",
    "generate_preview_chart",
    # Grid analysis
    "calculate_natr",
    "calculate_price_stats",
    "analyze_all_grids",
    "generate_theoretical_grid",
    "calculate_optimal_multi_grids",
]
