"""Seed a fresh workstream worktree's config.yml so a preview Condor boots usable.

A MetaBrain worktree gets no Condor config: ``config.yml`` and ``data/`` are
gitignored, and MetaBrain only copies ``.env*`` into a new worktree. Without
this, a preview Condor starts with an auto-created ``config.yml`` that has no
``servers.local`` and no credentials, and the owner fills in
localhost / admin / admin by hand for every workstream. ``uv run python
scripts/seed_worktree_config.py`` (wired to ``make setup-worktree``, which
``metabrain.yml``'s ``worktree_setup`` runs once per worktree) writes a
working ``config.yml`` instead.

Never overwrites an existing, non-empty ``config.yml`` -- a worktree that
already has one (hand-edited, or carrying real session state like a hub
token) is left alone.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yml"
ENV_PATH = REPO_ROOT / ".env"

FALLBACK_USERNAME = "admin"
FALLBACK_PASSWORD = "admin"
FALLBACK_ADMIN_ID = "1"


def read_env_var(path: Path, key: str) -> str | None:
    """Read one ``KEY=VALUE`` out of a .env file without sourcing it.

    Mirrors ``read_env_var`` in setup-environment.sh: ``source``-ing a .env
    runs it as a shell script, so a value with shell metacharacters can abort
    the read. The last matching line wins, matching the shell version.
    """
    if not path.is_file():
        return None
    value: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        found_key, _, found_value = line.partition("=")
        if found_key.strip() != key:
            continue
        found_value = found_value.strip()
        if (
            len(found_value) >= 2
            and found_value[0] == found_value[-1]
            and found_value[0] in "\"'"
        ):
            found_value = found_value[1:-1]
        value = found_value
    return value


def main_checkout_root(worktree_root: Path) -> Path | None:
    """The main checkout's directory, resolved from this worktree's git-common-dir.

    ``git rev-parse --git-common-dir`` points at the shared ``.git`` that
    every worktree of a repo (including the main one) uses; its parent is
    the main checkout's own root.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=worktree_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (worktree_root / common_dir).resolve()
    return common_dir.parent


def resolve_local_credentials(
    worktree_root: Path, main_root: Path | None = None
) -> tuple[str, str, str]:
    """(username, password, source) for ``servers.local``.

    Inherited from the main checkout's own ``config.yml`` when it has a
    non-empty username and password for its ``local`` server; otherwise the
    admin/admin fallback.
    """
    if main_root is None:
        main_root = main_checkout_root(worktree_root)
    if main_root is not None:
        main_config = main_root / "config.yml"
        if main_config.is_file():
            try:
                data = yaml.safe_load(main_config.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            local = (data.get("servers") or {}).get("local") or {}
            username = local.get("username") or ""
            password = local.get("password") or ""
            if username and password:
                return str(username), str(password), f"inherited from {main_config}"
    return FALLBACK_USERNAME, FALLBACK_PASSWORD, "admin/admin fallback"


def resolve_admin_id(env_path: Path) -> int | str:
    raw = read_env_var(env_path, "ADMIN_USER_ID") or FALLBACK_ADMIN_ID
    return int(raw) if raw.isdigit() else raw


def seed_config(worktree_root: Path) -> int:
    """Write ``worktree_root/config.yml``; no-op if one already exists and is non-empty."""
    config_path = worktree_root / "config.yml"
    if config_path.is_file() and config_path.stat().st_size > 0:
        print(f"{config_path} already exists -- leaving it alone.")
        return 0

    admin_id = resolve_admin_id(worktree_root / ".env")
    username, password, source = resolve_local_credentials(worktree_root)

    config = {
        "servers": {
            "local": {
                "host": "localhost",
                "port": 8000,
                "username": username,
                "password": password,
            },
        },
        "default_server": "local",
        "admin_id": admin_id,
        # Mirrors ConfigManager._ensure_admin_user's own shape: without this
        # record, check_local_user() rejects the id this script just picked
        # as admin_id and a fresh preview refuses to boot (ConfigManager only
        # materialises it when ADMIN_USER_ID is set in the environment).
        "users": {
            admin_id: {
                "user_id": admin_id,
                "role": "admin",
                "created_at": time.time(),
                "notes": "Primary admin from ADMIN_USER_ID",
            },
        },
        "server_access": {
            "local": {
                "owner_id": admin_id,
                "created_at": None,
                "shared_with": {},
            },
        },
        "chat_defaults": {admin_id: "local"},
        "version": 1,
    }

    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
    )
    config_path.chmod(0o600)
    print(
        f"Wrote {config_path}: local server credentials {source}, admin_id={admin_id}."
    )
    return 0


def main() -> int:
    return seed_config(REPO_ROOT)


if __name__ == "__main__":
    sys.exit(main())
