"""config.yml shared by more than one process.

The bot/dashboard and every MCP server process each hold their own
ConfigManager over the same file. Each used to keep the copy it read at
startup forever: a write from one was invisible to the others, and the next
save from any of them rewrote the file from its stale copy. A hub submission
recorded over MCP never reached the dashboard, and the dashboard's next save
erased it. Two instances over one path stand in for the two processes.
"""

import os
import threading

import pytest
import yaml

from config_manager import ConfigManager, _merge_changes

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

BASE_CONFIG = {
    "servers": {"local": {"host": "localhost", "port": 8000}},
    "admin_id": 111,
    "users": {111: {"user_id": 111, "role": "admin"}},
    "hub": {"submissions": {"old_agent": {"status": "published"}}},
    "version": 1,
}


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.ADMIN_USER_ID", 111)
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(BASE_CONFIG, sort_keys=False))
    return path


def _bump_mtime(path):
    """Make a same-size rewrite within one mtime tick still look changed."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def _submissions(cm):
    return set(cm.get_hub().get("submissions", {}))


def test_a_write_from_another_process_is_visible_on_the_next_read(config_path):
    dashboard = ConfigManager(str(config_path))
    mcp = ConfigManager(str(config_path))

    subs = dict(mcp.get_hub()["submissions"], new_agent={"status": "pending"})
    mcp.update_hub(submissions=subs)
    _bump_mtime(config_path)

    assert _submissions(dashboard) == {"old_agent", "new_agent"}


def test_a_save_from_a_stale_process_keeps_the_other_process_write(config_path):
    dashboard = ConfigManager(str(config_path))
    mcp = ConfigManager(str(config_path))

    subs = dict(mcp.get_hub()["submissions"], new_agent={"status": "pending"})
    mcp.update_hub(submissions=subs)
    _bump_mtime(config_path)

    # The dashboard writes an unrelated section, through the raw store so the
    # read path's refresh cannot hide a save that would clobber.
    dashboard._store["chat_defaults"][42] = "local"
    dashboard._save_config()

    on_disk = yaml.safe_load(config_path.read_text())
    assert set(on_disk["hub"]["submissions"]) == {"old_agent", "new_agent"}
    assert on_disk["chat_defaults"] == {42: "local"}


def test_both_processes_edit_different_keys_of_one_section(config_path):
    a = ConfigManager(str(config_path))
    b = ConfigManager(str(config_path))

    a._store["users"][222] = {"user_id": 222, "role": "user"}
    a._save_config()
    _bump_mtime(config_path)
    b._store["users"][333] = {"user_id": 333, "role": "pending"}
    b._save_config()

    on_disk = yaml.safe_load(config_path.read_text())
    assert set(on_disk["users"]) == {111, 222, 333}


def test_a_deletion_by_another_process_propagates(config_path):
    a = ConfigManager(str(config_path))
    b = ConfigManager(str(config_path))

    a._store["hub"]["submissions"].pop("old_agent")
    a._save_config()
    _bump_mtime(config_path)

    assert _submissions(b) == set()


def test_a_reference_held_across_a_refresh_still_writes_to_live_state(config_path):
    """The merge is in place, so ``users = cm._data["users"]`` is not orphaned."""
    a = ConfigManager(str(config_path))
    b = ConfigManager(str(config_path))

    users = b._store["users"]
    a._store["users"][222] = {"user_id": 222, "role": "user"}
    a._save_config()
    _bump_mtime(config_path)

    b.reload()
    users[333] = {"user_id": 333, "role": "pending"}
    b._save_config()

    on_disk = yaml.safe_load(config_path.read_text())
    assert set(on_disk["users"]) == {111, 222, 333}


def test_an_unreadable_file_mid_run_keeps_the_in_memory_state(config_path):
    cm = ConfigManager(str(config_path))
    config_path.write_text("servers: {local: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(config_path.read_text())
    _bump_mtime(config_path)

    assert cm._data["servers"] == BASE_CONFIG["servers"]


def test_a_server_changed_by_another_process_drops_its_cached_client(config_path):
    dashboard = ConfigManager(str(config_path))
    mcp = ConfigManager(str(config_path))
    stale = object()
    dashboard._clients["local"] = (stale, 0.0)
    dashboard._status_cache["local"] = ({"status": "online"}, 0.0)

    mcp._store["servers"]["local"]["host"] = "10.0.0.2"
    mcp._save_config()
    _bump_mtime(config_path)

    assert dashboard.get_server("local")["host"] == "10.0.0.2"
    assert "local" not in dashboard._clients
    assert "local" not in dashboard._status_cache


def test_an_unchanged_server_keeps_its_cached_client(config_path):
    dashboard = ConfigManager(str(config_path))
    mcp = ConfigManager(str(config_path))
    client = object()
    dashboard._clients["local"] = (client, 0.0)

    mcp._store["chat_defaults"][42] = "local"
    mcp._save_config()
    _bump_mtime(config_path)

    assert dashboard._data["chat_defaults"] == {42: "local"}
    assert dashboard._clients["local"][0] is client


@pytest.mark.skipif(fcntl is None, reason="POSIX file locks only")
def test_startup_waits_for_a_save_in_progress(config_path):
    """Load and baseline happen under the lock, so a concurrent save can't
    slip between them and be erased later."""
    lock_path = str(config_path) + ".lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another process is mid-save
    built = []
    t = threading.Thread(target=lambda: built.append(ConfigManager(str(config_path))))
    try:
        t.start()
        t.join(0.3)
        assert not built, "loaded while another process held the lock"
        data = yaml.safe_load(config_path.read_text())
        data["hub"]["submissions"]["new_agent"] = {"status": "pending"}
        config_path.write_text(yaml.dump(data, sort_keys=False))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    t.join(5)

    assert _submissions(built[0]) == {"old_agent", "new_agent"}


def test_a_save_while_loading_does_not_deadlock_on_our_own_lock(config_path):
    data = dict(BASE_CONFIG, users={})  # admin missing: loading saves it back
    config_path.write_text(yaml.dump(data, sort_keys=False))

    cm = ConfigManager(str(config_path))

    assert 111 in cm._data["users"]
    assert 111 in yaml.safe_load(config_path.read_text())["users"]


def test_no_external_change_is_a_plain_save(config_path):
    cm = ConfigManager(str(config_path))
    cm._store["default_server"] = "local"
    cm._save_config()

    assert yaml.safe_load(config_path.read_text())["default_server"] == "local"


@pytest.mark.parametrize(
    "base, ours, theirs, expected",
    [
        ({"k": 1}, {"k": 1}, {"k": 2}, {"k": 2}),  # only theirs changed
        ({"k": 1}, {"k": 3}, {"k": 1}, {"k": 3}),  # only ours changed
        ({"k": 1}, {"k": 3}, {"k": 2}, {"k": 3}),  # both: ours wins
        ({"k": 1}, {"k": 1}, {}, {}),  # theirs deleted
        ({"k": 1}, {}, {"k": 1}, {}),  # ours deleted
        ({}, {}, {"k": 1}, {"k": 1}),  # theirs added
        (
            {"d": {"a": 1}},
            {"d": {"a": 1, "b": 2}},
            {"d": {"a": 1, "c": 3}},
            {"d": {"a": 1, "b": 2, "c": 3}},
        ),
    ],
)
def test_merge_changes(base, ours, theirs, expected):
    _merge_changes(base, ours, theirs)
    assert ours == expected
