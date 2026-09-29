"""What blocks an update, and what an update is actually measuring.

Three defects are pinned here. The first: ``/update`` used to refuse whenever
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
from utils import updater
from utils.updater import DirtyState


def _component(key=components.HUMMINGBOT_API, repo_dir="/tmp/repo"):
    return components.Component(
        key, "Hummingbot API", repo_dir, service="hummingbot-api"
    )


def _blocks(
    *,
    dirty: DirtyState,
    incoming: list[str] | None,
    ahead: int = 0,
    is_repo=True,
    fetched=True,
):
    """Run the blocker policy against a synthetic working tree.

    ``incoming=None`` stands for a diff that did not resolve, ``fetched=False``
    for a remote that could not be reached -- neither is an empty diff.
    """
    with patch.multiple(
        "utils.updater",
        is_git_repo=AsyncMock(return_value=is_repo),
        is_detached=AsyncMock(return_value=False),
        fetch=AsyncMock(return_value=(fetched, "" if fetched else "no such remote")),
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
        is_detached=AsyncMock(return_value=False),
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


# ---------------------------------------------------------------------------
# An unanswered question is not an answer of "no"
# ---------------------------------------------------------------------------


def test_an_empty_diff_and_an_unresolvable_one_do_not_land_in_the_same_place():
    """The defect, pinned as a contrast: [] cleared the update, and so did failure.

    ``incoming_paths`` used to return ``[]`` for a diff that never ran, which
    ``repo_blocks`` read as "nothing is coming in" and therefore "nothing is in
    the way" — a failure that green-lit the very fast-forward it was asked to
    gate. Same dirty tree, two different states, two different verdicts.
    """
    dirty = DirtyState(modified=("environment.yml",))

    assert _blocks(dirty=dirty, incoming=[]) == []

    blocks = _blocks(dirty=dirty, incoming=None)
    assert [b.code for b in blocks] == ["incoming-unknown"]
    assert blocks[0].resolutions == ["cancel"]
    assert blocks[0].paths == []
    assert "origin/main" in blocks[0].message


def test_a_fetch_that_failed_blocks_rather_than_judging_from_a_stale_ref():
    """No fetch, no basis: the incoming set below it would be about yesterday."""
    blocks = _blocks(dirty=DirtyState(), incoming=[], fetched=False)
    assert [b.code for b in blocks] == ["incoming-unknown"]
    assert blocks[0].resolutions == ["cancel"]


def test_an_unresolvable_diff_blocks_even_on_a_pristine_tree():
    """Nothing dirty to clobber today, but nobody checked what lands tomorrow."""
    blocks = _blocks(dirty=DirtyState(), incoming=None)
    assert [b.code for b in blocks] == ["incoming-unknown"]


def test_incoming_paths_returns_none_when_the_diff_fails():
    """The primitive's half of the contract: rc != 0 is None, never []."""
    with patch.object(
        updater, "_run_git", AsyncMock(return_value=(128, ""))
    ) as run_git:
        assert asyncio.run(updater.incoming_paths("/tmp/repo", "main")) is None
    assert run_git.await_args.args == ("diff", "--name-only", "HEAD..origin/main")


def test_incoming_paths_returns_an_empty_list_when_the_diff_is_empty():
    with patch.object(updater, "_run_git", AsyncMock(return_value=(0, "\n"))):
        assert asyncio.run(updater.incoming_paths("/tmp/repo", "main")) == []


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


IMAGE_ID = "sha256:943773e318e028d8a303e7c2237586fbd0d96e3ba46b902b215dc3799264bb54"


def _facet(*, service=_DEFAULT_SERVICE, local=LOCAL, remote=REMOTE, image_id=IMAGE_ID):
    """``service=None`` stands for ``docker compose config`` having failed.

    ``image_id=None`` is "no such image here"; ``local=None`` with an id is an
    image that is present but carries no registry digest — a local build under
    the classic image store.
    """
    with patch.multiple(
        "utils.updater",
        compose_service=AsyncMock(return_value=service),
        local_image_identity=AsyncMock(return_value=(image_id, local)),
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


def test_an_image_that_is_not_here_at_all_is_unknown_not_behind():
    """Nothing to compare, and nothing to run either."""
    facet, _ = _facet(local=None, image_id=None)
    assert facet.up_to_date is False
    assert facet.current == "unknown"
    assert facet.error_code == "image-absent"
    assert facet.error and "never been pulled or built" in facet.error


def test_an_image_present_without_a_registry_digest_is_the_operators_own():
    """`make build` under the classic image store leaves `RepoDigests` empty.

    That store is still the default on Docker Engine, so this is the ordinary
    shape of an operator running their own build — not an error, and above all
    not a reason to block the whole update. It used to raise
    ``registry-unreachable`` while the registry was answering fine.
    """
    facet, _ = _facet(local=None)
    assert facet.error is None, "a local build is not a failure"
    assert facet.up_to_date is True
    assert facet.behind == 0
    assert facet.current == "sha256:943773e3"
    assert facet.detail and "Built here" in facet.detail[0]


def test_a_hand_added_build_key_no_longer_means_a_second_mode():
    """ "source" was a mode the product could not enter, so it is gone.

    No shipped compose file has a ``build:`` key and hummingbot-api is deployed
    from the published image, so the branch was dead code. An operator who adds
    one is running their own image, which the checkout gate and the
    locally-built check already cover -- by leaving it alone, not by rebuilding
    it on their behalf.
    """
    _, mode = _facet(service={"build": {"context": "."}, "image": "local/api"})
    assert mode == "image"


def test_docker_being_down_is_an_error_not_a_verdict():
    facet, mode = _facet(service=None)
    assert mode == "unknown"
    assert facet.up_to_date is False
    assert facet.error and "compose" in facet.error


@pytest.mark.parametrize("digest", ["", None, "not-a-digest"])
def test_short_digest_survives_junk(digest):
    assert components._short_digest(digest) in ("", "not-a-digest")


# ── V12: an update must not adopt another checkout's stack ──


def _owner_probe(monkeypatch, label, repo_dir):
    """Stub `docker inspect` returning the compose working-dir label."""
    from utils import updater as u

    async def fake(*args, **kwargs):
        return (0, label) if label is not None else (1, "")

    monkeypatch.setattr(u, "_run_cmd", fake)
    return asyncio.run(u.compose_stack_owner(repo_dir, "hummingbot-api"))


def test_containers_from_another_checkout_are_reported(monkeypatch):
    """`container_name:` is fixed, so Compose matches by name across projects.

    It does not treat that as an error — it prints `Recreate` and the second
    checkout takes over the first's containers and its postgres volume.
    Condor points at a directory; Compose acts on a project.
    """
    owner = _owner_probe(monkeypatch, "/srv/other-checkout", "/srv/mine")
    assert owner == "/srv/other-checkout"


def test_our_own_containers_are_not_a_conflict(monkeypatch):
    assert _owner_probe(monkeypatch, "/srv/mine", "/srv/mine") is None


def test_a_trailing_slash_is_not_a_different_checkout(monkeypatch):
    assert _owner_probe(monkeypatch, "/srv/mine/", "/srv/mine") is None


def test_nothing_running_is_not_a_conflict(monkeypatch):
    assert _owner_probe(monkeypatch, None, "/srv/mine") is None


def test_an_unlabelled_container_is_not_a_conflict(monkeypatch):
    """Docker prints `<no value>` for a label that is not set."""
    assert _owner_probe(monkeypatch, "<no value>", "/srv/mine") is None


# ── V13: a daemon that is down is not a missing image ──


def _facet_with_docker(*, daemon_up, identity=(None, None)):
    """`_image_facet` with the daemon's liveness and the inspect result pinned."""
    with patch.multiple(
        "utils.updater",
        compose_service=AsyncMock(return_value=_DEFAULT_SERVICE),
        local_image_identity=AsyncMock(return_value=identity),
        registry_image_digest=AsyncMock(return_value=REMOTE),
        docker_running=AsyncMock(return_value=daemon_up),
    ):
        return asyncio.run(components._image_facet("/tmp/repo", "hummingbot-api"))


def test_a_stopped_daemon_is_not_reported_as_a_missing_image():
    """Reported from a live dashboard while Docker Desktop was restarting.

    `docker image inspect` is the only probe here that needs the daemon —
    `compose config` merges files locally and `imagetools inspect` asks the
    registry — so a daemon that is down reaches the `local is None` branch
    with everything else looking healthy. It read "it has never been pulled by
    tag", about an image that was sitting on disk the whole time.
    """
    facet, _ = _facet_with_docker(daemon_up=False)
    assert facet.error_code == "docker-unavailable"
    assert "Docker is not answering" in facet.error
    assert "never been pulled" not in facet.error
    # The registry answered even with the daemon down, so keep what we learned.
    assert facet.available == "sha256:62d70399"


def test_a_running_daemon_with_no_such_image_still_says_so():
    """The fix must not swallow the case it was carved out of."""
    facet, _ = _facet_with_docker(daemon_up=True)
    assert facet.error_code == "image-absent"
    assert "never been pulled or built" in facet.error


def test_the_daemon_is_not_probed_when_the_image_is_there():
    """One extra command, and only on the error path."""

    def boom(*a, **kw):  # pragma: no cover - reached only on regression
        raise AssertionError("docker_running must not run on the happy path")

    with patch.multiple(
        "utils.updater",
        compose_service=AsyncMock(return_value=_DEFAULT_SERVICE),
        local_image_identity=AsyncMock(return_value=(IMAGE_ID, LOCAL)),
        registry_image_digest=AsyncMock(return_value=LOCAL),
        docker_running=boom,
    ):
        facet, _ = asyncio.run(components._image_facet("/tmp/repo", "hummingbot-api"))
    assert facet.up_to_date is True and facet.error is None


def test_a_symlinked_checkout_is_not_a_foreign_stack(monkeypatch, tmp_path):
    """Compose records the *resolved* directory; `normpath` does not resolve.

    A checkout reached through a symlink — macOS `/tmp` is one, and symlinked
    project roots are common — compared unequal to itself, so the guard
    refused the operator's own update naming the same directory back at them.
    """
    from utils import updater as u

    real = tmp_path / "checkout"
    real.mkdir()
    link = tmp_path / "via-symlink"
    link.symlink_to(real)

    async def fake(*args, **kwargs):
        return (0, str(real))

    monkeypatch.setattr(u, "_run_cmd", fake)
    assert asyncio.run(u.compose_stack_owner(str(link), "hummingbot-api")) is None
