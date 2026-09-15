"""PERF-572: the MCP chat seat rides the mtime cache instead of forcing a reload.

``_own_plus_shared`` and ``list_routines``' chat branch used to pass
``force_reload=True``, so every ``manage_routines`` call — ``list``, ``run``,
``describe`` — re-imported every module in ``routines/`` and re-executed every
file in the shared roots. The mtime cache already gives the chat what the flag
was there for: an edited file is re-imported, a new one is loaded and a deleted
one is dropped on the next call, with no MCP subprocess restart.
"""

import pytest

import condor.memory.paths as paths_mod
import routines.base as base
from condor.memory.paths import CHAT_SLUG

ROUTINE_TEMPLATE = '''
from pydantic import BaseModel

with open({sentinel!r}, "a") as f:
    f.write("exec\\n")


class Config(BaseModel):
    """{desc}"""
    value: int = 1


async def run(config, context):
    return "ok"
'''


def _write(dir_path, name, sentinel, desc="a routine"):
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{name}.py"
    path.write_text(ROUTINE_TEMPLATE.format(sentinel=str(sentinel), desc=desc))
    return path


def _bump_mtime(path):
    import os

    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))


def _execs(sentinel):
    return sentinel.read_text().count("exec") if sentinel.exists() else 0


@pytest.fixture
def chat_seat(tmp_path, monkeypatch):
    """The MCP chat seat, with the shared root redirected under ``tmp_path``.

    The chat's *own* library stays the repo's real ``routines/`` — its modules
    are imported as the ``routines`` package and cannot be relocated — so the
    per-file assertions below are made on a shared routine, which
    ``discover_routines`` merges into the very same general library.
    """
    from mcp_servers.condor.tools import routines as mcp_routines

    monkeypatch.setattr(base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(base, "_routines_cache", None)
    monkeypatch.setattr(base, "_routines_mtimes", {})
    monkeypatch.setattr(base, "_path_caches", {})
    monkeypatch.setattr(mcp_routines.settings, "agent_slug", CHAT_SLUG)
    return mcp_routines


def _names(listed):
    return {r["name"] for r in listed["routines"]}


class TestWarmCallsDoNotReimport:
    def test_second_list_reimports_nothing(self, chat_seat, tmp_path, monkeypatch):
        sentinel = tmp_path / "execs.txt"
        _write(paths_mod.shared_routines_root(), "published", sentinel)

        chat_seat.list_routines()  # warm
        warm = _execs(sentinel)
        assert warm >= 1

        calls = []
        monkeypatch.setattr(
            base.importlib, "reload", lambda m: calls.append(m.__name__)
        )
        listed = chat_seat.list_routines()

        # Zero re-imports of the chat's own library, zero re-executions of the
        # shared one — and the catalog is unchanged.
        assert calls == []
        assert _execs(sentinel) == warm
        assert "published" in _names(listed)

    def test_resolving_a_routine_to_run_reimports_nothing(
        self, chat_seat, tmp_path, monkeypatch
    ):
        """``run``/``describe`` funnel through ``_resolve_routine`` too."""
        sentinel = tmp_path / "execs.txt"
        _write(paths_mod.shared_routines_root(), "published", sentinel)

        assert chat_seat._resolve_routine("published") is not None  # warm
        warm = _execs(sentinel)

        calls = []
        monkeypatch.setattr(
            base.importlib, "reload", lambda m: calls.append(m.__name__)
        )
        assert chat_seat._resolve_routine("published") is not None

        assert calls == []
        assert _execs(sentinel) == warm


class TestEditsAreStillVisibleWithoutARestart:
    def test_edited_file_is_reflected_on_the_next_call(self, chat_seat, tmp_path):
        sentinel = tmp_path / "execs.txt"
        path = _write(
            paths_mod.shared_routines_root(), "published", sentinel, desc="before"
        )

        chat_seat.list_routines()
        assert chat_seat.describe_routine("published")["description"] == "before"

        path.write_text(ROUTINE_TEMPLATE.format(sentinel=str(sentinel), desc="after"))
        _bump_mtime(path)

        assert chat_seat.describe_routine("published")["description"] == "after"
        listed = {r["name"]: r for r in chat_seat.list_routines()["routines"]}
        assert listed["published"]["description"] == "after"

    def test_new_file_is_reflected_on_the_next_call(self, chat_seat, tmp_path):
        sentinel = tmp_path / "execs.txt"
        _write(paths_mod.shared_routines_root(), "first", sentinel)

        assert "second" not in _names(chat_seat.list_routines())

        _write(paths_mod.shared_routines_root(), "second", sentinel)

        assert "second" in _names(chat_seat.list_routines())
        assert chat_seat._resolve_routine("second") is not None

    def test_deleted_file_is_dropped_on_the_next_call(self, chat_seat, tmp_path):
        sentinel = tmp_path / "execs.txt"
        path = _write(paths_mod.shared_routines_root(), "published", sentinel)

        assert "published" in _names(chat_seat.list_routines())

        path.unlink()

        assert "published" not in _names(chat_seat.list_routines())
        assert chat_seat._resolve_routine("published") is None
