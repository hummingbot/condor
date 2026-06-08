"""
Controller Registry

Provides a unified interface for accessing controller type implementations.
Each controller type (grid_strike, pmm, etc.) has its own module with:
- Configuration defaults and field definitions
- Validation logic
- Chart/visualization generation
- ID generation with chronological numbering
"""

from typing import Dict, List, Optional, Type

from ._base import BaseController, ControllerField
from .grid_strike import GridStrikeController
from .pmm_mister import PmmMisterController
from .pmm_v1 import PmmV1Controller
from .arbitrage_controller import ArbitrageControllerController
from .dman_v3 import DManV3Controller
from .multi_grid_strike import MultiGridStrikeController
from .xemm_multiple_levels import XEMMMultipleLevelsController
from .macd_bb_v1 import MacdBbV1Controller
from .supertrend_v1 import SuperTrendV1Controller
from .anti_folla_v1 import AntiFollaV1Controller
from .funding_rate_arb import FundingRateArbController
from .delta_neutral_mm import DeltaNeutralMMController
from .bollingrid import BollinGridController
from .quantum_grid_allocator import QuantumGridAllocatorController
from .stat_arb_v2 import StatArbV2Controller
from .lm_multi_pair_dex import LMMultiPairDEXController
# Registry of controller types
_CONTROLLER_REGISTRY: Dict[str, Type[BaseController]] = {
    "grid_strike": GridStrikeController,
    "pmm_mister": PmmMisterController,
    "pmm_v1": PmmV1Controller,
    "dman_v3": DManV3Controller,
    "arbitrage_controller": ArbitrageControllerController,
    "xemm_multiple_levels": XEMMMultipleLevelsController,
    "macd_bb_v1": MacdBbV1Controller,
    "supertrend_v1": SuperTrendV1Controller,
    "anti_folla_v1": AntiFollaV1Controller,
    "funding_rate_arb": FundingRateArbController,
    "delta_neutral_mm": DeltaNeutralMMController,
    "bollingrid": BollinGridController,
    "quantum_grid_allocator": QuantumGridAllocatorController,
    "stat_arb_v2": LMMultiPairDEXController,
    "lm_multi_pair_dex": LMMultiPairDEXController,
}


def get_controller(controller_type: str) -> Optional[Type[BaseController]]:
    """
    Get a controller class by type.

    Args:
        controller_type: The controller type identifier (e.g., "grid_strike")

    Returns:
        Controller class or None if not found
    """
    return _CONTROLLER_REGISTRY.get(controller_type)


def list_controllers() -> Dict[str, Type[BaseController]]:
    """
    Get all registered controllers.

    Returns:
        Dict mapping controller type to controller class
    """
    return _CONTROLLER_REGISTRY.copy()


def get_supported_controller_types() -> List[str]:
    """Get list of supported controller type identifiers."""
    return list(_CONTROLLER_REGISTRY.keys())


def get_controller_info() -> Dict[str, Dict[str, str]]:
    """
    Get display info for all controllers.

    Returns:
        Dict mapping type to {name, description}
    """
    return {
        ctrl_type: {
            "name": ctrl.display_name,
            "description": ctrl.description,
        }
        for ctrl_type, ctrl in _CONTROLLER_REGISTRY.items()
    }


# For backwards compatibility, also export the registry as SUPPORTED_CONTROLLERS
SUPPORTED_CONTROLLERS = {
    ctrl_type: {
        "name": ctrl.display_name,
        "description": ctrl.description,
        "defaults": ctrl.get_defaults(),
        "fields": {
            name: {
                "label": field.label,
                "type": field.type,
                "required": field.required,
                "hint": field.hint,
            }
            for name, field in ctrl.get_fields().items()
        },
        "field_order": ctrl.get_field_order(),
    }
    for ctrl_type, ctrl in _CONTROLLER_REGISTRY.items()
}


__all__ = [
    # Registry functions
    "get_controller",
    "list_controllers",
    "get_supported_controller_types",
    "get_controller_info",
    # Base class
    "BaseController",
    "ControllerField",
    # Controller implementations
    "GridStrikeController",
    "PmmMisterController",
    "PmmV1Controller",
    "DManV3Controller",
    "ArbitrageControllerController",
    "XEMMMultipleLevelsController",
    "MacdBbV1Controller",
    "SuperTrendV1Controller",
    "AntiFollaV1Controller",
    "FundingRateArbController",
    "DeltaNeutralMMController",
    "BollinGridController",
    "QuantumGridAllocatorController",
    "StatArbV2Controller",
    "LMMultiPairDEXController",
    # Backwards compatibility
    "SUPPORTED_CONTROLLERS",
]
