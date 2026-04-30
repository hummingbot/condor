"""
Auto-update utilities for Condor and Hummingbot API.

Compares local HEAD against the remote branch, pulls new commits,
and restarts the process when requested.
"""

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

# How often to check for updates (seconds)
UPDATE_CHECK_INTERVAL = int(os.environ.get("UPDATE_CHECK_INTERVAL", "3600"))  # 1h default

CONDOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMMINGBOT_API_DIR = os.path.normpath(
    os.environ.get("HUMMINGBOT_API_DIR", os.path.join(CONDOR_DIR, "..", "hummingbot-api"))
)


async def _run_cmd(*args: str, cwd: str | None = None) -> tuple[int, str]:
    """Run a command and return (returncode, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode().strip() if stdout else ""
    if proc.returncode != 0 and stderr:
        err_text = stderr.decode().strip()
        logger.debug("%s stderr: %s", " ".join(args[:3]), err_text)
        if not output:
            output = err_text
    return proc.returncode, output


async def _run_git(*args: str, repo_dir: str = CONDOR_DIR) -> tuple[int, str]:
    """Run a git command in the given repo and return (returncode, stdout)."""
    return await _run_cmd("git", *args, cwd=repo_dir)


async def get_local_commit(repo_dir: str = CONDOR_DIR) -> str:
    """Return the short hash of the current HEAD."""
    _, out = await _run_git("rev-parse", "--short", "HEAD", repo_dir=repo_dir)
    return out


async def get_local_commit_full(repo_dir: str = CONDOR_DIR) -> str:
    """Return the full hash of the current HEAD."""
    _, out = await _run_git("rev-parse", "HEAD", repo_dir=repo_dir)
    return out


async def get_current_branch(repo_dir: str = CONDOR_DIR) -> str:
    """Return the current branch name."""
    _, out = await _run_git("rev-parse", "--abbrev-ref", "HEAD", repo_dir=repo_dir)
    return out


async def check_for_updates(repo_dir: str = CONDOR_DIR) -> dict:
    """
    Fetch from remote and compare local vs remote HEAD.

    Returns dict with:
        - up_to_date: bool
        - local_commit: str (short hash)
        - remote_commit: str (short hash)
        - commits_behind: int
        - commit_log: str (summary of new commits)
        - branch: str
        - error: str | None
    """
    result = {
        "up_to_date": True,
        "local_commit": "",
        "remote_commit": "",
        "commits_behind": 0,
        "commit_log": "",
        "branch": "",
        "error": None,
    }

    try:
        branch = await get_current_branch(repo_dir)
        result["branch"] = branch

        # Fetch latest from remote
        rc, _ = await _run_git("fetch", "origin", branch, repo_dir=repo_dir)
        if rc != 0:
            result["error"] = "Failed to fetch from remote"
            return result

        # Get local and remote commits
        _, local = await _run_git("rev-parse", "--short", "HEAD", repo_dir=repo_dir)
        _, remote = await _run_git("rev-parse", "--short", f"origin/{branch}", repo_dir=repo_dir)
        result["local_commit"] = local
        result["remote_commit"] = remote

        if local == remote:
            return result

        # Count commits behind
        _, count_str = await _run_git(
            "rev-list", "--count", f"HEAD..origin/{branch}", repo_dir=repo_dir
        )
        commits_behind = int(count_str) if count_str.isdigit() else 0
        result["commits_behind"] = commits_behind
        result["up_to_date"] = commits_behind == 0

        if commits_behind > 0:
            # Get log of new commits
            _, log = await _run_git(
                "log", "--oneline", f"HEAD..origin/{branch}", "--max-count=10",
                repo_dir=repo_dir,
            )
            result["commit_log"] = log

    except Exception as e:
        logger.error("Error checking for updates: %s", e)
        result["error"] = str(e)

    return result


async def pull_updates(repo_dir: str = CONDOR_DIR) -> tuple[bool, str]:
    """
    Pull latest changes from remote.

    Returns (success, message).
    """
    branch = await get_current_branch(repo_dir)

    # Check for uncommitted changes
    rc, status = await _run_git("status", "--porcelain", repo_dir=repo_dir)
    if status:
        return False, "Cannot update: there are uncommitted changes. Please commit or stash first."

    # Pull
    rc, output = await _run_git("pull", "origin", branch, repo_dir=repo_dir)
    if rc != 0:
        return False, f"Pull failed:\n{output}"

    return True, output


async def install_dependencies() -> tuple[bool, str]:
    """Run uv sync to install any new dependencies."""
    rc, output = await _run_cmd("uv", "sync", cwd=CONDOR_DIR)
    if rc != 0:
        return False, f"Dependency install failed:\n{output}"
    return True, output


def restart_process() -> None:
    """
    Restart the current process by replacing it with a fresh one.
    Uses os.execv to replace the running process in-place.
    """
    logger.info("Restarting Condor...")
    python = sys.executable
    os.execv(python, [python] + sys.argv)


# ---------------------------------------------------------------------------
# Hummingbot API helpers
# ---------------------------------------------------------------------------


def hb_api_available() -> bool:
    """Check if the hummingbot-api directory exists."""
    return os.path.isdir(HUMMINGBOT_API_DIR)


async def get_docker_container_info(container_name: str = "hummingbot-api") -> dict | None:
    """
    Inspect a Docker container and return basic info.

    Returns {"status", "started_at", "image"} or None if unavailable.
    """
    rc, output = await _run_cmd(
        "docker", "inspect",
        "--format", '{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}',
        container_name,
    )
    if rc != 0 or not output:
        return None

    parts = output.split("|", 2)
    if len(parts) < 3:
        return None

    return {
        "status": parts[0],
        "started_at": parts[1],
        "image": parts[2],
    }


async def check_hb_api_updates() -> dict:
    """
    Check hummingbot-api for git updates and Docker status.

    Returns {"available": bool, "git_info": dict, "docker": dict | None}.
    If HUMMINGBOT_API_DIR doesn't exist, returns {"available": False}.
    """
    if not hb_api_available():
        return {"available": False}

    git_info = await check_for_updates(repo_dir=HUMMINGBOT_API_DIR)

    # Try to get Docker container info (non-fatal if Docker unavailable)
    docker = await get_docker_container_info()

    return {
        "available": True,
        "git_info": git_info,
        "docker": docker,
    }


async def update_hb_api() -> tuple[bool, str]:
    """
    Update hummingbot-api: git pull, docker compose build, docker compose up -d.

    Returns (success, message).
    """
    if not hb_api_available():
        return False, f"Hummingbot API directory not found: {HUMMINGBOT_API_DIR}"

    # Git pull
    success, msg = await pull_updates(repo_dir=HUMMINGBOT_API_DIR)
    if not success:
        return False, f"Git pull failed: {msg}"

    # Docker compose build
    rc, output = await _run_cmd(
        "docker", "compose", "build",
        cwd=HUMMINGBOT_API_DIR,
    )
    if rc != 0:
        return False, f"Docker build failed:\n{output}"

    # Docker compose up -d
    rc, output = await _run_cmd(
        "docker", "compose", "up", "-d",
        cwd=HUMMINGBOT_API_DIR,
    )
    if rc != 0:
        return False, f"Docker restart failed:\n{output}"

    return True, "Hummingbot API updated and restarted successfully."
