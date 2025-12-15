"""
PMM Mister Controller Module

Provides configuration, validation, and visualization for PMM (Pure Market Making) controllers.

PMM Mister is an advanced market making strategy that:
- Places buy/sell orders at configurable spread levels
- Manages position with target/min/max base percentages
- Features hanging executors and breakeven awareness
- Supports price distance requirements and cooldowns
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .config import (
    DEFAULTS,
    FIELDS,
    FIELD_ORDER,
    WIZARD_STEPS,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_LABELS,
    validate_config,
    generate_id,
    parse_spreads,
    format_spreads,
)
from .chart import generate_chart, generate_preview_chart
from .pmm_analysis import (
    calculate_natr,
    calculate_price_stats,
    suggest_pmm_params,
    generate_theoretical_levels,
    format_pmm_summary,
    calculate_effective_spread,
)


class PmmMisterController(BaseController):
    """PMM Mister controller implementation."""

    controller_type = "pmm_mister"
    display_name = "PMM Mister"
    description = "Advanced pure market making with position management"

    @classmethod
    def get_defaults(cls) -> Dict[str, Any]:
        """Get default configuration values."""
        return DEFAULTS.copy()

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
    "PmmMisterController",
    # Config
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "WIZARD_STEPS",
    "ORDER_TYPE_MARKET",
    "ORDER_TYPE_LIMIT",
    "ORDER_TYPE_LIMIT_MAKER",
    "ORDER_TYPE_LABELS",
    # Functions
    "validate_config",
    "generate_id",
    "parse_spreads",
    "format_spreads",
    "generate_chart",
    "generate_preview_chart",
    # PMM analysis
    "calculate_natr",
    "calculate_price_stats",
    "suggest_pmm_params",
    "generate_theoretical_levels",
    "format_pmm_summary",
    "calculate_effective_spread",
]
