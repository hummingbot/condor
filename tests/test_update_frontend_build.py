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
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import condor.web.app as web_app
from condor.web.app import _adopt_orphaned_bundle, create_app
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


# ── docker steps get the same treatment (H2, P3) ──


def test_a_docker_timeout_says_retrying_is_safe():
    """1800s exists so a hung pull cannot wedge the update; hitting it said nothing.

    Both compose steps are idempotent, so the honest answer is "run it again" —
    but the bare "Timed out after 1800s" left the operator guessing whether a
    half-rebuilt stack was safe to touch.
    """
    message = updater._explain_docker_failure(
        124, "Timed out after 1800s: docker compose pull", "docker compose pull"
    )
    assert "idempotent" in message
    assert "safe" in message
    # And the platforms where the ceiling is actually reachable.
    assert "macOS" in message and "WSL2" in message


def test_an_ordinary_docker_failure_is_passed_through_unchanged():
    assert (
        updater._explain_docker_failure(1, "no such image", "docker compose pull")
        == "no such image"
    )


# ── what a browser gets mid-swap ──


def test_a_missing_bundle_answers_503_rather_than_an_opaque_500():
    """The window the swap narrowed, and what is served inside it.

    ``index.html`` absent is a real state: a build is mid-swap, or one failed.
    Starlette raises inside ``FileResponse`` on a missing path, which reaches
    the browser as a 500 indistinguishable from a crashed backend — so a
    reader reloading during a build was told Condor was broken. 503 says the
    true thing, and says the API is fine.
    """
    dist = Path(web_app.__file__).resolve().parent.parent.parent / "frontend" / "dist"
    index = dist / "index.html"
    assets = dist / "assets"

    made_dist = not dist.is_dir()
    made_assets = not assets.is_dir()
    if made_dist:
        dist.mkdir(parents=True)
    if made_assets:
        # StaticFiles refuses to mount a directory that is not there.
        assets.mkdir(parents=True)
    moved = index.exists()
    aside = dist / "index.html.pytest-aside"
    if moved:
        index.rename(aside)
    try:
        with TestClient(create_app()) as client:
            res = client.get("/settings")
        assert res.status_code == 503, "a missing bundle is not a server error"
        assert "rebuilt" in res.text
        assert "API is unaffected" in res.text
        # And it must not be cached, or the browser keeps the outage after the
        # build finishes.
        assert "no-cache" in res.headers.get("cache-control", "")
    finally:
        if moved:
            aside.rename(index)
        if made_assets:
            assets.rmdir()
        if made_dist:
            dist.rmdir()


# ── and the other place that builds it ──


def test_make_build_frontend_swaps_too_rather_than_emptying_in_place():
    """`make run` and `make restart` stop Condor first; this target does not.

    It is public, and a Condor started any other way — ``run-fg``, a
    supervisor, a second checkout — is still serving out of ``dist`` while it
    runs. An in-place build would empty that directory under it, which is
    exactly C1, reintroduced by the path the updater does not take.
    """
    makefile = (Path(__file__).resolve().parent.parent / "Makefile").read_text()
    target = makefile.split("\nbuild-frontend:", 1)[1].split("\n\n", 1)[0]

    assert "--outDir dist.new" in target, "builds straight into the served dir"
    assert "rm -rf dist.new" in target, "an interrupted run left a stale scratch dir"
    assert target.index("mv dist dist.old") < target.index("mv dist.new dist")
