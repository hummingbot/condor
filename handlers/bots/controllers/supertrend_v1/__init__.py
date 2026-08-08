"""SuperTrend V1 Controller Module - Directional trading with SuperTrend indicator."""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import DEFAULTS, EDITABLE_FIELDS, FIELD_ORDER, FIELDS, generate_id, validate_config


class SuperTrendV1Controller(BaseController):
    controller_type = "supertrend_v1"
    display_name = "SuperTrend V1"
    description = "Directional trading with SuperTrend indicator (ATR-based trend following)"

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
        return generate_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
        return generate_id(config, existing_configs)


__all__ = ["SuperTrendV1Controller", "DEFAULTS", "FIELDS", "FIELD_ORDER", "EDITABLE_FIELDS",
           "validate_config", "generate_id", "generate_chart", "generate_preview_chart"]
