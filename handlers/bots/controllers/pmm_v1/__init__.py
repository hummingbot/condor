"""
PMM V1 Controller Module

Provides configuration, validation, and visualization for PMM V1 (Pure Market Making) controllers.

PMM V1 is a simple market making strategy that:
- Places buy/sell orders at configurable spread levels
- Supports order refresh and age-based cancellation
- Optional inventory skew management
- Price ceiling/floor boundaries
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import (
    DEFAULTS,
    FIELD_ORDER,
    FIELDS,
    WIZARD_STEPS,
    format_spreads,
    generate_id,
    parse_spreads,
    validate_config,
)


class PmmV1Controller(BaseController):
    """PMM V1 controller implementation."""

    controller_type = "pmm_v1"
    display_name = "PMM V1"
    description = "Simple pure market making with spread-based orders"

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


# Export commonly used items at module level
__all__ = [
    # Controller class
    "PmmV1Controller",
    # Config
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "WIZARD_STEPS",
    # Functions
    "validate_config",
    "generate_id",
    "parse_spreads",
    "format_spreads",
    "generate_chart",
    "generate_preview_chart",
]
