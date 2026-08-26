"""/portfolio may only read a server the calling user can access (CORR-246).

``portfolio_command`` used to build its candidate list from
``ConfigManager.list_servers()`` -- every server on the install -- filter it on
``enabled`` alone, and fall back to ``enabled_servers[0]`` whenever the user's
stored ``active_server`` preference was unset or stale. It then reached for the
raw by-name client factory, which performs no access check, so a trader shared
only into server B (or a freshly approved user with no server at all) got server
A's full balance sheet rendered into their chat and cached in ``KEY_BALANCES`` /
``KEY_SERVER_NAME`` for the connector-detail callbacks to keep serving.

The sibling of ``tests/test_web_server_access_dependency.py`` for the Telegram
side: the resolution is pinned here, on the one helper both the command and its
refresh callback now go through.
"""

import pytest

from handlers import portfolio

MINE = "mine-prod"
THEIRS = "someone-else"
USER_ID = 555


class FakeConfigManager:
    """Two enabled servers; the user can reach exactly one of them."""

    def __init__(self, accessible):
        self._accessible = accessible

    def list_servers(self):
        # THEIRS first, so an unfiltered ``enabled_servers[0]`` fallback lands
        # on the server the user must never see.
        return {
            THEIRS: {"enabled": True},
            MINE: {"enabled": True},
        }

    def get_accessible_servers(self, user_id):
        return list(self._accessible) if user_id == USER_ID else []


class FakeContext:
    def __init__(self, user_data):
        self.user_data = user_data


@pytest.fixture
def cm(monkeypatch):
    def _install(accessible):
        fake = FakeConfigManager(accessible)
        monkeypatch.setattr("config_manager.get_config_manager", lambda: fake)
        return fake

    return _install


def _context(active_server=None, user_id=USER_ID):
    user_data = {}
    if user_id is not None:
        user_data["_user_id"] = user_id
    if active_server is not None:
        from handlers.config.user_preferences import set_active_server

        set_active_server(user_data, active_server)
    return FakeContext(user_data)


def test_unset_preference_never_falls_back_to_a_foreign_server(cm):
    cm([MINE])

    assert portfolio._resolve_server_for_user(_context()) == MINE


def test_stale_preference_for_a_foreign_server_is_ignored(cm):
    cm([MINE])

    assert portfolio._resolve_server_for_user(_context(active_server=THEIRS)) == MINE


def test_accessible_preference_is_honoured(cm):
    cm([MINE, THEIRS])

    assert portfolio._resolve_server_for_user(_context(active_server=THEIRS)) == THEIRS


def test_user_with_no_accessible_server_is_refused(cm):
    cm([])

    with pytest.raises(ValueError, match="No accessible API servers"):
        portfolio._resolve_server_for_user(_context())


def test_missing_user_id_is_refused_rather_than_served_any_server(cm):
    cm([MINE, THEIRS])

    with pytest.raises(ValueError, match="No accessible API servers"):
        portfolio._resolve_server_for_user(_context(user_id=None))


def test_disabled_servers_are_never_candidates(cm, monkeypatch):
    fake = cm([MINE])
    monkeypatch.setattr(
        fake, "list_servers", lambda: {MINE: {"enabled": False}, THEIRS: {}}
    )

    with pytest.raises(ValueError, match="No accessible API servers"):
        portfolio._resolve_server_for_user(_context())
