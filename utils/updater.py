"""
Auto-update utilities for Condor and Hummingbot API.

Compares local HEAD against the remote branch, pulls new commits, rebuilds
whatever the new commits invalidated, and restarts the process when requested.

The restart is a *graceful* one: :func:`request_restart` asks the running
process to wind itself down through the normal shutdown path (so persistence is
flushed and subprocesses are reaped) and ``main()`` re-execs once that finished.
The exec replaces the process in place, which keeps Condor inside the same tmux
pane it was started in.
"""

import asyncio
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)

# How often to check for updates (seconds)
UPDATE_CHECK_INTERVAL = int(os.environ.get("UPDATE_CHECK_INTERVAL", "3600"))  # 1h default

CONDOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMMINGBOT_API_DIR = os.path.normpath(
    os.environ.get("HUMMINGBOT_API_DIR", os.path.join(CONDOR_DIR, "..", "hummingbot-api"))
)
FRONTEND_DIR = os.path.join(CONDOR_DIR, "frontend")

# Step timeouts (seconds). A hung `npm ci` or a `uv sync` waiting on a prompt
# would otherwise leave the update stuck forever with no way to recover.
GIT_TIMEOUT = 180
DEPS_TIMEOUT = 900
FRONTEND_BUILD_TIMEOUT = 1200
DOCKER_TIMEOUT = 1800


async def _run_cmd(
    *args: str, cwd: str | None = None, timeout: float | None = None
) -> tuple[int, str]:
    """Run a command and return (returncode, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        # Kill the whole thing: a half-finished build is not worth waiting on.
        proc.kill()
        await proc.wait()
        cmd = " ".join(args[:3])
        logger.error("Command timed out after %ss: %s", timeout, cmd)
        return 124, f"Timed out after {int(timeout or 0)}s: {cmd}"

    output = stdout.decode(errors="replace").strip() if stdout else ""
    if proc.returncode != 0 and stderr:
        err_text = stderr.decode(errors="replace").strip()
        logger.debug("%s stderr: %s", " ".join(args[:3]), err_text)
        if not output:
            output = err_text
    return proc.returncode, output


async def _run_git(*args: str, repo_dir: str = CONDOR_DIR) -> tuple[int, str]:
    """Run a git command in the given repo and return (returncode, stdout)."""
    return await _run_cmd("git", *args, cwd=repo_dir, timeout=GIT_TIMEOUT)


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
    rc, output = await _run_cmd("uv", "sync", cwd=CONDOR_DIR, timeout=DEPS_TIMEOUT)
    if rc != 0:
        return False, f"Dependency install failed:\n{output}"
    return True, output


async def paths_changed(
    old_commit: str, new_commit: str, *paths: str, repo_dir: str = CONDOR_DIR
) -> bool:
    """Whether any of ``paths`` differs between two commits.

    Errs on the side of True: if the diff can't be resolved (shallow clone,
    rewritten history), the caller should redo the work rather than skip it.
    """
    if not old_commit or not new_commit:
        return True
    if old_commit == new_commit:
        return False
    rc, out = await _run_git(
        "diff", "--name-only", f"{old_commit}..{new_commit}", "--", *paths,
        repo_dir=repo_dir,
    )
    if rc != 0:
        logger.warning(
            "Could not diff %s..%s, assuming changed", old_commit, new_commit
        )
        return True
    return bool(out.strip())


async def frontend_needs_build(old_commit: str, new_commit: str) -> bool:
    """Whether the dashboard bundle has to be rebuilt after a pull.

    ``make run`` builds the frontend before starting, but an in-place update
    never goes through the Makefile — without this, pulled frontend commits
    would keep serving the stale ``frontend/dist`` bundle until someone ran
    ``make restart`` by hand.
    """
    if not os.path.isdir(FRONTEND_DIR):
        return False
    # No bundle at all (fresh clone, cleaned tree) — build regardless of diff.
    if not os.path.isfile(os.path.join(FRONTEND_DIR, "dist", "index.html")):
        return True
    return await paths_changed(old_commit, new_commit, "frontend")


async def build_frontend() -> tuple[bool, str]:
    """Build the dashboard bundle, mirroring the Makefile's build-frontend target.

    Node usually lives under nvm rather than on the PATH Condor inherited, so
    source nvm.sh first exactly like the Makefile does.
    """
    if not os.path.isdir(FRONTEND_DIR):
        return True, "No frontend directory; skipped."

    script = (
        'export NVM_DIR="$HOME/.nvm"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        'cd "$1" || exit 1; '
        "{ [ -d node_modules ] || npm ci; } && npm run build"
    )
    rc, output = await _run_cmd(
        "bash", "-c", script, "bash", FRONTEND_DIR, timeout=FRONTEND_BUILD_TIMEOUT
    )
    if rc != 0:
        return False, output or "Frontend build failed (no output)"
    return True, output


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

_restart_pending = False


def request_restart() -> None:
    """Ask Condor to shut down cleanly and come back up.

    Deliberately does NOT exec here: os.execv from inside a handler would drop
    the process image on the spot, skipping ``teardown()`` — persistence would
    lose up to ``update_interval`` seconds of state, trading-agent loops would
    never record their final state, and ACP/MCP subprocesses would be orphaned.
    Instead this raises SIGTERM, which the running loop already handles as a
    normal shutdown, and ``main()`` re-execs once teardown has finished.
    """
    global _restart_pending
    _restart_pending = True
    logger.info("Restart requested; signalling shutdown")
    os.kill(os.getpid(), signal.SIGTERM)


def restart_pending() -> bool:
    """Whether the shutdown currently under way should end in a restart."""
    return _restart_pending


def exec_restart() -> None:
    """Replace this process with a fresh one. Never returns.

    Called from ``main()`` after the event loop is gone. Replacing the image
    in place keeps the PID, the parent (``uv run``) and the controlling
    terminal, so Condor stays in the same tmux pane it was started in.
    """
    python = sys.executable
    logger.info("Restarting Condor: exec %s %s", python, " ".join(sys.argv))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    # sys.argv[0] is usually the relative "main.py"; make sure it still resolves.
    os.chdir(CONDOR_DIR)
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
        timeout=30,
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
        timeout=DOCKER_TIMEOUT,
    )
    if rc != 0:
        return False, f"Docker build failed:\n{output}"

    # Docker compose up -d
    rc, output = await _run_cmd(
        "docker", "compose", "up", "-d",
        cwd=HUMMINGBOT_API_DIR,
        timeout=DOCKER_TIMEOUT,
    )
    if rc != 0:
        return False, f"Docker restart failed:\n{output}"

    return True, "Hummingbot API updated and restarted successfully."
