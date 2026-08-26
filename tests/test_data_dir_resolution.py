"""The operational store resolves the same way from anywhere (READ-215).

``notifications.json``, ``routine_hooks.json`` and ``backtests/`` used to be
built as ``Path("data")/…`` at import — relative to the *working directory*,
while the pickle beside them and ``code_runs/`` were already anchored at the
repo. Every in-tree launch path happens to start in the repo, so the split
never fired; it was a trap for the next launcher, and it made ``paths.py``'s
"the only place a runtime path is built" untrue.

What is pinned here is the promise that replaced it, and it is a promise about
*existing data*: an install that upgrades keeps reading the files it already
has, because ``data_dir()`` resolves to the same ``<repo>/data`` the old
constants resolved to for anyone whose cwd was the repo. Nothing is moved and
nothing is migrated — so the flip side is pinned too: a stray ``./data`` under
some *other* directory is not adopted, and ``$CONDOR_DATA_DIR`` is the way to
point at one.
"""

from pathlib import Path

import pytest

from condor import backtest_store, code_runs, notifications, paths, routine_hooks

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def unset_override(monkeypatch):
    """Production's view: neither root overridden.

    Only path *resolution* may be exercised under this — a test that wrote
    while it was active would land in the developer's live install, which is
    the failure this whole area exists to prevent.
    """
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)


def test_the_default_is_the_repos_data_directory(unset_override):
    """Where an existing install's files already are. Nothing to migrate."""
    assert paths.data_dir() == REPO / "data"
    assert paths.notifications_path() == REPO / "data" / "notifications.json"
    assert paths.routine_hooks_path() == REPO / "data" / "routine_hooks.json"
    assert paths.backtests_dir() == REPO / "data" / "backtests"
    assert paths.legacy_backtests_file() == REPO / "data" / "backtests.json"
    assert paths.code_runs_dir() == REPO / "data" / "code_runs"


def test_the_working_directory_does_not_move_the_store(unset_override, monkeypatch):
    """The defect itself: started from elsewhere, you used to get a fresh bell.

    The upgrade guarantee is this assertion read forwards — a bot restarted
    from any cwd finds the same ``notifications.json`` it wrote yesterday.
    """
    monkeypatch.chdir(REPO.parent)
    assert paths.data_dir() == REPO / "data"
    assert paths.notifications_path() == REPO / "data" / "notifications.json"


def test_the_env_override_wins_and_is_read_after_import(tmp_path, monkeypatch):
    """A constant would have frozen at import; these are functions on purpose."""
    elsewhere = tmp_path / "somewhere-else"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(elsewhere))

    assert paths.data_dir() == elsewhere
    assert paths.notifications_path() == elsewhere / "notifications.json"
    assert paths.routine_hooks_path() == elsewhere / "routine_hooks.json"
    assert paths.backtests_dir() == elsewhere / "backtests"
    assert paths.code_runs_dir() == elsewhere / "code_runs"


def test_the_override_expands_a_home_relative_path(monkeypatch):
    monkeypatch.setenv(paths.DATA_DIR_ENV, "~/condor-data")
    assert paths.data_dir() == Path.home() / "condor-data"


def test_the_two_roots_stay_apart(monkeypatch, tmp_path):
    """Repointing the runtime store must not drag the operational one with it."""
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "runtime"))
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "operational"))

    assert paths.runtime_root() == tmp_path / "runtime"
    assert paths.data_dir() == tmp_path / "operational"


# ── the writers, not a reconstruction of their paths ──


def test_every_store_follows_the_override(tmp_path, monkeypatch):
    """One knob moves all four. The whole point of the resolver."""
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "moved"))

    notifications._write_all({"7": []})
    routine_hooks._write_all({"r": {"": {"trigger": "success"}}})
    backtest_store.BacktestStore().save_result("srv", "task-1", {"x": 1})
    code_runs.CodeRunStore().save(code_runs.CodeRun(id="code_1", created=1.0))

    moved = tmp_path / "moved"
    assert (moved / "notifications.json").is_file()
    assert (moved / "routine_hooks.json").is_file()
    assert (moved / "backtests" / "task-1.json").is_file()
    assert (moved / "code_runs" / "code_1.json").is_file()


def test_an_existing_store_is_read_back_not_reset(tmp_path, monkeypatch):
    """The upgrade case: files already on disk are found, not replaced.

    ``tmp_path`` stands in for ``<repo>/data`` — same shape, same filenames, and
    the same resolver reaching them. What is asserted is that the new readers
    open what an older build wrote rather than starting empty beside it.
    """
    existing = tmp_path / "already-here"
    (existing / "backtests").mkdir(parents=True)
    (existing / "code_runs").mkdir(parents=True)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(existing))

    notifications._write_all({"7": [{"id": "n1"}]})
    routine_hooks._write_all({"r": {"": {"trigger": "always"}}})
    backtest_store.BacktestStore().save_result("srv", "old-task", {"x": 1})
    code_runs.CodeRunStore().save(code_runs.CodeRun(id="code_old", created=1.0))

    # A second boot: fresh store objects, no in-process cache to lean on.
    assert notifications._read_all() == {"7": [{"id": "n1"}]}
    assert routine_hooks._read_all()["r"][""]["trigger"] == "always"
    assert backtest_store.BacktestStore().get_result("old-task") is not None
    assert [r["id"] for r in code_runs.CodeRunStore().list()] == ["code_old"]


def test_a_stray_cwd_data_directory_is_not_adopted(tmp_path, monkeypatch):
    """Stated plainly because it is the one behaviour change (READ-215).

    An install started from outside the repo had been writing its bell and
    hooks to ``$PWD/data`` while its pickle and code runs went to the repo.
    Those files are neither migrated nor read as a fallback — a cwd-relative
    fallback is exactly what was removed. ``$CONDOR_DATA_DIR`` adopts them.
    """
    stray = tmp_path / "stray"
    (stray / "data").mkdir(parents=True)
    (stray / "data" / "notifications.json").write_text('{"7": [{"id": "old"}]}')
    monkeypatch.chdir(stray)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "configured"))

    assert paths.notifications_path() != stray / "data" / "notifications.json"
    assert notifications._read_all() == {}  # not silently picked up

    monkeypatch.setenv(paths.DATA_DIR_ENV, str(stray / "data"))
    assert notifications._read_all() == {"7": [{"id": "old"}]}  # opt in, explicitly
