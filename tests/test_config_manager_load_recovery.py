"""A config.yml that cannot be read must never be overwritten (CORR-106)."""

import textwrap

import pytest
import yaml

from config_manager import ConfigManager, ServerPermission

VALID_CONFIG = {
    "servers": {"prod": {"url": "http://localhost:8000", "username": "admin"}},
    "default_server": "prod",
    "admin_id": 111,
    "users": {
        111: {"user_id": 111, "role": "admin"},
        222: {"user_id": 222, "role": "user"},
    },
    "server_access": {"prod": {"owner": 111, "shared_with": {222: "trader"}}},
    "chat_defaults": {},
    "web_jwt_secret": "super-secret-value",
    "version": 1,
}

INVALID_YAML = textwrap.dedent("""\
    servers:
      prod:
        url: http://localhost:8000
       username: broken-indentation
    """)


@pytest.fixture
def cm_env(tmp_path, monkeypatch):
    """Isolate cwd (audit_log.yml is cwd-relative) and the admin env var."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.ADMIN_USER_ID", 111)
    return tmp_path


def _write_yaml(path, data):
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def test_unreadable_config_is_left_untouched(cm_env):
    """An invalid existing config.yml stays byte-identical after startup."""
    config_path = cm_env / "config.yml"
    config_path.write_text(INVALID_YAML)

    cm = ConfigManager(str(config_path))

    assert config_path.read_text() == INVALID_YAML
    assert cm._load_failed is True


def test_unreadable_config_refuses_to_save_on_mutation(cm_env):
    """Later writes must not clobber the file we could not read."""
    config_path = cm_env / "config.yml"
    config_path.write_text(INVALID_YAML)

    cm = ConfigManager(str(config_path))
    cm.register_pending(333, "someone")
    cm.get_or_create_web_jwt_secret()

    assert config_path.read_text() == INVALID_YAML


def test_unreadable_config_recovers_from_backup(cm_env):
    """State survives a failed load when a .bak is available."""
    config_path = cm_env / "config.yml"
    _write_yaml(config_path.with_suffix(".yml.bak"), VALID_CONFIG)
    config_path.write_text(INVALID_YAML)

    cm = ConfigManager(str(config_path))

    assert cm._load_failed is False
    assert cm.get_server("prod") is not None
    assert cm.get_user(222) is not None
    assert cm.has_server_access(222, "prod", ServerPermission.TRADER)
    assert cm.get_or_create_web_jwt_secret() == "super-secret-value"


def test_missing_config_still_bootstraps_defaults(cm_env):
    """The happy path for a fresh install is unchanged."""
    config_path = cm_env / "config.yml"

    cm = ConfigManager(str(config_path))

    assert config_path.exists()
    assert cm._load_failed is False
    written = yaml.safe_load(config_path.read_text())
    assert written["users"][111]["role"] == "admin"
    assert written["servers"] == {}


def test_save_keeps_a_backup_of_the_previous_file(cm_env):
    """Each save rotates the last known-good file to .bak."""
    config_path = cm_env / "config.yml"
    _write_yaml(config_path, VALID_CONFIG)

    cm = ConfigManager(str(config_path))
    cm.register_pending(444, "newcomer")

    backup = yaml.safe_load(config_path.with_suffix(".yml.bak").read_text())
    assert 444 not in backup["users"]
    assert backup["web_jwt_secret"] == "super-secret-value"
    assert yaml.safe_load(config_path.read_text())["web_jwt_secret"] == (
        "super-secret-value"
    )
