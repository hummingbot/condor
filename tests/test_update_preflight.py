"""What blocks an update, and what an update is actually measuring.

Two defects are pinned here. The first: ``/update`` used to refuse whenever
``git status --porcelain`` printed anything, which includes untracked files. The
hummingbot-api checkout is bind-mounted into its own container, so it re-dirties
itself by running — that guard blocked forever, by construction. The question is
not "is the tree clean", it is "would fast-forwarding clobber something", and
that is the intersection of the dirty set with the incoming diff.

The second: the hummingbot-api service has no ``build:`` key, so it runs a
published image and ``git pull`` never reaches the container. Its version is the
image digest, and when the digest cannot be resolved the answer is "unknown",
never "up to date".
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from condor.updates import components
from utils.updater import DirtyState


def _component(key=components.HUMMINGBOT_API, repo_dir="/tmp/repo"):
    return components.Component(
        key, "Hummingbot API", repo_dir, service="hummingbot-api"
    )


def _blocks(*, dirty: DirtyState, incoming: list[str], ahead: int = 0, is_repo=True):
    """Run the blocker policy against a synthetic working tree."""
    with patch.multiple(
        "utils.updater",
        is_git_repo=AsyncMock(return_value=is_repo),
        fetch=AsyncMock(return_value=(True, "")),
        get_current_branch=AsyncMock(return_value="main"),
        ahead_count=AsyncMock(return_value=ahead),
        dirty_state=AsyncMock(return_value=dirty),
        incoming_paths=AsyncMock(return_value=incoming),
    ):
        return asyncio.run(components.repo_blocks(_component()))


def test_the_blockers_are_computed_against_a_freshly_fetched_origin():
    """A stale origin/ makes the incoming set a lie, and the lie is silent."""
    fetch = AsyncMock(return_value=(True, ""))
    with patch.multiple(
        "utils.updater",
        is_git_repo=AsyncMock(return_value=True),
        fetch=fetch,
        get_current_branch=AsyncMock(return_value="main"),
        ahead_count=AsyncMock(return_value=0),
        dirty_state=AsyncMock(return_value=DirtyState()),
        incoming_paths=AsyncMock(return_value=[]),
    ):
        asyncio.run(components.repo_blocks(_component()))
    fetch.assert_awaited_once()


# ---------------------------------------------------------------------------
# The blocker set
# ---------------------------------------------------------------------------


def test_a_dirty_file_the_update_would_overwrite_blocks():
    blocks = _blocks(
        dirty=DirtyState(modified=("environment.yml",)),
        incoming=["environment.yml", "routers/bots.py"],
    )
    assert [b.code for b in blocks] == ["dirty-conflict"]
    assert blocks[0].paths == ["environment.yml"]
    assert set(blocks[0].resolutions) == {"discard", "stash", "cancel"}


def test_a_dirty_file_the_update_never_touches_does_not_block():
    """The whole defect: local work outside the incoming diff is not a conflict."""
    assert (
        _blocks(
            dirty=DirtyState(modified=("environment.yml",)),
            incoming=["routers/bots.py"],
        )
        == []
    )


def test_untracked_junk_never_blocks():
    """A .DS_Store is not in anybody's incoming diff, so it is not in the way."""
    assert (
        _blocks(
            dirty=DirtyState(untracked=("bots/archived/.DS_Store", "bots/data/x.db")),
            incoming=["routers/bots.py"],
        )
        == []
    )


def test_an_untracked_file_the_update_would_write_blocks():
    blocks = _blocks(
        dirty=DirtyState(untracked=("claude.md",)),
        incoming=["claude.md"],
    )
    assert [b.code for b in blocks] == ["untracked-conflict"]
    assert blocks[0].paths == ["claude.md"]


def test_both_kinds_of_conflict_are_reported_apart():
    blocks = _blocks(
        dirty=DirtyState(modified=("environment.yml",), untracked=("claude.md",)),
        incoming=["environment.yml", "claude.md"],
    )
    assert [b.code for b in blocks] == ["dirty-conflict", "untracked-conflict"]


def test_staged_changes_count_as_dirty():
    blocks = _blocks(
        dirty=DirtyState(staged=("config.py",)),
        incoming=["config.py"],
    )
    assert [b.code for b in blocks] == ["dirty-conflict"]


def test_local_commits_ahead_report_diverged_and_offer_no_button():
    """A merge commit produced from a Telegram button is nobody's intent."""
    blocks = _blocks(dirty=DirtyState(), incoming=["a.py"], ahead=3)
    assert [b.code for b in blocks] == ["diverged"]
    assert blocks[0].resolutions == ["cancel"]
    assert "3 commits" in blocks[0].message


def test_a_directory_that_is_not_a_checkout_says_so():
    blocks = _blocks(dirty=DirtyState(), incoming=[], is_repo=False)
    assert [b.code for b in blocks] == ["not-a-repo"]


def test_nothing_incoming_means_nothing_can_conflict():
    blocks = _blocks(
        dirty=DirtyState(modified=("a.py",), untracked=("b.py",)),
        incoming=[],
    )
    assert blocks == []


def test_the_real_hummingbot_api_tree_blocks_on_at_most_environment_yml():
    """This install, verbatim: runtime output everywhere, one deliberate pin."""
    dirty = DirtyState(
        modified=("environment.yml",),
        untracked=(
            "bots/archived/.DS_Store",
            "bots/archived/ema_trend_loop-20260806-213931/config.yml",
            "bots/data/hummingbot.sqlite",
            "bots/controllers/directional_trading/ema_trend_v1.py",
            "bots/controllers/directional_trading/rsi_adx_mean_reversion.py",
            "claude.md",
            "test/test_bot_runs_payload.py",
            "test/test_ticker_sources.py",
        ),
    )
    incoming = ["routers/bots.py", "services/accounts.py", "models/executors.py"]
    assert _blocks(dirty=dirty, incoming=incoming) == []

    # And when upstream *does* touch the pinned file, that one path blocks.
    blocks = _blocks(dirty=dirty, incoming=incoming + ["environment.yml"])
    assert [b.paths for b in blocks] == [["environment.yml"]]


# ---------------------------------------------------------------------------
# What version the API is actually on
# ---------------------------------------------------------------------------

LOCAL = "sha256:4ae0104dd3772dafd45177f071b1dcefcd78e606b5dfdadb0eae5bba28be28d0"
REMOTE = "sha256:62d70399bf8e80d491ee7e11edacd0b740a6bfc60a1025140e4f8e6b8f597e0f"


_DEFAULT_SERVICE = {"image": "hummingbot/hummingbot-api:latest"}


def _facet(*, service=_DEFAULT_SERVICE, local=LOCAL, remote=REMOTE):
    """``service=None`` stands for ``docker compose config`` having failed."""
    with patch.multiple(
        "utils.updater",
        compose_service=AsyncMock(return_value=service),
        local_image_digest=AsyncMock(return_value=local),
        registry_image_digest=AsyncMock(return_value=remote),
    ):
        return asyncio.run(components._image_facet("/tmp/repo", "hummingbot-api"))


def test_no_build_key_means_the_image_is_the_version():
    facet, mode = _facet(local=LOCAL, remote=LOCAL)
    assert mode == "image"
    assert facet.up_to_date is True
    assert facet.current == "sha256:4ae0104d"
    assert facet.available is None


def test_a_newer_published_image_is_reported_as_behind():
    facet, mode = _facet()
    assert mode == "image"
    assert facet.up_to_date is False
    assert (facet.current, facet.available) == ("sha256:4ae0104d", "sha256:62d70399")
    assert facet.behind == 1


def test_an_unreachable_registry_never_claims_up_to_date():
    facet, _ = _facet(remote=None)
    assert facet.up_to_date is False
    assert facet.error and "registry" in facet.error
    assert facet.available is None


def test_an_image_with_no_local_digest_is_unknown_not_behind():
    """Loaded from a tarball: there is nothing comparable to compare."""
    facet, _ = _facet(local=None)
    assert facet.up_to_date is False
    assert facet.current == "unknown"
    assert facet.error and "never been pulled" in facet.error


def test_a_build_key_means_source_mode_and_no_registry_lookup():
    facet, mode = _facet(service={"build": {"context": "."}, "image": "local/api"})
    assert mode == "source"
    assert facet.up_to_date is True


def test_docker_being_down_is_an_error_not_a_verdict():
    facet, mode = _facet(service=None)
    assert mode == "unknown"
    assert facet.up_to_date is False
    assert facet.error and "compose" in facet.error


@pytest.mark.parametrize("digest", ["", None, "not-a-digest"])
def test_short_digest_survives_junk(digest):
    assert components._short_digest(digest) in ("", "not-a-digest")
