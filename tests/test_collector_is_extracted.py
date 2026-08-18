"""The telemetry collector lives in its own repo (``condor-telemetry-server``).

Condor only *emits* telemetry; it must never pull the collector's database
driver into a bot install, and no Condor module may import the collector. These
guard the FEAT-024 extraction so the coupling cannot silently creep back and
Condor keeps running with the collector entirely absent.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_asyncpg_is_never_a_condor_dependency():
    """A bot that only emits telemetry has no use for a database driver."""
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert not any("asyncpg" in dep for dep in project.get("dependencies", []))
    for group, deps in project.get("optional-dependencies", {}).items():
        assert not any(
            "asyncpg" in dep for dep in deps
        ), f"asyncpg leaked into the {group!r} extra"


def test_no_condor_module_imports_the_collector():
    """The collector was extracted; nothing in the client tree may reach for it."""
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "condor").rglob("*.py")
        if "telemetry_server" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"collector coupling crept back into: {offenders}"
