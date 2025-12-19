"""
Base classes and discovery for routines.

Routine Types:
- Interval: Has `interval_sec` field in Config → runs repeatedly at interval
- One-shot: No `interval_sec` field → runs once and returns result
"""

import importlib
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_routines_cache: dict[str, "RoutineInfo"] | None = None


class RoutineInfo:
    """Metadata container for a discovered routine."""

    def __init__(
        self,
        name: str,
        config_class: type[BaseModel],
        run_fn: Callable[[BaseModel, Any], Awaitable[str]],
    ):
        self.name = name
        self.config_class = config_class
        self.run_fn = run_fn

        # Extract description from Config docstring
        doc = config_class.__doc__ or name
        self.description = doc.strip().split("\n")[0]

    @property
    def is_interval(self) -> bool:
        """Check if this is an interval routine (has interval_sec field)."""
        return "interval_sec" in self.config_class.model_fields

    @property
    def default_interval(self) -> int:
        """Get default interval in seconds (only for interval routines)."""
        if not self.is_interval:
            return 0
        field = self.config_class.model_fields["interval_sec"]
        return field.default if field.default is not None else 5

    def get_default_config(self) -> BaseModel:
        """Create config instance with default values."""
        return self.config_class()

    def get_fields(self) -> dict[str, dict]:
        """Get field metadata for UI display."""
        fields = {}
        for name, field_info in self.config_class.model_fields.items():
            annotation = field_info.annotation
            type_name = getattr(annotation, "__name__", str(annotation))
            fields[name] = {
                "type": type_name,
                "default": field_info.default,
                "description": field_info.description or name,
            }
        return fields


def discover_routines(force_reload: bool = False) -> dict[str, RoutineInfo]:
    """
    Discover all routines in the routines folder.

    Each routine module needs:
    - Config: Pydantic BaseModel with optional docstring description
    - run(config, context) -> str: Async function that executes the routine

    Args:
        force_reload: Force reimport of all modules

    Returns:
        Dict mapping routine name to RoutineInfo
    """
    global _routines_cache

    if _routines_cache is not None and not force_reload:
        return _routines_cache

    routines_dir = Path(__file__).parent
    routines = {}

    for file_path in routines_dir.glob("*.py"):
        if file_path.stem in ("__init__", "base"):
            continue

        try:
            module_name = f"routines.{file_path.stem}"

            if force_reload and module_name in importlib.sys.modules:
                importlib.reload(importlib.sys.modules[module_name])
            else:
                importlib.import_module(module_name)

            module = importlib.sys.modules[module_name]

            if not hasattr(module, "Config") or not hasattr(module, "run"):
                logger.warning(f"Routine {file_path.stem}: missing Config or run")
                continue

            routines[file_path.stem] = RoutineInfo(
                name=file_path.stem,
                config_class=module.Config,
                run_fn=module.run,
            )
            logger.debug(f"Discovered routine: {file_path.stem}")

        except Exception as e:
            logger.error(f"Failed to load routine {file_path.stem}: {e}")

    _routines_cache = routines
    return routines


def get_routine(name: str) -> RoutineInfo | None:
    """Get a specific routine by name."""
    return discover_routines().get(name)
