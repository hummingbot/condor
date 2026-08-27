"""
Update primitives: the commands, none of the policy.

Everything here shells out to git, docker, uv or npm and reports what happened.
It decides nothing -- which components exist, what blocks an update, in what
order the steps run and what is recorded lives one layer up in
:mod:`condor.updates`, and the surfaces (Telegram, the dashboard) read that.
That split is why a second surface costs no orchestration.

The restart is a *graceful* one: :func:`request_restart` asks the running
process to wind itself down through the normal shutdown path (so persistence is
flushed and subprocesses are reaped) and ``main()`` re-execs once that finished.
The exec replaces the process in place, which keeps Condor inside the same tmux
pane it was started in.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# How often to check for updates (seconds)
UPDATE_CHECK_INTERVAL = int(
    os.environ.get("UPDATE_CHECK_INTERVAL", "3600")
)  # 1h default

CONDOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMMINGBOT_API_DIR = os.path.normpath(
    os.environ.get(
        "HUMMINGBOT_API_DIR", os.path.join(CONDOR_DIR, "..", "hummingbot-api")
    )
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
        _, remote = await _run_git(
            "rev-parse", "--short", f"origin/{branch}", repo_dir=repo_dir
        )
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
                "log",
                "--oneline",
                f"HEAD..origin/{branch}",
                "--max-count=10",
                repo_dir=repo_dir,
            )
            result["commit_log"] = log

    except Exception as e:
        logger.error("Error checking for updates: %s", e)
        result["error"] = str(e)

    return result


@dataclass(frozen=True)
class DirtyState:
    """What is uncommitted in a working tree, split by how git sees it.

    Kept apart because the resolutions differ: a tracked change is discarded
    with ``git checkout HEAD --``, an untracked file with ``git clean``. The
    caller that only wants "everything uncommitted" reads :attr:`paths`.
    """

    modified: tuple[str, ...] = ()
    staged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        """Every uncommitted path, deduplicated, in a stable order."""
        seen: dict[str, None] = {}
        for group in (self.staged, self.modified, self.untracked):
            for path in group:
                seen.setdefault(path, None)
        return tuple(seen)

    @property
    def tracked(self) -> tuple[str, ...]:
        """Staged and unstaged modifications to files git already knows."""
        seen: dict[str, None] = {}
        for group in (self.staged, self.modified):
            for path in group:
                seen.setdefault(path, None)
        return tuple(seen)


def _lines(output: str) -> tuple[str, ...]:
    """Split command output into non-empty stripped lines."""
    return tuple(line.strip() for line in (output or "").split("\n") if line.strip())


async def fetch(repo_dir: str = CONDOR_DIR) -> tuple[bool, str]:
    """Update ``origin/<branch>`` without touching the working tree."""
    branch = await get_current_branch(repo_dir)
    rc, out = await _run_git("fetch", "origin", branch, repo_dir=repo_dir)
    if rc != 0:
        return False, out or "Failed to fetch from remote"
    return True, out


async def dirty_state(repo_dir: str = CONDOR_DIR) -> DirtyState:
    """Uncommitted work in ``repo_dir``, by category.

    Three plumbing commands rather than one ``status --porcelain`` parse: the
    porcelain format conflates the three categories into a two-column code that
    then has to be decoded, and the whole point of this split is that they are
    resolved differently.
    """
    _, modified = await _run_git("diff", "--name-only", repo_dir=repo_dir)
    _, staged = await _run_git("diff", "--name-only", "--cached", repo_dir=repo_dir)
    _, untracked = await _run_git(
        "ls-files", "--others", "--exclude-standard", repo_dir=repo_dir
    )
    return DirtyState(
        modified=_lines(modified),
        staged=_lines(staged),
        untracked=_lines(untracked),
    )


async def incoming_paths(repo_dir: str = CONDOR_DIR, branch: str = "") -> list[str]:
    """Files the pending fast-forward would write, ``HEAD..origin/<branch>``.

    This is the half of the question that matters. "Is the tree clean" blocks
    forever on a checkout that is also a runtime working directory; "would the
    incoming commits clobber something local" is answerable, and is almost
    always no.
    """
    branch = branch or await get_current_branch(repo_dir)
    rc, out = await _run_git(
        "diff", "--name-only", f"HEAD..origin/{branch}", repo_dir=repo_dir
    )
    if rc != 0:
        # Unresolvable diff (no remote ref, shallow clone): treat every dirty
        # path as potentially conflicting rather than waving the update through.
        logger.warning("Could not diff HEAD..origin/%s in %s", branch, repo_dir)
        return []
    return list(_lines(out))


async def ahead_count(repo_dir: str = CONDOR_DIR, branch: str = "") -> int:
    """How many local commits are not on ``origin/<branch>``.

    Non-zero means the checkout diverged and cannot be fast-forwarded; saying
    so beats producing a merge commit from a Telegram button.
    """
    branch = branch or await get_current_branch(repo_dir)
    rc, out = await _run_git(
        "rev-list", "--count", f"origin/{branch}..HEAD", repo_dir=repo_dir
    )
    if rc != 0 or not out.strip().isdigit():
        return 0
    return int(out.strip())


async def is_git_repo(repo_dir: str) -> bool:
    """Whether ``repo_dir`` is inside a git work tree."""
    if not os.path.isdir(repo_dir):
        return False
    rc, out = await _run_git("rev-parse", "--is-inside-work-tree", repo_dir=repo_dir)
    return rc == 0 and out.strip() == "true"


async def fast_forward(repo_dir: str = CONDOR_DIR) -> tuple[bool, str]:
    """Fetch and fast-forward to ``origin/<branch>``. Never merges.

    Replaces the old ``git pull``: an update means "take upstream's commits",
    and anything that is not a fast-forward is a state the admin has to look
    at, not something a button should resolve.
    """
    branch = await get_current_branch(repo_dir)

    # The sha we are leaving, so a successful move can report what it moved.
    before = await get_local_commit(repo_dir)

    ok, out = await fetch(repo_dir)
    if not ok:
        return False, f"Fetch failed:\n{out}"

    rc, output = await _run_git(
        "merge", "--ff-only", f"origin/{branch}", repo_dir=repo_dir
    )
    if rc != 0:
        return False, f"Fast-forward failed:\n{output}"

    # Version adoption telemetry (FEAT-023): two short shas and how far behind
    # this install had drifted. Only for the Condor repo itself, and a no-op
    # unless the admin opted in.
    try:
        after = await get_local_commit(repo_dir)
        if repo_dir == CONDOR_DIR and before and after and before != after:
            _, behind = await _run_git(
                "rev-list", "--count", f"{before}..{after}", repo_dir=repo_dir
            )
            from condor.telemetry import taps as telemetry_taps

            telemetry_taps.version_change(
                before, after, int(behind) if behind.strip().isdigit() else 0
            )
    except Exception:
        logger.debug("Could not record version change", exc_info=True)

    return True, output


# ---------------------------------------------------------------------------
# Resolutions: offered to the admin, never taken on their behalf
# ---------------------------------------------------------------------------


async def discard_paths(repo_dir: str, paths: list[str]) -> tuple[bool, str]:
    """Throw away local work on exactly ``paths``, and nothing else.

    Tracked paths go back to HEAD (index and worktree both, so a staged change
    does not survive), untracked ones are deleted. Destructive and unrecoverable
    -- the surface offering this must confirm first.
    """
    if not paths:
        return True, "Nothing to discard."

    state = await dirty_state(repo_dir)
    tracked = set(state.tracked)
    untracked = set(state.untracked)

    to_checkout = [p for p in paths if p in tracked]
    to_clean = [p for p in paths if p in untracked]

    messages = []
    if to_checkout:
        rc, out = await _run_git(
            "checkout", "HEAD", "--", *to_checkout, repo_dir=repo_dir
        )
        if rc != 0:
            return False, f"Could not restore {len(to_checkout)} file(s):\n{out}"
        messages.append(f"Restored {len(to_checkout)} tracked file(s).")
    if to_clean:
        rc, out = await _run_git("clean", "-fd", "--", *to_clean, repo_dir=repo_dir)
        if rc != 0:
            return False, f"Could not remove {len(to_clean)} file(s):\n{out}"
        messages.append(f"Removed {len(to_clean)} untracked file(s).")

    return True, " ".join(messages) or "Nothing to discard."


async def stash_paths(repo_dir: str, paths: list[str]) -> tuple[bool, str]:
    """Park local work on ``paths`` in a stash and report the ref back.

    Deliberately not popped afterwards: a pop that conflicts leaves a
    half-merged tree plus a stash entry nobody was told about. The admin gets
    the ref and decides.
    """
    if not paths:
        return True, "Nothing to stash."

    rc, out = await _run_git(
        "stash", "push", "-u", "-m", "condor /update", "--", *paths, repo_dir=repo_dir
    )
    if rc != 0:
        return False, f"Stash failed:\n{out}"

    _, ref = await _run_git("rev-parse", "--short", "stash@{0}", repo_dir=repo_dir)
    ref = ref.strip()
    if ref:
        return True, f"Stashed as stash@{{0}} ({ref}). Restore with: git stash pop"
    return True, out or "Stashed."


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
        "diff",
        "--name-only",
        f"{old_commit}..{new_commit}",
        "--",
        *paths,
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


async def npm_deps_stale(old_commit: str = "", new_commit: str = "") -> bool:
    """Whether ``frontend/node_modules`` has to be reinstalled before a build.

    "node_modules exists" answers "is *something* installed", not "is it
    current" — and after the first boot it is always true. A pull that adds a
    devDependency therefore left the install skipped, and since the bundle is
    built with ``tsc -b`` the new test files were type-checked against a
    dependency tree that never got it: the build failed on an import it could
    not resolve. Key off the manifest moving instead, which is the thing that
    actually invalidates the tree.

    Unknown commit range => :func:`paths_changed` returns True, so an
    unresolvable diff installs rather than skips.
    """
    if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
        return True
    return await paths_changed(
        old_commit,
        new_commit,
        "frontend/package.json",
        "frontend/package-lock.json",
    )


async def build_frontend(
    old_commit: str = "", new_commit: str = ""
) -> tuple[bool, str]:
    """Build the dashboard bundle, mirroring the Makefile's build-frontend target.

    Node usually lives under nvm rather than on the PATH Condor inherited, so
    source nvm.sh first exactly like the Makefile does.

    The commit range is the one the pull just moved through; it decides whether
    the JS dependencies are reinstalled first (see :func:`npm_deps_stale`).
    """
    if not os.path.isdir(FRONTEND_DIR):
        return True, "No frontend directory; skipped."

    install = "npm ci && " if await npm_deps_stale(old_commit, new_commit) else ""
    script = (
        'export NVM_DIR="$HOME/.nvm"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        'cd "$1" || exit 1; ' + install + "npm run build"
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
# Docker: what the container actually runs
# ---------------------------------------------------------------------------


async def compose_service(repo_dir: str, service: str) -> dict | None:
    """The fully resolved compose definition for one service, or None.

    ``docker compose config`` is the only thing that knows how this install
    produces the container: it merges every override file and expands the
    environment, so the answer it gives is the deployment's own, not a guess
    from reading ``docker-compose.yml`` by hand.
    """
    rc, out = await _run_cmd(
        "docker",
        "compose",
        "config",
        "--format",
        "json",
        cwd=repo_dir,
        timeout=60,
    )
    if rc != 0 or not out:
        logger.debug("docker compose config failed in %s: %s", repo_dir, out[:200])
        return None
    try:
        parsed = json.loads(out)
    except ValueError:
        logger.debug("docker compose config returned non-JSON in %s", repo_dir)
        return None
    services = parsed.get("services")
    if not isinstance(services, dict):
        return None
    definition = services.get(service)
    return definition if isinstance(definition, dict) else None


async def compose_mode(repo_dir: str, service: str) -> str:
    """How this install produces ``service``: ``source``, ``image`` or ``unknown``.

    A ``build:`` key means the image is built here, so an update is
    ``compose build``. No ``build:`` key means a published image is pulled, so
    an update is ``compose pull`` -- and the git checkout, whatever it says,
    has nothing to do with the version running inside the container.
    """
    definition = await compose_service(repo_dir, service)
    if definition is None:
        return "unknown"
    return "source" if definition.get("build") else "image"


async def local_image_digest(image_ref: str) -> str | None:
    """The registry digest of the local copy of ``image_ref``, or None.

    ``RepoDigests[0]`` is index-level when the image was pulled by tag, which
    makes it directly comparable to what the registry reports. An image loaded
    from a tarball has no RepoDigest at all -- that is "unknown", never
    "behind".
    """
    rc, out = await _run_cmd(
        "docker",
        "image",
        "inspect",
        image_ref,
        "--format",
        "{{json .RepoDigests}}",
        timeout=30,
    )
    if rc != 0 or not out:
        return None
    try:
        digests = json.loads(out)
    except ValueError:
        return None
    if not isinstance(digests, list) or not digests:
        return None
    first = str(digests[0])
    return first.split("@", 1)[1] if "@" in first else None


async def registry_image_digest(image_ref: str) -> str | None:
    """The index digest ``image_ref`` currently resolves to upstream, or None.

    ``buildx imagetools inspect`` is the only probe used. ``docker manifest
    inspect`` returns the *inner* per-platform manifests rather than the index,
    so it is not a drop-in fallback and a wrong comparison is worse than an
    honest "unknown".
    """
    rc, out = await _run_cmd(
        "docker",
        "buildx",
        "imagetools",
        "inspect",
        image_ref,
        "--format",
        "{{.Manifest.Digest}}",
        timeout=30,
    )
    if rc != 0:
        return None
    digest = (out or "").strip()
    return digest if digest.startswith("sha256:") else None


async def compose_pull(repo_dir: str, service: str) -> tuple[bool, str]:
    """Pull the published image for one service."""
    rc, output = await _run_cmd(
        "docker", "compose", "pull", service, cwd=repo_dir, timeout=DOCKER_TIMEOUT
    )
    if rc != 0:
        return False, output or "docker compose pull failed (no output)"
    return True, output


async def compose_build(repo_dir: str, service: str) -> tuple[bool, str]:
    """Rebuild one service's image from source."""
    rc, output = await _run_cmd(
        "docker", "compose", "build", service, cwd=repo_dir, timeout=DOCKER_TIMEOUT
    )
    if rc != 0:
        return False, output or "docker compose build failed (no output)"
    return True, output


async def compose_up(repo_dir: str) -> tuple[bool, str]:
    """Recreate the stack on whatever images are now on disk."""
    rc, output = await _run_cmd(
        "docker", "compose", "up", "-d", cwd=repo_dir, timeout=DOCKER_TIMEOUT
    )
    if rc != 0:
        return False, output or "docker compose up failed (no output)"
    return True, output


async def wait_healthy(
    url: str, timeout: float = 120, interval: float = 2
) -> tuple[bool, str]:
    """Poll ``url`` until it answers 200, or give up.

    ``compose up -d`` returns the moment the container is *created*, which is
    well before it serves. Without this a crash-looping container reports as a
    successful update.
    """
    import aiohttp

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = "no response"
    attempts = 0

    async with aiohttp.ClientSession() as session:
        while loop.time() < deadline:
            attempts += 1
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=interval * 2)
                ) as response:
                    if response.status == 200:
                        waited = int(timeout - (deadline - loop.time()))
                        return True, f"Healthy after {waited}s ({attempts} probes)."
                    last = f"HTTP {response.status}"
            except Exception as e:  # noqa: BLE001 - a refused connection is normal here
                last = type(e).__name__
            await asyncio.sleep(interval)

    return False, f"Not serving {url} after {int(timeout)}s (last: {last})."
