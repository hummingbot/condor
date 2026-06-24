"""
Funding Rate Arbitrage Controller Module

Perp↔Perp delta neutral and Spot↔Perp cash-and-carry arbitrage.
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import DEFAULTS, EDITABLE_FIELDS, FIELD_ORDER, FIELDS, generate_id, validate_config


class FundingRateArbController(BaseController):
    controller_type = "funding_rate_arb"
    display_name = "Funding Rate Arbitrage"
    description = "Multi-exchange funding rate arbitrage with hourly normalization"

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
        return validate_config(config)

    @classmethod
    def generate_chart(cls, config: Dict[str, Any], candles_data: List[Dict[str, Any]], current_price: Optional[float] = None) -> io.BytesIO:
        # Funding rate arb doesn't use candlestick charts
        # Generate a simple status chart instead
        return generate_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
        return generate_id(config, existing_configs)


__all__ = ["FundingRateArbController", "DEFAULTS", "FIELDS", "FIELD_ORDER", "EDITABLE_FIELDS",
           "validate_config", "generate_id", "generate_chart", "generate_preview_chart"]