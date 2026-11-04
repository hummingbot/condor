"""Unit tests for scripts/seed_worktree_config.py."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import seed_worktree_config as swc  # noqa: E402


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def worktree_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A real main checkout plus a real worktree of it, so git-common-dir resolution is exercised for real."""
    main_root = tmp_path / "main"
    main_root.mkdir()
    _run_git("init", "-q", cwd=main_root)
    _run_git("config", "user.email", "test@example.com", cwd=main_root)
    _run_git("config", "user.name", "Test", cwd=main_root)
    (main_root / "README.md").write_text("seed\n")
    _run_git("add", "README.md", cwd=main_root)
    _run_git("commit", "-q", "-m", "seed", cwd=main_root)

    worktree_root = tmp_path / "wt"
    _run_git(
        "worktree", "add", "-q", "-b", "wt-branch", str(worktree_root), cwd=main_root
    )
    return main_root, worktree_root


def test_main_checkout_root_resolves_to_the_real_main_checkout(
    worktree_pair: tuple[Path, Path],
) -> None:
    main_root, worktree_root = worktree_pair
    resolved = swc.main_checkout_root(worktree_root)
    assert resolved == main_root.resolve()


def test_main_checkout_root_returns_none_outside_a_git_repo(tmp_path: Path) -> None:
    bare_dir = tmp_path / "not-a-repo"
    bare_dir.mkdir()
    assert swc.main_checkout_root(bare_dir) is None


def test_read_env_var_skips_comments_and_strips_quotes(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text('# comment\nADMIN_USER_ID="481175164"\nOTHER=1\n')
    assert swc.read_env_var(env_path, "ADMIN_USER_ID") == "481175164"
    assert swc.read_env_var(env_path, "MISSING") is None


def test_read_env_var_missing_file_returns_none(tmp_path: Path) -> None:
    assert swc.read_env_var(tmp_path / "nope.env", "ADMIN_USER_ID") is None


def test_resolve_admin_id_reads_from_env(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("ADMIN_USER_ID=481175164\n")
    assert swc.resolve_admin_id(env_path) == 481175164


def test_resolve_admin_id_falls_back_to_1(tmp_path: Path) -> None:
    assert swc.resolve_admin_id(tmp_path / "missing.env") == 1


def test_resolve_local_credentials_inherits_from_main_checkout(tmp_path: Path) -> None:
    main_root = tmp_path / "main"
    main_root.mkdir()
    (main_root / "config.yml").write_text(
        yaml.safe_dump(
            {"servers": {"local": {"username": "admin", "password": "sekret"}}}
        )
    )
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    username, password, source = swc.resolve_local_credentials(
        worktree_root, main_root=main_root
    )

    assert (username, password) == ("admin", "sekret")
    assert "inherited" in source


def test_resolve_local_credentials_falls_back_when_main_has_no_credentials(
    tmp_path: Path,
) -> None:
    main_root = tmp_path / "main"
    main_root.mkdir()
    (main_root / "config.yml").write_text(
        yaml.safe_dump({"servers": {"local": {"username": "", "password": ""}}})
    )
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    username, password, source = swc.resolve_local_credentials(
        worktree_root, main_root=main_root
    )

    assert (username, password) == (swc.FALLBACK_USERNAME, swc.FALLBACK_PASSWORD)
    assert source == "admin/admin fallback"


def test_resolve_local_credentials_falls_back_when_main_config_missing(
    tmp_path: Path,
) -> None:
    main_root = tmp_path / "main-without-config"
    main_root.mkdir()
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()

    username, password, source = swc.resolve_local_credentials(
        worktree_root, main_root=main_root
    )

    assert (username, password) == (swc.FALLBACK_USERNAME, swc.FALLBACK_PASSWORD)
    assert source == "admin/admin fallback"


def test_seed_config_noop_when_config_already_exists(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("servers: {}\n")
    before = config_path.read_text()

    exit_code = swc.seed_config(tmp_path)

    assert exit_code == 0
    assert config_path.read_text() == before


def test_seed_config_writes_full_shape_with_fallback_credentials(
    tmp_path: Path,
) -> None:
    exit_code = swc.seed_config(tmp_path)
    assert exit_code == 0

    config_path = tmp_path / "config.yml"
    data = yaml.safe_load(config_path.read_text())

    assert data["servers"]["local"] == {
        "host": "localhost",
        "port": 8000,
        "username": "admin",
        "password": "admin",
    }
    assert data["default_server"] == "local"
    assert data["admin_id"] == 1
    assert data["users"].keys() == {1}
    assert data["users"][1]["role"] == "admin"
    assert data["server_access"]["local"] == {
        "owner_id": 1,
        "created_at": None,
        "shared_with": {},
    }
    assert data["chat_defaults"] == {1: "local"}
    assert data["version"] == 1


def test_seed_config_chmods_600(tmp_path: Path) -> None:
    swc.seed_config(tmp_path)
    mode = stat.S_IMODE((tmp_path / "config.yml").stat().st_mode)
    assert mode == 0o600


def test_seed_config_uses_env_admin_id_and_never_prints_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text("ADMIN_USER_ID=999\n")

    swc.seed_config(tmp_path)

    data = yaml.safe_load((tmp_path / "config.yml").read_text())
    assert data["admin_id"] == 999
    assert data["chat_defaults"] == {999: "local"}
    assert data["users"][999]["role"] == "admin"

    captured = capsys.readouterr()
    assert "admin_id=999" in captured.out
    assert "password" not in captured.out.lower()


def test_seed_config_lets_a_fresh_preview_boot_with_admin_user_id_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Greptile P1 on PR #314: ``ADMIN_USER_ID`` unset picks id ``1`` for
    ``admin_id`` but used to write ``users: {}``. ``ConfigManager`` only
    materialises an admin user record from the *environment*
    (``_get_admin_from_env``), so with no ``ADMIN_USER_ID`` set that record
    never appears and ``check_local_user`` rejected user ``1`` at boot.
    """
    from config_manager import ConfigManager, UserRole
    from utils import config as app_config
    from utils.config import check_local_user

    monkeypatch.setattr(app_config, "ADMIN_USER_ID", None)

    exit_code = swc.seed_config(tmp_path)
    assert exit_code == 0

    cm = ConfigManager(config_path=str(tmp_path / "config.yml"))
    assert cm.get_user_role(1) == UserRole.ADMIN

    check_local_user({"CONDOR_MODE": "local"}, get_role=cm.get_user_role)
