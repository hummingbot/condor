"""
Auto-update utilities for Condor.

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


async def _run_git(*args: str) -> tuple[int, str]:
    """Run a git command and return (returncode, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode().strip() if stdout else ""
    if proc.returncode != 0 and stderr:
        logger.debug("git %s stderr: %s", " ".join(args), stderr.decode().strip())
    return proc.returncode, output


async def get_local_commit() -> str:
    """Return the short hash of the current HEAD."""
    _, out = await _run_git("rev-parse", "--short", "HEAD")
    return out


async def get_local_commit_full() -> str:
    """Return the full hash of the current HEAD."""
    _, out = await _run_git("rev-parse", "HEAD")
    return out


async def get_current_branch() -> str:
    """Return the current branch name."""
    _, out = await _run_git("rev-parse", "--abbrev-ref", "HEAD")
    return out


async def check_for_updates() -> dict:
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
        branch = await get_current_branch()
        result["branch"] = branch

        # Fetch latest from remote
        rc, _ = await _run_git("fetch", "origin", branch)
        if rc != 0:
            result["error"] = "Failed to fetch from remote"
            return result

        # Get local and remote commits
        _, local = await _run_git("rev-parse", "--short", "HEAD")
        _, remote = await _run_git("rev-parse", "--short", f"origin/{branch}")
        result["local_commit"] = local
        result["remote_commit"] = remote

        if local == remote:
            return result

        # Count commits behind
        _, count_str = await _run_git(
            "rev-list", "--count", f"HEAD..origin/{branch}"
        )
        commits_behind = int(count_str) if count_str.isdigit() else 0
        result["commits_behind"] = commits_behind
        result["up_to_date"] = commits_behind == 0

        if commits_behind > 0:
            # Get log of new commits
            _, log = await _run_git(
                "log", "--oneline", f"HEAD..origin/{branch}", "--max-count=10"
            )
            result["commit_log"] = log

    except Exception as e:
        logger.error("Error checking for updates: %s", e)
        result["error"] = str(e)

    return result


async def pull_updates() -> tuple[bool, str]:
    """
    Pull latest changes from remote.

    Returns (success, message).
    """
    branch = await get_current_branch()

    # Check for uncommitted changes
    rc, status = await _run_git("status", "--porcelain")
    if status:
        return False, "Cannot update: there are uncommitted changes. Please commit or stash first."

    # Pull
    rc, output = await _run_git("pull", "origin", branch)
    if rc != 0:
        return False, f"Pull failed:\n{output}"

    return True, output


async def install_dependencies() -> tuple[bool, str]:
    """Run uv sync to install any new dependencies."""
    proc = await asyncio.create_subprocess_exec(
        "uv", "sync",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode().strip() if stdout else ""
    if proc.returncode != 0:
        err = stderr.decode().strip() if stderr else ""
        return False, f"Dependency install failed:\n{err}"
    return True, output


def restart_process() -> None:
    """
    Restart the current process by replacing it with a fresh one.
    Uses os.execv to replace the running process in-place.
    """
    logger.info("Restarting Condor...")
    python = sys.executable
    os.execv(python, [python] + sys.argv)
