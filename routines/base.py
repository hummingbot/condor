"""
Base classes and discovery for routines.

Routine Types:
- One-shot: Runs once and returns result. Can be scheduled externally.
- Continuous: Has CONTINUOUS = True. Contains internal loop (while True).
              Runs forever until cancelled. Handles its own timing.
"""

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from condor.memory.paths import CHAT_SLUG

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def assistant_routines_dir(agent_slug: str | None) -> Path:
    """Routines dir for an agent.

    There is a single home for the general library — the repo-root ``routines/``,
    owned by the chat ``condor``. Domain experts/trading agents are isolated: each
    owns its routines and does **not** see the general library.

    - chat ``condor`` (``agent_slug`` None **or** ``"condor"``) → ``routines``
    - trading agent / domain expert (slug) → ``agents/<slug>/routines`` (isolated)

    The explicit ``"condor"`` carve-out is load-bearing (FEAT-033): Condor is now
    an ordinary entry under ``agents/``, so threading its slug through naively
    would relocate the general library to ``agents/condor/routines`` and empty
    the catalog — silently, since a missing dir simply lists nothing.
    """
    if agent_slug and agent_slug != CHAT_SLUG:
        return _PROJECT_ROOT / "agents" / agent_slug / "routines"
    return _PROJECT_ROOT / "routines"


@dataclass
class RoutineResult:
    """Rich result from a routine execution.

    Routines can return this instead of a plain string to provide
    structured data for the web dashboard (tables, charts, sections).
    Telegram still uses the `text` field.
    """

    text: str
    table_data: list[dict] | None = None
    table_columns: list[str] | None = None
    chart_image: bytes | None = None
    sections: list[dict] | None = field(default=None)


def normalize_result(result) -> RoutineResult:
    """Wrap a plain string into RoutineResult for backwards compatibility."""
    if isinstance(result, RoutineResult):
        return result
    return RoutineResult(text=str(result) if result else "Completed")


_routines_cache: dict[str, "RoutineInfo"] | None = None

# {stem: mtime} of every file seen on the last scan of routines/ — including
# files that failed to load, so a broken routine isn't re-executed on every
# call but is retried as soon as its mtime changes.
_routines_mtimes: dict[str, float | None] = {}

# Per-directory caches for discover_routines_from_path, keyed by
# (resolved dir, agent_slug): (mtimes-as-scanned, loaded RoutineInfos).
_path_caches: dict[
    tuple[str, str | None],
    tuple[dict[str, float | None], dict[str, "RoutineInfo"]],
] = {}


def _safe_mtime(file_path: Path) -> float | None:
    """Return the file's modification time (epoch seconds), or None on failure."""
    try:
        return file_path.stat().st_mtime
    except OSError:
        return None


class RoutineInfo:
    """Metadata container for a discovered routine."""

    def __init__(
        self,
        name: str,
        config_class: type[BaseModel],
        run_fn: Callable[[BaseModel, Any], Awaitable[str]],
        is_continuous: bool = False,
        callback_handler: Callable | None = None,
        message_handler: Callable | None = None,
        message_states: list[str] | None = None,
        cleanup_fn: Callable | None = None,
        category: str = "Uncategorized",
        source: str = "global",
        last_modified: float | None = None,
    ):
        self.name = name
        self.config_class = config_class
        self.run_fn = run_fn
        self._is_continuous = is_continuous
        self.callback_handler = callback_handler
        self.message_handler = message_handler
        self.message_states = message_states or []
        self.cleanup_fn = cleanup_fn
        self.category = category
        self.source = source
        # File modification time (epoch seconds) of the routine's source file.
        self.last_modified = last_modified

        # Extract description from Config docstring
        doc = config_class.__doc__ or name
        self.description = doc.strip().split("\n")[0]

    @property
    def is_continuous(self) -> bool:
        """Check if this is a continuous routine (has CONTINUOUS = True in module)."""
        return self._is_continuous

    def get_default_config(self) -> BaseModel:
        """Create config instance with default values."""
        return self.config_class()

    def get_fields(self) -> dict[str, dict]:
        """Get field metadata for UI display."""
        fields = {}
        for name, field_info in self.config_class.model_fields.items():
            annotation = field_info.annotation
            type_name = getattr(annotation, "__name__", str(annotation))
            entry: dict = {
                "type": type_name,
                "default": field_info.default,
                "description": field_info.description or name,
            }
            # Pass through widget hints from json_schema_extra
            extra = field_info.json_schema_extra
            if isinstance(extra, dict):
                if "widget" in extra:
                    entry["widget"] = extra["widget"]
                if "options_from" in extra:
                    entry["options_from"] = extra["options_from"]
            fields[name] = entry
        return fields


def discover_routines(force_reload: bool = False) -> dict[str, RoutineInfo]:
    """
    Discover all routines in the routines folder.

    Each routine module needs:
    - Config: Pydantic BaseModel with optional docstring description
    - run(config, context) -> str: Async function that executes the routine
    - CONTINUOUS = True (optional): Mark as continuous routine with internal loop

    Discovery is cached per file mtime: unchanged modules are NOT re-imported,
    while new/edited files are (re)loaded and deleted files dropped on every
    call — so edits are still picked up without restarting the bot.

    Args:
        force_reload: Force reimport of all modules regardless of mtimes
                      (explicit hot-reload path, e.g. the file watcher)

    Returns:
        Dict mapping routine name to RoutineInfo
    """
    global _routines_cache

    fresh_start = force_reload or _routines_cache is None
    prev_routines = {} if fresh_start else _routines_cache
    prev_mtimes = {} if fresh_start else _routines_mtimes

    routines_dir = Path(__file__).parent
    routines = {}
    scanned_mtimes: dict[str, float | None] = {}

    for file_path in routines_dir.glob("*.py"):
        stem = file_path.stem
        if stem in ("__init__", "base"):
            continue

        mtime = _safe_mtime(file_path)
        scanned_mtimes[stem] = mtime
        if mtime is not None and stem in prev_mtimes and prev_mtimes[stem] == mtime:
            # Unchanged since last scan: reuse cached info (or cached failure).
            if stem in prev_routines:
                routines[stem] = prev_routines[stem]
            continue

        try:
            module_name = f"routines.{stem}"

            if module_name in importlib.sys.modules:
                importlib.reload(importlib.sys.modules[module_name])
            else:
                importlib.import_module(module_name)

            module = importlib.sys.modules[module_name]

            if not hasattr(module, "Config") or not hasattr(module, "run"):
                logger.warning(f"Routine {file_path.stem}: missing Config or run")
                continue

            # Check for CONTINUOUS flag
            is_continuous = getattr(module, "CONTINUOUS", False)
            category = getattr(module, "CATEGORY", "Uncategorized")

            # Detect optional handlers
            callback_handler = getattr(module, "handle_callback", None)
            message_handler = getattr(module, "handle_message", None)
            message_states = getattr(module, "MESSAGE_STATES", None)
            cleanup_fn = getattr(module, "cleanup", None)

            routines[file_path.stem] = RoutineInfo(
                name=file_path.stem,
                config_class=module.Config,
                run_fn=module.run,
                is_continuous=is_continuous,
                callback_handler=callback_handler,
                message_handler=message_handler,
                message_states=message_states,
                cleanup_fn=cleanup_fn,
                category=category,
                source="global",
                last_modified=_safe_mtime(file_path),
            )
            logger.debug(
                f"Discovered routine: {file_path.stem} (continuous={is_continuous})"
            )

        except Exception as e:
            logger.error(f"Failed to load routine {file_path.stem}: {e}")

    _routines_mtimes.clear()
    _routines_mtimes.update(scanned_mtimes)
    _routines_cache = routines
    return routines


def discover_routines_from_path(
    routines_dir: Path, agent_slug: str | None = None, force_reload: bool = False
) -> dict[str, RoutineInfo]:
    """Discover routines from an arbitrary directory path.

    Uses importlib.util for dynamic loading since agent-local routines
    aren't on the Python module path. Discovery is cached per directory and
    keyed by file mtimes: unchanged modules are NOT re-executed, while
    new/edited files are (re)loaded and deleted files dropped on every call.

    Args:
        routines_dir: Directory containing routine .py files.
        agent_slug: If provided, sets source to "agent:{slug}" and default category to agent name.
        force_reload: Force re-execution of all modules regardless of mtimes.

    Returns:
        Dict mapping routine name to RoutineInfo.
    """
    import importlib.util

    routines: dict[str, RoutineInfo] = {}

    if not routines_dir.exists():
        return routines

    cache_key = (str(routines_dir.resolve()), agent_slug)
    prev_mtimes, prev_routines = (
        ({}, {}) if force_reload else _path_caches.get(cache_key, ({}, {}))
    )
    scanned_mtimes: dict[str, float | None] = {}

    source = f"agent:{agent_slug}" if agent_slug else "global"
    default_category = (
        agent_slug.replace("_", " ").replace("-", " ").title()
        if agent_slug
        else "Uncategorized"
    )

    for file_path in routines_dir.glob("*.py"):
        stem = file_path.stem
        if stem.startswith("_"):
            continue

        mtime = _safe_mtime(file_path)
        scanned_mtimes[stem] = mtime
        if mtime is not None and stem in prev_mtimes and prev_mtimes[stem] == mtime:
            # Unchanged since last scan: reuse cached info (or cached failure).
            if stem in prev_routines:
                routines[stem] = prev_routines[stem]
            continue

        try:
            module_name = (
                f"agent_routine_{agent_slug}_{file_path.stem}"
                if agent_slug
                else f"agent_routine_{file_path.stem}"
            )
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "Config") or not hasattr(module, "run"):
                logger.warning(f"Agent routine {file_path.stem}: missing Config or run")
                continue

            is_continuous = getattr(module, "CONTINUOUS", False)
            category = getattr(module, "CATEGORY", default_category)
            callback_handler = getattr(module, "handle_callback", None)
            message_handler = getattr(module, "handle_message", None)
            message_states = getattr(module, "MESSAGE_STATES", None)
            cleanup_fn = getattr(module, "cleanup", None)

            routines[file_path.stem] = RoutineInfo(
                name=file_path.stem,
                config_class=module.Config,
                run_fn=module.run,
                is_continuous=is_continuous,
                callback_handler=callback_handler,
                message_handler=message_handler,
                message_states=message_states,
                cleanup_fn=cleanup_fn,
                category=category,
                source=source,
                last_modified=_safe_mtime(file_path),
            )
            logger.debug(f"Discovered agent routine: {file_path.stem}")

        except Exception as e:
            logger.error(f"Failed to load agent routine {file_path.stem}: {e}")

    _path_caches[cache_key] = (scanned_mtimes, routines)
    return routines


def get_routine(name: str) -> RoutineInfo | None:
    """Get a specific routine by name."""
    return discover_routines().get(name)


def get_routine_by_state(state: str) -> RoutineInfo | None:
    """Find a routine that handles the given message state."""
    for routine in discover_routines().values():
        if state in routine.message_states:
            return routine
    return None
