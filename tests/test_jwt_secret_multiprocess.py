"""Cross-process web JWT secret consistency (config_manager + condor/web/auth).

Several processes share config.yml — the main bot plus each MCP subprocess —
and each holds its own ConfigManager snapshot loaded at startup. These tests
cover the split-brain failure where a process whose snapshot predates the
secret generated its own and clobbered the file, invalidating every JWT the
sibling processes signed (surfaced as opaque 401 "Invalid token" on consult).
"""

import pytest
from jose import jwt

import condor.web.auth as auth
from config_manager import ConfigManager


@pytest.fixture
def two_managers(tmp_path, monkeypatch):
    """Two ConfigManager instances over the same config file — one per
    simulated process. ``stale`` loads its snapshot before the file has a
    secret; ``fresh`` is used to interact with the file afterwards."""
    monkeypatch.delenv("WEB_JWT_SECRET", raising=False)
    path = tmp_path / "config.yml"
    path.write_text("users: {}\n")
    stale = ConfigManager(str(path))
    fresh = ConfigManager(str(path))
    yield stale, fresh
    ConfigManager.reset_instance()


def test_get_or_create_adopts_secret_persisted_by_sibling(two_managers):
    """A snapshot without the secret must adopt the on-disk one, not mint a
    second secret and clobber the sibling's via _save_config."""
    stale, fresh = two_managers

    sibling_secret = fresh.get_or_create_web_jwt_secret()
    assert sibling_secret

    assert stale.get_or_create_web_jwt_secret() == sibling_secret


def test_get_or_create_still_generates_when_disk_has_none(two_managers):
    """First-ever caller still generates and persists a secret."""
    stale, fresh = two_managers

    secret = stale.get_or_create_web_jwt_secret()
    assert secret
    # Persisted: the other snapshot adopts it rather than generating anew.
    assert fresh.get_or_create_web_jwt_secret() == secret


def test_decode_jwt_heals_from_stale_snapshot_secret(two_managers, monkeypatch):
    """decode_jwt must accept a token signed with the on-disk secret even when
    this process's snapshot holds an outdated one (refresh-and-retry)."""
    stale, fresh = two_managers

    # This process cached secret A; a sibling later rotated the file to B and
    # signed its tokens with B.
    stale._data["web_jwt_secret"] = "secret-A-cached-before-rotation"
    fresh._data["web_jwt_secret"] = None
    rotated = fresh.get_or_create_web_jwt_secret()
    token = jwt.encode({"sub": "1"}, rotated, algorithm=auth._ALGORITHM)

    monkeypatch.setattr(auth, "get_config_manager", lambda: stale)
    payload = auth.decode_jwt(token)
    assert payload is not None and payload["sub"] == "1"
    # The refresh also repaired the snapshot for future create_jwt calls.
    assert stale._data["web_jwt_secret"] == rotated


def test_decode_jwt_still_rejects_garbage(two_managers, monkeypatch):
    """The refresh-and-retry path must not turn invalid tokens into valid ones."""
    stale, _fresh = two_managers
    stale.get_or_create_web_jwt_secret()

    monkeypatch.setattr(auth, "get_config_manager", lambda: stale)
    assert auth.decode_jwt("not-a-jwt") is None
    wrong = jwt.encode({"sub": "1"}, "wrong-secret", algorithm=auth._ALGORITHM)
    assert auth.decode_jwt(wrong) is None
