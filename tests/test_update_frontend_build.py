"""The dashboard bundle is not destroyed to build its replacement (C1, C13).

vite defaults to ``outDir: "dist"`` with ``emptyOutDir: true``, and
``vite.config.ts`` overrides neither — so the build emptied the directory
uvicorn was serving out of, for the whole length of the build. A reload in that
window returned 500 (a missing ``index.html`` raises inside ``FileResponse``,
which nothing upstream can tell from a crash), and a build that *failed* left
the install with no dashboard at all while the run said the previous bundle
would come back.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from condor.web.app import _adopt_orphaned_bundle
from utils import updater

# ── the build script ──


def _script() -> str:
    """The script build_frontend hands to bash, with npm stubbed out."""
    captured = {}

    async def _fake_run(*args, **kwargs):
        captured["script"] = args[2]
        return 0, ""

    with (
        patch.object(updater, "_run_cmd", _fake_run),
        patch.object(updater, "npm_deps_stale", AsyncMock(return_value=False)),
        patch.object(updater.os.path, "isdir", lambda p: True),
    ):
        asyncio.run(updater.build_frontend("a", "b"))
    return captured["script"]


def test_the_build_never_writes_into_the_directory_being_served():
    script = _script()
    assert "--outDir dist.new" in script
    # The bare form is what emptied dist in place.
    assert "npm run build\n" not in script
    assert script.count("npm run build") == 1


def test_the_swap_keeps_the_previous_bundle_as_a_rollback():
    script = _script()
    # Order matters: the old bundle is only moved aside once the build has won.
    assert script.index("npm run build") < script.index("mv dist dist.old")
    assert script.index("mv dist dist.old") < script.index("mv dist.new dist")


def test_a_stale_scratch_dir_is_cleared_before_building():
    """A dist.new left by an interrupted run must not be built on top of."""
    assert "rm -rf dist.new" in _script()


# ── failure messages that name the actual failure ──


@pytest.mark.parametrize(
    "rc, expected",
    [
        (137, "memory limit"),
        (-9, "memory limit"),
        (124, "timed out"),
        (90, "npm ci` failed"),
    ],
)
def test_the_common_build_failures_are_named(rc, expected):
    message = updater._explain_build_failure(rc, "some output")
    assert expected in message
    # And each says the bundle survived, which is the thing the operator
    # actually needs to know.
    assert "not touched" in message


def test_an_unremarkable_failure_is_passed_through_unchanged():
    assert updater._explain_build_failure(1, "tsc error TS2304") == "tsc error TS2304"


# ── boot-time recovery from an interrupted swap ──


def test_an_interrupted_swap_is_adopted_at_boot(tmp_path):
    (tmp_path / "dist.old").mkdir()
    (tmp_path / "dist.old" / "index.html").write_text("old")
    (tmp_path / "dist.new").mkdir()
    (tmp_path / "dist.new" / "index.html").write_text("new")

    _adopt_orphaned_bundle(tmp_path / "dist")

    # dist.new won the build; it is the newer of the two.
    assert (tmp_path / "dist" / "index.html").read_text() == "new"


def test_only_a_rollback_bundle_is_still_adopted(tmp_path):
    (tmp_path / "dist.old").mkdir()
    (tmp_path / "dist.old" / "index.html").write_text("old")

    _adopt_orphaned_bundle(tmp_path / "dist")

    assert (tmp_path / "dist" / "index.html").read_text() == "old"


def test_adoption_does_nothing_when_a_bundle_is_already_in_place(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("live")
    (tmp_path / "dist.old").mkdir()
    (tmp_path / "dist.old" / "index.html").write_text("old")

    _adopt_orphaned_bundle(tmp_path / "dist")

    assert (tmp_path / "dist" / "index.html").read_text() == "live"


def test_adoption_is_a_no_op_with_nothing_to_adopt(tmp_path):
    _adopt_orphaned_bundle(tmp_path / "dist")
    assert not (tmp_path / "dist").exists()
