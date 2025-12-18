"""Base classes and discovery for routines."""

import importlib
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Cache for discovered routines
_routines_cache: dict[str, dict] | None = None


class RoutineInfo:
    """Container for routine metadata."""

    def __init__(self, name: str, config_class: type[BaseModel], run_fn: Callable[[BaseModel, Any], Awaitable[str]]):
        self.name = name
        self.config_class = config_class
        self.run_fn = run_fn
        # Use Config docstring as description
        self.description = (config_class.__doc__ or name).strip().split('\n')[0]

    def get_default_config(self) -> BaseModel:
        """Create config with default values."""
        return self.config_class()

    def get_fields(self) -> dict[str, dict]:
        """Get field info with names, types, defaults, and descriptions."""
        fields = {}
        for name, field_info in self.config_class.model_fields.items():
            fields[name] = {
                "type": field_info.annotation.__name__ if hasattr(field_info.annotation, '__name__') else str(field_info.annotation),
                "default": field_info.default,
                "description": field_info.description or name,
            }
        return fields


def discover_routines(force_reload: bool = False) -> dict[str, RoutineInfo]:
    """
    Discover all routines in the routines folder.

    Each routine needs:
    - Config: Pydantic BaseModel (docstring = description)
    - run(config, context) -> str: Async function

    Returns dict mapping routine name to RoutineInfo.
    """
    global _routines_cache

    if _routines_cache is not None and not force_reload:
        return _routines_cache

    routines_dir = Path(__file__).parent
    routines = {}

    for file_path in routines_dir.glob("*.py"):
        # Skip __init__ and base
        if file_path.stem in ("__init__", "base"):
            continue

        try:
            module_name = f"routines.{file_path.stem}"

            # Force reload if requested
            if force_reload and module_name in importlib.sys.modules:
                importlib.reload(importlib.sys.modules[module_name])
            else:
                importlib.import_module(module_name)

            module = importlib.sys.modules[module_name]

            # Validate required attributes (Config and run)
            if not all(hasattr(module, attr) for attr in ("Config", "run")):
                logger.warning(f"Routine {file_path.stem} missing required attributes (Config, run)")
                continue

            routines[file_path.stem] = RoutineInfo(
                name=file_path.stem,
                config_class=module.Config,
                run_fn=module.run,
            )
            logger.info(f"Discovered routine: {file_path.stem}")

        except Exception as e:
            logger.error(f"Failed to load routine {file_path.stem}: {e}")

    _routines_cache = routines
    return routines


def get_routine(name: str) -> RoutineInfo | None:
    """Get a specific routine by name."""
    routines = discover_routines()
    return routines.get(name)
