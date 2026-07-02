"""Tests for PERF-047: routine discovery is mtime-cached, not reloaded per call.

``discover_routines`` / ``discover_routines_from_path`` used to re-import or
re-exec every routine module on every call (every GET /routines, every MCP
list, every agent-routine execution). They now keep an mtime-keyed cache and
only (re)load files that are new or changed, while still picking up edits
and deletions on the next call — no restart needed.
"""

import os
from pathlib import Path

import routines.base as base
from routines.base import discover_routines, discover_routines_from_path

ROUTINE_TEMPLATE = '''
from pydantic import BaseModel

with open({sentinel!r}, "a") as f:
    f.write("exec\\n")


class Config(BaseModel):
    """Test routine {name}"""
    value: int = 1


async def run(config, context):
    return "ok"
'''


def _write_routine(dir_path: Path, name: str, sentinel: Path) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{name}.py"
    file_path.write_text(ROUTINE_TEMPLATE.format(sentinel=str(sentinel), name=name))
    return file_path


def _exec_count(sentinel: Path) -> int:
    if not sentinel.exists():
        return 0
    return len(sentinel.read_text().splitlines())


def _bump_mtime(file_path: Path) -> None:
    """Force a strictly newer mtime without relying on clock resolution."""
    st = file_path.stat()
    os.utime(file_path, (st.st_atime, st.st_mtime + 10))


class TestDiscoverRoutinesFromPath:
    def test_unchanged_files_are_not_reexecuted(self, tmp_path):
        sentinel = tmp_path / "execs.txt"
        _write_routine(tmp_path / "r", "alpha", sentinel)

        first = discover_routines_from_path(tmp_path / "r")
        second = discover_routines_from_path(tmp_path / "r")

        assert "alpha" in first and "alpha" in second
        assert _exec_count(sentinel) == 1
        assert second["alpha"] is first["alpha"]

    def test_edited_file_is_reloaded(self, tmp_path):
        sentinel = tmp_path / "execs.txt"
        routines_dir = tmp_path / "r"
        file_path = _write_routine(routines_dir, "alpha", sentinel)
        discover_routines_from_path(routines_dir)

        _bump_mtime(file_path)
        result = discover_routines_from_path(routines_dir)

        assert "alpha" in result
        assert _exec_count(sentinel) == 2

    def test_new_file_loads_without_reexecuting_siblings(self, tmp_path):
        sentinel = tmp_path / "execs.txt"
        routines_dir = tmp_path / "r"
        _write_routine(routines_dir, "alpha", sentinel)
        discover_routines_from_path(routines_dir)

        new_sentinel = tmp_path / "execs_new.txt"
        _write_routine(routines_dir, "beta", new_sentinel)
        result = discover_routines_from_path(routines_dir)

        assert set(result) == {"alpha", "beta"}
        assert _exec_count(sentinel) == 1  # sibling untouched
        assert _exec_count(new_sentinel) == 1

    def test_deleted_file_is_dropped(self, tmp_path):
        sentinel = tmp_path / "execs.txt"
        routines_dir = tmp_path / "r"
        file_path = _write_routine(routines_dir, "alpha", sentinel)
        assert "alpha" in discover_routines_from_path(routines_dir)

        file_path.unlink()
        assert "alpha" not in discover_routines_from_path(routines_dir)

    def test_force_reload_reexecutes(self, tmp_path):
        sentinel = tmp_path / "execs.txt"
        routines_dir = tmp_path / "r"
        _write_routine(routines_dir, "alpha", sentinel)
        discover_routines_from_path(routines_dir)

        discover_routines_from_path(routines_dir, force_reload=True)
        assert _exec_count(sentinel) == 2

    def test_broken_routine_not_retried_until_edited(self, tmp_path):
        routines_dir = tmp_path / "r"
        routines_dir.mkdir()
        file_path = routines_dir / "broken.py"
        file_path.write_text("raise RuntimeError('boom')\n")

        assert "broken" not in discover_routines_from_path(routines_dir)
        assert "broken" not in discover_routines_from_path(routines_dir)

        # Fixing the file (mtime change) picks it up again
        sentinel = tmp_path / "execs.txt"
        _write_routine(routines_dir, "broken", sentinel)
        _bump_mtime(file_path)
        assert "broken" in discover_routines_from_path(routines_dir)

    def test_cache_is_per_agent_slug(self, tmp_path):
        sentinel = tmp_path / "execs.txt"
        routines_dir = tmp_path / "r"
        _write_routine(routines_dir, "alpha", sentinel)

        plain = discover_routines_from_path(routines_dir)
        scoped = discover_routines_from_path(routines_dir, agent_slug="myagent")

        assert plain["alpha"].source == "global"
        assert scoped["alpha"].source == "agent:myagent"


class TestDiscoverRoutines:
    def test_warm_cache_does_not_reimport(self, monkeypatch):
        discover_routines()  # warm

        calls = []
        monkeypatch.setattr(
            base.importlib,
            "reload",
            lambda m: calls.append(m.__name__),
        )
        result = discover_routines()

        assert calls == []
        assert isinstance(result, dict)

    def test_force_reload_still_reimports_all(self, monkeypatch):
        warm = discover_routines()  # warm
        if not warm:
            return  # nothing in routines/ to assert on

        calls = []
        real_reload = base.importlib.reload
        monkeypatch.setattr(
            base.importlib,
            "reload",
            lambda m: (calls.append(m.__name__), real_reload(m))[1],
        )
        discover_routines(force_reload=True)

        assert len(calls) == len(warm)


class TestRoutineStoreResolve:
    def test_resolve_agent_routine_does_not_reexec_siblings(
        self, tmp_path, monkeypatch
    ):
        from condor.routine_store import RoutineStore

        agents_dir = tmp_path / "agents"
        routines_dir = agents_dir / "myagent" / "routines"
        s1 = tmp_path / "e1.txt"
        s2 = tmp_path / "e2.txt"
        _write_routine(routines_dir, "alpha", s1)
        _write_routine(routines_dir, "beta", s2)

        store = RoutineStore()
        # Point _resolve_routine's agents dir (derived from __file__) at the tmp tree
        import condor.routine_store as rs_module

        monkeypatch.setattr(
            rs_module, "__file__", str(tmp_path / "condor" / "routine_store.py")
        )

        assert store._resolve_routine("myagent/alpha") is not None
        assert store._resolve_routine("myagent/alpha") is not None
        assert store._resolve_routine("myagent/beta") is not None

        assert _exec_count(s1) == 1
        assert _exec_count(s2) == 1
