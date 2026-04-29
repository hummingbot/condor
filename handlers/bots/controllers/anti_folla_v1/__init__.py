"""Anti-Folla V1 Controller Module - Directional trading with crowd-contrarian indicators."""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import DEFAULTS, EDITABLE_FIELDS, FIELD_ORDER, FIELDS, generate_id, validate_config


class AntiFollaV1Controller(BaseController):
    controller_type = "anti_folla_v1"
    display_name = "Anti-Folla V1"
    description = "Crowd-contrarian directional trading: VWAP, Donchian, OBV, OBI, Volume Spike, Trade Flow, Funding Rate"

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


__all__ = ["AntiFollaV1Controller", "DEFAULTS", "FIELDS", "FIELD_ORDER", "EDITABLE_FIELDS",
           "validate_config", "generate_id", "generate_chart", "generate_preview_chart"]
