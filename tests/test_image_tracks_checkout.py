"""The container does not run the checkout, so only offer what can match (H1).

hummingbot-api's CI publishes on merges to the default branch and nothing else,
tagging only ``latest`` and the version. There is no per-branch tag, so on any
other checkout the published image has nothing to do with the code on disk and
"pull the newer image" means "silently run the default branch's code".

The rule is therefore two-way: either this checkout is the one the tag is built
from, or the operator owns the image. These tests pin the four conditions that
make up "is", because the branch *name* alone gets each of them wrong.
"""

from __future__ import annotations

import subprocess

import pytest

from condor.updates import components
from utils import updater

CANONICAL = "https://github.com/hummingbot/hummingbot-api.git"


def _git(*args, cwd):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin",
        },
    )


@pytest.fixture
def checkout(tmp_path):
    """A clone of a local "canonical" origin, on its default branch, clean."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    for i in range(2):
        (origin / "f.txt").write_text(f"{i}\n")
        _git("add", "-A", cwd=origin)
        _git("commit", "-qm", f"c{i}", cwd=origin)

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True
    )
    # Present the clone as the canonical repository; origin/HEAD already points
    # at main from the clone.
    _git("remote", "set-url", "origin", CANONICAL, cwd=clone)
    return clone


# ── the canonical-remote condition ──


def test_a_fork_is_not_treated_as_the_canonical_repository():
    assert components._is_canonical_remote(CANONICAL) is True
    assert (
        components._is_canonical_remote("git@github.com:hummingbot/hummingbot-api.git")
        is True
    )
    assert (
        components._is_canonical_remote("https://github.com/someone/hummingbot-api")
        is False
    )
    assert components._is_canonical_remote("https://example.com/mirror") is False


@pytest.mark.asyncio
async def test_the_default_branch_of_the_canonical_repo_tracks_the_image(checkout):
    tracks, why = await components.image_tracks_this_checkout(str(checkout))
    assert tracks is True, why


@pytest.mark.asyncio
async def test_a_forks_main_does_not_track_the_image(checkout):
    _git(
        "remote",
        "set-url",
        "origin",
        "https://github.com/someone/hummingbot-api",
        cwd=checkout,
    )
    tracks, why = await components.image_tracks_this_checkout(str(checkout))
    assert tracks is False
    assert "not the repository the image is built from" in why


@pytest.mark.asyncio
async def test_a_feature_branch_does_not_track_the_image(checkout):
    _git("checkout", "-qb", "feat/add-https", cwd=checkout)
    tracks, why = await components.image_tracks_this_checkout(str(checkout))
    assert tracks is False
    assert "feat/add-https" in why and "main" in why


@pytest.mark.asyncio
async def test_a_detached_head_does_not_track_the_image(checkout):
    _git("checkout", "-q", "--detach", "HEAD~1", cwd=checkout)
    tracks, why = await components.image_tracks_this_checkout(str(checkout))
    assert tracks is False
    assert "detached" in why


@pytest.mark.asyncio
async def test_unpushed_commits_mean_the_code_is_not_the_default_branch(checkout):
    """The branch is `main`; the code is not. The name alone cannot see this."""
    (checkout / "mine.txt").write_text("local work\n")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-qm", "my own commit", cwd=checkout)

    tracks, why = await components.image_tracks_this_checkout(str(checkout))
    assert tracks is False
    assert "not on `main`" in why


# ── what the operator is shown ──


@pytest.mark.asyncio
async def test_an_untracked_checkout_is_not_offered_an_image_pull(
    monkeypatch, checkout
):
    """The offer disappears and the reason is stated -- not silently skipped."""
    _git("checkout", "-qb", "feat/mine", cwd=checkout)

    async def _service(*a, **k):
        return {"image": "hummingbot/hummingbot-api:latest"}

    monkeypatch.setattr(updater, "compose_service", _service)
    monkeypatch.setattr(
        updater, "local_image_digest", lambda *a, **k: _val("sha256:aaa")
    )
    monkeypatch.setattr(
        updater, "registry_image_digest", lambda *a, **k: _val("sha256:bbb")
    )
    monkeypatch.setattr(
        components,
        "_table",
        lambda: {
            components.HUMMINGBOT_API: components.Component(
                key=components.HUMMINGBOT_API,
                name="Hummingbot API",
                repo_dir=str(checkout),
                service="hummingbot-api",
            )
        },
    )

    status = await components.status(components.HUMMINGBOT_API)
    image = status.facets["image"]
    assert image.behind == 0
    assert image.up_to_date is True
    assert image.available is None
    detail = " ".join(image.detail)
    assert "you own this image" in detail.lower()
    assert "feat/mine" in detail

    steps = components._steps_for(components.HUMMINGBOT_API, status)
    assert not any("Pull the published" in s for s in steps)
    assert not any("Recreate the containers" in s for s in steps)


async def _val(v):
    return v
