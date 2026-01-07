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


# Registry of controller types
_CONTROLLER_REGISTRY: Dict[str, Type[BaseController]] = {
    "grid_strike": GridStrikeController,
    "pmm_mister": PmmMisterController,
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
        "fields": {name: {
            "label": field.label,
            "type": field.type,
            "required": field.required,
            "hint": field.hint,
        } for name, field in ctrl.get_fields().items()},
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
    # Backwards compatibility
    "SUPPORTED_CONTROLLERS",
]
