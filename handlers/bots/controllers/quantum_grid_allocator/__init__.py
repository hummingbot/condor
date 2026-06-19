"""
Quantum Grid Allocator Controller Module

Portfolio rebalancing with grid trading on multiple assets.
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .config import DEFAULTS, EDITABLE_FIELDS, FIELD_ORDER, FIELDS, generate_id, validate_config


class QuantumGridAllocatorController(BaseController):
    controller_type = "quantum_grid_allocator"
    display_name = "Quantum Grid Allocator"
    description = "Portfolio rebalancing with grid trading on multiple assets"

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
        from handlers.dex.visualizations import generate_candlestick_chart
        return generate_candlestick_chart(candles_data, title=f"Quantum Grid Allocator - Portfolio")

    @classmethod
    def generate_id(cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
        return generate_id(config, existing_configs)


__all__ = ["QuantumGridAllocatorController"]


