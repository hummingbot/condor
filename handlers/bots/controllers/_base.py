"""
Base interface for controller types.

Each controller type (grid_strike, pmm, dca, etc.) should implement this interface
to provide consistent functionality across all controller types.
"""

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ControllerField:
    """Definition of a controller configuration field."""
    name: str
    label: str
    type: str  # "str", "int", "float", "bool"
    required: bool = False
    hint: str = ""
    default: Any = None


class BaseController(ABC):
    """Base class for controller type implementations."""

    # Controller type identifier (e.g., "grid_strike")
    controller_type: str = ""

    # Human-readable name
    display_name: str = ""

    # Short description
    description: str = ""

    @classmethod
    @abstractmethod
    def get_defaults(cls) -> Dict[str, Any]:
        """Get default configuration values for this controller type."""
        pass

    @classmethod
    @abstractmethod
    def get_fields(cls) -> Dict[str, ControllerField]:
        """Get field definitions for configuration form."""
        pass

    @classmethod
    @abstractmethod
    def get_field_order(cls) -> List[str]:
        """Get the order of fields for display."""
        pass

    @classmethod
    @abstractmethod
    def validate_config(cls, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate a configuration.

        Returns:
            Tuple of (is_valid, error_message)
        """
        pass

    @classmethod
    @abstractmethod
    def generate_chart(
        cls,
        config: Dict[str, Any],
        candles_data: List[Dict[str, Any]],
        current_price: Optional[float] = None
    ) -> io.BytesIO:
        """
        Generate a visualization chart for this controller configuration.

        Args:
            config: Controller configuration dict
            candles_data: OHLCV candle data
            current_price: Current market price

        Returns:
            BytesIO containing PNG image
        """
        pass

    @classmethod
    @abstractmethod
    def generate_id(
        cls,
        config: Dict[str, Any],
        existing_configs: List[Dict[str, Any]]
    ) -> str:
        """
        Generate a unique ID for this configuration.

        The ID should include a sequential number prefix for chronological ordering.
        Format: NNN_prefix_connector_pair

        Args:
            config: The configuration being created
            existing_configs: List of existing configurations to determine next number

        Returns:
            Generated config ID
        """
        pass

    @classmethod
    def get_next_sequence_number(cls, existing_configs: List[Dict[str, Any]]) -> int:
        """
        Get the next sequence number based on existing configs.

        Parses existing config IDs to find the highest number and returns +1.
        """
        max_num = 0

        for cfg in existing_configs:
            config_id = cfg.get("id", "")
            if not config_id:
                continue

            # Try to extract leading number (e.g., "001_gs_..." -> 1)
            parts = config_id.split("_", 1)
            if parts and parts[0].isdigit():
                num = int(parts[0])
                max_num = max(max_num, num)

        return max_num + 1

    @classmethod
    def format_sequence_number(cls, num: int, width: int = 3) -> str:
        """Format sequence number with leading zeros."""
        return str(num).zfill(width)
