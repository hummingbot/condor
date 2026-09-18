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
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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


# A crashed git leaves .git/index.lock behind and every later subcommand fails
# on it. The raw error does reach output_tail, but it says "Another git process
# seems to be running" -- which sends the reader looking for a process that is
# not there. One line naming the file turns a dead end into a fix.
_LOCK_HINT = (
    "\nStale git lock: remove {repo}/.git/index.lock if no git process is "
    "actually running."
)


async def _run_git(*args: str, repo_dir: str = CONDOR_DIR) -> tuple[int, str]:
    """Run a git command in the given repo and return (returncode, stdout)."""
    rc, out = await _run_cmd("git", *args, cwd=repo_dir, timeout=GIT_TIMEOUT)
    if rc != 0 and "index.lock" in out:
        out += _LOCK_HINT.format(repo=repo_dir)
    return rc, out


async def get_local_commit(repo_dir: str = CONDOR_DIR) -> str:
    """Return the short hash of the current HEAD."""
    _, out = await _run_git("rev-parse", "--short", "HEAD", repo_dir=repo_dir)
    return out


async def get_local_commit_full(repo_dir: str = CONDOR_DIR) -> str:
    """Return the full hash of the current HEAD."""
    _, out = await _run_git("rev-parse", "HEAD", repo_dir=repo_dir)
    return out


async def get_current_branch(repo_dir: str = CONDOR_DIR) -> str:
    """Return the current branch name, or the literal ``HEAD`` when detached.

    ``rev-parse --abbrev-ref`` answers ``HEAD`` for a detached checkout rather
    than failing, and every caller downstream then treats that string as a
    branch name. Use :func:`is_detached` to ask the question this cannot
    answer.
    """
    _, out = await _run_git("rev-parse", "--abbrev-ref", "HEAD", repo_dir=repo_dir)
    return out


async def is_detached(repo_dir: str = CONDOR_DIR) -> bool:
    """Whether ``HEAD`` points at a commit rather than a branch.

    ``symbolic-ref`` is the only probe that distinguishes the two:
    ``rev-parse --abbrev-ref HEAD`` returns the *string* ``HEAD`` when detached,
    which reads as an ordinary branch name everywhere downstream. That is how a
    detached checkout came to fast-forward onto the remote's default branch --
    ``ahead_count(repo, "HEAD")`` resolves ``origin/HEAD``, finds nothing ahead,
    so no ``diverged`` block fires, and ``git merge --ff-only origin/HEAD``
    quietly succeeds and leaves the checkout detached.
    """
    rc, _ = await _run_git("symbolic-ref", "-q", "HEAD", repo_dir=repo_dir)
    return rc != 0


async def remote_default_branch(repo_dir: str = CONDOR_DIR) -> str | None:
    """The remote's default branch (e.g. ``main``), or ``None`` if unknown.

    Read from ``refs/remotes/origin/HEAD`` rather than hardcoded, so a repo that
    renames its default branch does not need a code change here. ``None`` when
    the ref is absent -- a clone made with ``--single-branch``, or one that has
    never run ``git remote set-head`` -- and callers must treat that as "cannot
    tell" rather than assuming anything.
    """
    rc, out = await _run_git(
        "symbolic-ref", "-q", "refs/remotes/origin/HEAD", repo_dir=repo_dir
    )
    if rc != 0 or not out:
        return None
    # refs/remotes/origin/main -> main
    return out.strip().rsplit("/", 1)[-1] or None


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


async def incoming_paths(
    repo_dir: str = CONDOR_DIR, branch: str = ""
) -> list[str] | None:
    """Files the pending fast-forward would write, ``HEAD..origin/<branch>``.

    This is the half of the question that matters. "Is the tree clean" blocks
    forever on a checkout that is also a runtime working directory; "would the
    incoming commits clobber something local" is answerable, and is almost
    always no.

    ``None`` means the question could not be answered -- no remote ref, a
    shallow clone, a broken checkout -- and is emphatically *not* the empty
    list. The empty list says "this update brings in nothing that could clash";
    ``None`` says "nobody knows what it brings in", and a caller that reads the
    second as the first waves through precisely the update it was asked to
    gate. Same convention as
    :func:`condor.updates.components.running_executor_count`.
    """
    branch = branch or await get_current_branch(repo_dir)
    rc, out = await _run_git(
        "diff", "--name-only", f"HEAD..origin/{branch}", repo_dir=repo_dir
    )
    if rc != 0:
        logger.warning("Could not diff HEAD..origin/%s in %s", branch, repo_dir)
        return None
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
        messages.append(
            f"Discarded your changes to {len(to_checkout)} tracked file(s); "
            "they are back to the committed version."
        )
    if to_clean:
        rc, out = await _run_git("clean", "-fd", "--", *to_clean, repo_dir=repo_dir)
        if rc != 0:
            return False, f"Could not remove {len(to_clean)} file(s):\n{out}"
        messages.append(f"Deleted {len(to_clean)} untracked file(s).")

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


async def stash_pop(repo_dir: str) -> tuple[bool, str]:
    """Put back the work ``stash_paths`` parked, if it still applies cleanly.

    Stashing was never meant to be the end of the story -- the whole point of
    parking work rather than discarding it is getting it back, and a clean pop
    is the common case once the fast-forward has landed. What the original
    caution was right about is the *failure*: a conflicting pop leaves a
    half-merged tree plus a stash entry nobody was told about. So a conflict
    aborts and says where the work still is, rather than leaving the operator
    to discover both.
    """
    _, listing = await _run_git("stash", "list", repo_dir=repo_dir)
    if "condor /update" not in listing:
        return True, "Nothing of ours was stashed."

    rc, out = await _run_git("stash", "pop", repo_dir=repo_dir)
    if rc != 0:
        return False, (
            f"{out}\n\nYour work is still in stash@{{0}} — nothing was lost. "
            "Resolve it by hand with: git stash pop"
        )
    return True, "Restored the work that was stashed before the update."


async def move_to_local_root(repo_dir: str, rel_paths: list[str]) -> tuple[bool, str]:
    """Move edited agent files into the gitignored local root, then reset stock.

    The non-lossy exit from a ``dirty-conflict``. FEAT-115 redirects writes made
    *through the product* into ``.condor/agents``, where they shadow stock per
    item and no update can touch them -- but a text editor knows nothing about
    that, and these are markdown files sitting in a git checkout, which is
    exactly what people open in one. Such an edit blocked the update, and the
    only one-click way out destroyed it.

    This puts the edit where the product would have put it: the customization
    survives and keeps being used, the tracked file fast-forwards normally, and
    the install ends up in the state FEAT-115 supports rather than a dead end.
    """
    from condor.paths import local_agents_root

    if not rel_paths:
        return True, "Nothing to move."

    moved: list[str] = []
    local_root = local_agents_root()
    for rel in rel_paths:
        source = Path(repo_dir) / rel
        if not source.is_file():
            continue
        # ``agents/scout/AGENT.md`` -> ``<local>/scout/AGENT.md``
        target = local_root / Path(rel).relative_to("agents")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        except (OSError, ValueError) as exc:
            return False, f"Could not move {rel}: {exc}"
        moved.append(rel)

    if not moved:
        return True, "Nothing to move."

    # The tracked copies go back to HEAD so the fast-forward is unobstructed.
    rc, out = await _run_git("checkout", "HEAD", "--", *moved, repo_dir=repo_dir)
    if rc != 0:
        return False, f"Moved your edits, but could not reset the tracked files:\n{out}"

    return True, (
        f"Kept your version of {len(moved)} file(s), moved into .condor/agents "
        "where updates leave them alone. The shipped copies will now "
        "fast-forward; your versions stay in use."
    )


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


def _explain_build_failure(rc: int, output: str) -> str:
    """Turn a build exit code into something the operator can act on.

    Three failures are common, look identical in the raw output, and have
    different remedies — so each gets named rather than all three arriving as
    "Frontend build failed".
    """
    base = output or "Frontend build failed (no output)"
    if "__CONDOR_NPM_CI_FAILED__" in base or rc == 90:
        return (
            base.replace("__CONDOR_NPM_CI_FAILED__", "").strip()
            + "\n\n`npm ci` failed, and it removes node_modules before it "
            "installs — so there is currently no dependency tree and the build "
            "could not have run. Fix the network or free some disk, then "
            "`cd frontend && npm ci`. The bundle on disk was not touched."
        )
    # 137 is SIGKILL, which for a vite build is almost always the memory
    # ceiling: WSL2 caps the VM by default and Docker Desktop caps it on macOS.
    # The process gets no chance to say so, so the output is unhelpfully empty.
    if rc in (137, -9):
        return (
            base + "\n\nThe build was killed (signal 9), which for a bundler is "
            "almost always the memory limit. On WSL2 raise `memory=` in "
            "`.wslconfig`; on macOS raise Docker Desktop's memory. The bundle "
            "on disk was not touched."
        )
    if rc == 124:
        return (
            base + "\n\nThe build timed out. On a checkout under /mnt/c on WSL2, or "
            "on Docker Desktop for macOS, this step can legitimately take "
            "several times longer than it does on Linux. Retrying is safe — "
            "`npm ci` and the build are both idempotent, and the bundle on "
            "disk was not touched."
        )
    return base


async def run_doctor() -> tuple[bool, str]:
    """Run Condor's own health check and report what it said.

    ``python -m condor.doctor`` already exists, is read-only, and exits non-zero
    on real failures -- and was wired only into ``make install``. Nothing in the
    update path ran it, so a broken boot was indistinguishable from a good one
    until somebody opened the dashboard. The contrast is the sharpest argument
    for it: the hummingbot-api path gets a health gate whose step is literally
    "wait for the API to answer", while Condor -- the component actually running
    the update -- got none.

    Never fatal. It runs after the code is already on disk, so failing the run
    on its verdict would report a completed update as a failed one.
    """
    script = (
        'export NVM_DIR="$HOME/.nvm"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        "uv run python -m condor.doctor"
    )
    rc, output = await _run_cmd(
        "bash", "-c", script, cwd=CONDOR_DIR, timeout=DEPS_TIMEOUT
    )
    return rc == 0, output or ("Doctor reported no output." if rc == 0 else "")


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

    stale = await npm_deps_stale(old_commit, new_commit)
    # `npm ci` deletes node_modules before it installs, so a registry blip or a
    # full disk part-way through leaves the install with no dependency tree at
    # all -- and then the build cannot run either. Say which of the two failed,
    # because the remedies are different.
    install = (
        'npm ci || { echo "__CONDOR_NPM_CI_FAILED__"; exit 90; }; ' if stale else ""
    )
    # Build into a scratch directory and swap. vite's defaults are
    # `outDir: "dist"` with `emptyOutDir: true`, so building in place emptied
    # the directory uvicorn was serving out of and every request during the
    # build returned an error -- and a build that then *failed* left the install
    # with no dashboard at all, while the run reported that the previous bundle
    # would come back. Exposure is now one rename, and dist.old is a free
    # instant rollback.
    script = (
        'export NVM_DIR="$HOME/.nvm"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        'cd "$1" || exit 1; ' + install + "rm -rf dist.new && "
        "npm run build -- --outDir dist.new --emptyOutDir && "
        "rm -rf dist.old && "
        "{ [ -d dist ] && mv dist dist.old || true; } && "
        "mv dist.new dist"
    )
    rc, output = await _run_cmd(
        "bash", "-c", script, "bash", FRONTEND_DIR, timeout=FRONTEND_BUILD_TIMEOUT
    )
    if rc != 0:
        return False, _explain_build_failure(rc, output)
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


def set_tmux_remain_on_exit(on: bool) -> None:
    """Toggle ``remain-on-exit`` on Condor's own tmux session, best effort.

    The Makefile turns this on for the startup probe and back off once the boot
    is confirmed, deliberately: a crash three hours later must still take the
    session down, or ``make status`` would report a dead Condor as running. That
    leaves an in-process restart with no such protection — the successor's own
    startup failure closes the pane and takes the traceback with it.

    So the same dance is repeated around the exec: on just before, off again
    once the new process has got far enough to serve. Silent when there is no
    tmux, which is the ordinary case for a foreground or containerized run.
    """
    session = os.environ.get("CONDOR_TMUX_SESSION", "condor")
    value = "on" if on else "off"
    for scope in ("set-option", "set-window-option"):
        try:
            result = subprocess.run(
                ["tmux", scope, "-t", session, "remain-on-exit", value],
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if result.returncode == 0:
            return


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
    # Keep the pane alive across the handover so the successor's own startup
    # failure is readable. main() turns it back off once it is serving.
    set_tmux_remain_on_exit(True)
    try:
        os.execv(python, [python] + sys.argv)
    except OSError:
        # A failed exec is the one way a restart can end with nothing running
        # and nothing said. The process image is still this one, but the event
        # loop is gone and the tmux pane is about to close, so a log line alone
        # can vanish with it -- hence a file as well, in the runtime root where
        # the next boot (and the operator) can find it.
        logger.exception("exec_restart failed; Condor is not coming back")
        _record_restart_failure()
        raise


def _record_restart_failure() -> None:
    """Leave the reason a restart died somewhere it will survive the pane."""
    import traceback
    from datetime import datetime, timezone

    try:
        from condor.paths import runtime_root

        path = runtime_root() / "restart-failure.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"{datetime.now(timezone.utc).isoformat()}\n"
            f"exec {sys.executable} {' '.join(sys.argv)}\n\n"
            f"{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - never mask the original failure
        logger.debug("Could not write restart-failure.log", exc_info=True)


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


async def registry_has_digest(image_ref: str, digest: str) -> bool | None:
    """Whether the registry holds ``digest`` for ``image_ref``'s repository.

    The only reliable way to tell an operator's own build from a published
    image. The obvious test -- "a built image has no ``RepoDigests``" -- does
    **not** work: Docker's containerd image store records a canonical digest for
    a build as well as for a pull, so a locally built image has a RepoDigest
    like any other, it simply is not one the registry has ever seen. Measured on
    29.8.1: pull, then ``docker build`` over the same tag, and RepoDigests is
    populated both times with different values. ``.Comment`` is no help either
    (both say ``buildkit.dockerfile.v0``, because the published image was itself
    built by buildkit in CI).

    Returns ``None`` when the question could not be answered, which callers must
    keep distinct from ``False`` -- an unreachable registry is not evidence that
    an image was built locally.
    """
    repo = image_ref.split("@", 1)[0].rsplit(":", 1)[0]
    rc, out = await _run_cmd(
        "docker",
        "buildx",
        "imagetools",
        "inspect",
        f"{repo}@{digest}",
        "--format",
        "{{.Manifest.Digest}}",
        timeout=30,
    )
    if rc == 0 and (out or "").strip().startswith("sha256:"):
        return True
    # "not found" is an answer; anything else is a failure to ask.
    if "not found" in (out or "").lower():
        return False
    return None


async def compose_pull(repo_dir: str, service: str) -> tuple[bool, str]:
    """Pull the published image for one service."""
    rc, output = await _run_cmd(
        "docker", "compose", "pull", service, cwd=repo_dir, timeout=DOCKER_TIMEOUT
    )
    if rc != 0:
        return False, output or "docker compose pull failed (no output)"
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
