"""Preferences have exactly one import path: ``condor.preferences``.

``handlers/config/user_preferences.py`` used to be a two-line shim whose whole
body was ``from condor.preferences import *``. It split the codebase across two
names for the same module, re-exported 110 public names (incidental imports
like ``logging`` and ``Optional`` among them), and CLAUDE.md pointed readers at
the shim -- so the project's own authoritative doc opened a file with no code in
it. READ-256 rewired every call site to the real module and deleted the shim.

These guard that closure: the shim cannot come back, nothing may reach for it,
and every name a call site imports from ``condor.preferences`` must actually be
defined there -- the check a star import structurally cannot fail.
"""

from __future__ import annotations

import ast
from pathlib import Path

import condor.preferences as preferences

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", "worktrees", "__pycache__"}


def _source_files():
    """Every Python file in the working tree, excluding vendored/nested trees."""
    for path in REPO.rglob("*.py"):
        if SKIP_DIRS.isdisjoint(path.relative_to(REPO).parts):
            yield path


def test_the_preferences_shim_stays_deleted():
    """A re-export module with no code in it is not a home for preferences."""
    assert not (REPO / "handlers" / "config" / "user_preferences.py").exists()


def test_nothing_imports_preferences_through_the_shim():
    """One module, one import path -- no second name for the same code."""
    offenders = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "user_preferences"
            ):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
            elif isinstance(node, ast.Import) and any(
                alias.name.endswith("user_preferences") for alias in node.names
            ):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    assert offenders == [], f"the preferences shim is referenced from: {offenders}"


def test_preferences_is_never_star_imported():
    """Explicit named imports are the house style (handlers/bots/_shared.py)."""
    offenders = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    assert offenders == [], f"star imports hide what is in scope in: {offenders}"


def test_every_imported_preference_name_actually_exists():
    """A star re-export could not catch a typo here; a named import can."""
    missing = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "condor.preferences"
                and node.level == 0
            ):
                for alias in node.names:
                    if not hasattr(preferences, alias.name):
                        missing.append(
                            f"{path.relative_to(REPO).as_posix()}:{node.lineno} "
                            f"imports missing name {alias.name!r}"
                        )
    assert missing == [], f"unresolvable preference imports: {missing}"
