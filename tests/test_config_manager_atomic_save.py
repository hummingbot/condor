"""config.yml is replaced atomically and its backup is never made worse (OP-07).

The save used to truncate the file in place, so anything that stopped it
mid-write left a short file — and truncated YAML is often still valid YAML with
fewer keys, which loaded cleanly and was saved back as truth. The rotation ran
first and unconditionally, so a damaged file also overwrote the good ``.bak``.
"""

import os
import stat

import pytest
import yaml

import config_manager
from config_manager import ConfigManager


@pytest.fixture
def cm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # audit_log.yml is cwd-relative
    monkeypatch.setattr("utils.config.ADMIN_USER_ID", 111)
    manager = ConfigManager(str(tmp_path / "config.yml"))
    manager._data["servers"]["prod"] = {"url": "http://localhost:8000"}
    manager._save_config()
    return manager


def test_a_dump_that_fails_leaves_config_and_backup_untouched(cm, monkeypatch):
    before = cm.config_path.read_bytes()
    backup_before = cm._backup_path.read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(config_manager.yaml, "dump", boom)
    cm._data["servers"]["other"] = {"url": "http://localhost:9000"}
    with pytest.raises(RuntimeError):
        cm._save_config()

    assert cm.config_path.read_bytes() == before
    assert cm._backup_path.read_bytes() == backup_before
    assert "version" in yaml.safe_load(cm._backup_path.read_text())


def test_a_write_that_fails_leaves_config_intact_and_no_litter(cm, monkeypatch):
    before = cm.config_path.read_bytes()

    def boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", boom)
    cm._data["servers"]["other"] = {"url": "http://localhost:9000"}
    with pytest.raises(OSError):
        cm._save_config()

    assert cm.config_path.read_bytes() == before
    assert not list(cm.config_path.parent.glob(".config.yml.*"))


def test_a_cut_short_config_is_not_rotated_over_the_good_backup(cm):
    good_backup = cm._backup_path.read_bytes()
    # Still valid YAML, fewer keys: what a truncating write leaves behind.
    cm.config_path.write_text("servers: {}\ndefault_server: null\n")

    cm._write_config_file()

    assert cm._backup_path.read_bytes() == good_backup
    assert "version" in yaml.safe_load(cm.config_path.read_text())


def test_an_unparseable_config_is_not_rotated_over_the_good_backup(cm):
    good_backup = cm._backup_path.read_bytes()
    cm.config_path.write_text("servers:\n  prod:\n url: [broken\n")

    cm._write_config_file()

    assert cm._backup_path.read_bytes() == good_backup


def test_a_good_config_still_rotates_into_the_backup(cm):
    previous = cm.config_path.read_bytes()
    cm._data["servers"]["other"] = {"url": "http://localhost:9000"}
    cm._save_config()

    assert cm._backup_path.read_bytes() == previous
    assert "other" in yaml.safe_load(cm.config_path.read_text())["servers"]


def test_the_saved_file_is_owner_only_even_if_it_was_not(cm):
    os.chmod(cm.config_path, 0o644)
    cm._save_config()
    assert stat.S_IMODE(cm.config_path.stat().st_mode) == 0o600
