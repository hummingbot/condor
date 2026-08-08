"""
Statistical Arbitrage V2 Controller Module.

Trades two cointegrated assets on the same exchange.
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import DEFAULTS, FIELD_ORDER, FIELDS, WIZARD_STEPS, generate_id, validate_config


class StatArbV2Controller(BaseController):
    controller_type = "stat_arb_v2"
    display_name = "Statistical Arbitrage V2"
    description = "Trades two cointegrated assets, entering when z-score exceeds threshold"

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
    def generate_chart(
        cls,
        config: Dict[str, Any],
        candles_data: List[Dict[str, Any]],
        current_price: Optional[float] = None,
    ) -> io.BytesIO:
        return generate_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
        return generate_id(config, existing_configs)


__all__ = [
    "StatArbV2Controller",
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "WIZARD_STEPS",
    "validate_config",
    "generate_id",
    "generate_chart",
    "generate_preview_chart",
]
