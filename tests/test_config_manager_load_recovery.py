"""A config.yml that cannot be read must never be overwritten (CORR-106).

Also the round trip that keeps CONFIG_SECTIONS honest (ARCH-738): every
declared section must survive a save and a fresh load, and nothing undeclared
may be persisted. The round-trip tests iterate the declaration rather than a
list of their own, so a section added to CONFIG_SECTIONS with no save path
fails here immediately instead of being discarded in production.
"""

import textwrap

import pytest
import yaml

from config_manager import CONFIG_SECTIONS, ConfigManager, ServerPermission

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


ADMIN_ID = 111


def _probe_for(name, default):
    """A distinctive value for section `name` that a save/reload must return intact.

    Two sections cannot simply take an arbitrary value, because `_load_config`
    deliberately overrides them, and the probe matches what it will do:
    `admin_id` is always re-trusted from ADMIN_USER_ID, and `users` always
    carries a record for that admin. Every other section — including one added
    to the declaration tomorrow — is probed generically.
    """
    if name == "admin_id":
        return ADMIN_ID
    if name == "users":
        return {ADMIN_ID: {"user_id": ADMIN_ID, "role": "admin"}, "probe": name}
    if isinstance(default, dict):
        return {"probe": name}
    return f"probe-{name}"


def test_setdefault_path_and_default_data_declare_the_same_sections(cm_env):
    """The load path and the empty-structure path cannot drift apart again."""
    config_path = cm_env / "config.yml"
    _write_yaml(config_path, {})

    cm = ConfigManager(str(config_path))

    assert set(cm._data) == set(cm._default_data()) == set(CONFIG_SECTIONS)
    assert "sharing" in cm._default_data()


def test_every_declared_section_survives_a_save_and_reload(cm_env):
    """A value written into any declared section is still there after a restart."""
    config_path = cm_env / "config.yml"
    cm = ConfigManager(str(config_path))
    probes = {
        name: _probe_for(name, default(cm)) for name, default in CONFIG_SECTIONS.items()
    }

    cm._data.update(probes)
    cm._save_config()

    fresh = ConfigManager(str(config_path))
    for name, value in probes.items():
        assert fresh._data[name] == value, f"{name} did not survive the round trip"


def test_a_section_added_to_the_declaration_needs_no_other_change(cm_env, monkeypatch):
    """Declaring a section is the whole change: no save path to remember."""
    config_path = cm_env / "config.yml"
    monkeypatch.setitem(CONFIG_SECTIONS, "arch738_fixture", lambda cm: {})

    cm = ConfigManager(str(config_path))
    cm._data["arch738_fixture"] = {"token": "kept"}
    cm._save_config()

    fresh = ConfigManager(str(config_path))
    assert fresh._data["arch738_fixture"] == {"token": "kept"}


def test_an_undeclared_key_is_still_not_persisted(cm_env):
    """The save stays a projection: runtime scratch in _data never reaches disk."""
    config_path = cm_env / "config.yml"
    cm = ConfigManager(str(config_path))

    cm._data["junk"] = 1
    cm._save_config()

    assert "junk" not in yaml.safe_load(config_path.read_text())
    assert "junk" not in ConfigManager(str(config_path))._data
