"""The runtime root: one knob, honoured late, and one guard that refuses.

These pin the two properties everything else in FEAT-051 leans on -- the env
override is read at call time (so the suite can isolate itself and a subprocess
can inherit it), and an id that could escape its directory is refused rather
than sanitized.
"""

import pytest

from condor import paths


def test_root_defaults_to_a_dot_condor_beside_the_code(monkeypatch):
    monkeypatch.delenv(paths.RUNTIME_ROOT_ENV, raising=False)

    root = paths.runtime_root()

    assert root.name == ".condor"
    assert root.parent == paths._PROJECT_ROOT
    # The whole point: not inside the Python package any more.
    assert "condor/.runtime" not in str(root)


def test_env_override_is_observable_after_import(tmp_path, monkeypatch):
    """Read on every call, never captured in a module constant at import time."""
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "elsewhere"))

    assert paths.runtime_root() == tmp_path / "elsewhere"

    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "moved"))

    assert paths.runtime_root() == tmp_path / "moved"


def test_every_store_hangs_off_the_one_root(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path))

    assert paths.conversation_dir(42, "abc") == (
        tmp_path / "users" / "42" / "conversations" / "abc"
    )
    assert paths.delegation_dir(42, "condor-delegate-1") == (
        tmp_path / "users" / "42" / "delegations" / "condor-delegate-1"
    )
    assert paths.state_dir("ns") == tmp_path / "state" / "ns"
    assert paths.telemetry_dir() == tmp_path / "telemetry"


def test_the_user_is_the_first_segment_of_both_stores(tmp_path, monkeypatch):
    """A person's whole footprint is one directory -- that is the feature."""
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path))

    home = paths.user_dir(7)

    assert paths.conversation_dir(7, "c").is_relative_to(home)
    assert paths.delegation_dir(7, "t").is_relative_to(home)


@pytest.mark.parametrize("value", ["", "..", "../etc", "a/b", "a..b", "x\0y", " "])
def test_safe_id_refuses_anything_path_ish(value):
    with pytest.raises(paths.UnsafeIdError):
        paths.safe_id(value)


@pytest.mark.parametrize("value", ["42", "abc-123", "condor.delegate_1", 7])
def test_safe_id_passes_plain_identifiers_through(value):
    assert paths.safe_id(value) == str(value)


def test_a_bad_id_never_reaches_a_path(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path))

    with pytest.raises(paths.UnsafeIdError):
        paths.conversation_dir(42, "../../etc/passwd")
    with pytest.raises(paths.UnsafeIdError):
        paths.delegation_dir("../..", "t")


def test_iter_user_ids_lists_only_directories(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path))
    paths.user_dir(1).mkdir(parents=True)
    paths.user_dir(2).mkdir(parents=True)
    (paths.users_root() / "loose.txt").write_text("not a user")

    assert list(paths.iter_user_ids()) == ["1", "2"]


def test_iter_user_ids_is_empty_before_anything_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "nothing-here"))

    assert list(paths.iter_user_ids()) == []
