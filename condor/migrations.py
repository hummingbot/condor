"""Boot migrations: onto the one runtime root, then off the tracked agent tree.

A self-hoster pulls and restarts; nobody tells them to run a script. FEAT-003
shipped ``scripts/migrate_to_per_assistant_stores.py`` and it is gone from the
tree, which is exactly the failure mode to avoid here — this moves a person's
chat history, so it cannot depend on anyone reading a release note.

:func:`ensure_migrated` is therefore called from ``startup()``, before anything
has read a conversation and before boot reconciliation. It does two things:

1. ``condor/.runtime/{conversations,state,telemetry}`` → ``.condor/…``, with
   conversations re-keyed under ``users/{id}/conversations/``.
2. every ``agents/{slug}/delegations/{task_id}.*`` that records a ``user_id`` →
   ``.condor/users/{user_id}/delegations/{task_id}/``.

**Every step is independently idempotent**, and the ``.migrated-v1`` marker is
written last. So the marker is a fast path, not the correctness condition: a
run interrupted halfway finishes on the next boot, and a second boot on an
already-migrated tree changes nothing. A destination that already exists is
never overwritten — the worst realistic outcome is a record still readable from
its old location, not a lost one.

**One deliberate drop.** A conversation directory with no transcript at all and
``turn_count == 0`` is not migrated; the count is logged. Those are the stubs a
test suite wrote into the developer's install back when there was no single
root to repoint (812 of them in the install this shipped from). Nothing is lost
because nothing was there, and at this point in boot no session exists that
could be about to write its first turn.

**v2 (FEAT-115): out of the tracked agent tree.** ``agents/`` was a directory
git and the product both wrote to. The ``.migrated-v2`` pass empties the
product's half of it, in three steps that hold the same discipline as v1 — each
independently idempotent, a destination never overwritten, the marker last:

1. **Runtime artefacts move.** ``store/``, ``proposals/``, ``mutes.yml`` and,
   per strategy, ``learnings.md``, ``sessions/``, ``dry_runs/``, ``state.json``,
   ``config.yml`` and ``owned_bots.json`` go to ``.condor/agents/<slug>/...``.
   Pure moves: these were gitignored already, so no git is involved.
2. **Untracked agents move whole.** ``git ls-files`` *is* the definition of
   stock. An agent directory git tracks nothing of is this install's -- which is
   exactly what a ``.git/info/exclude``d agent looks like -- so it moves wholesale.
3. **Locally-modified tracked files are hoisted.** Each is copied to the local
   root with a ``forked_from`` stamp naming the HEAD revision it diverged from,
   then the checkout is restored. After this the operator's edit is still in
   force (local shadows stock) and ``git status --porcelain agents/`` is empty.

**Not a git checkout?** Steps 2 and 3 are skipped and everything stays stock.
That install has no update conflict to solve, so it has nothing to migrate --
and guessing at stock membership without git is the one way to get this wrong.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from condor import paths
from condor.agents.delegation_history import (
    DELEGATION_EVENTS_FILENAME,
    DELEGATION_STATUS_FILENAME,
    DELEGATION_TRANSCRIPT_FILENAME,
    EVENTS_SUFFIX,
    STATUS_SUFFIX,
)

log = logging.getLogger(__name__)

MARKER_FILENAME = ".migrated-v1"
MARKER_V2_FILENAME = ".migrated-v2"

# Runtime output that lived under a *tracked* agent directory. Everything here
# was already gitignored, so step 1 is a move with no git in it.
_AGENT_RUNTIME = ("store", "proposals", "mutes.yml")
_STRATEGY_RUNTIME = (
    "learnings.md",
    "sessions",
    "dry_runs",
    "state.json",
    "config.yml",
    "owned_bots.json",
)

# Old flat name -> new name inside the per-task delegation directory.
_DELEGATION_FILES = (
    (STATUS_SUFFIX, DELEGATION_STATUS_FILENAME),
    (".md", DELEGATION_TRANSCRIPT_FILENAME),
    (EVENTS_SUFFIX, DELEGATION_EVENTS_FILENAME),
)


@dataclass
class MigrationReport:
    """What one boot's migration actually moved. Logged, and asserted in tests."""

    conversations: int = 0
    dropped_stubs: int = 0
    state: int = 0
    telemetry: int = 0
    delegations: int = 0
    skipped: int = 0
    # v2 (FEAT-115)
    agent_artefacts: int = 0
    agent_dirs: int = 0
    agent_forks: int = 0

    @property
    def total(self) -> int:
        return (
            self.conversations
            + self.state
            + self.telemetry
            + self.delegations
            + self.agent_artefacts
            + self.agent_dirs
            + self.agent_forks
        )


def ensure_migrated(agents_root: Path | None = None) -> MigrationReport:
    """Bring this install onto ``.condor/``. Safe to call on every boot.

    ``agents_root`` is the *source* of v1's delegation step and of every v2 step,
    and it lives outside the runtime root, so it is a parameter and not a lookup:
    repointing ``$CONDOR_RUNTIME_ROOT`` alone would otherwise still let this walk
    the real ``agents/`` tree and move records out of it. Production passes
    nothing and gets :func:`condor.paths.stock_agents_root`.

    The two markers are independent. A box that already ran v1 still runs v2 on
    its next boot, and each marker stays a fast path rather than the correctness
    condition -- a run interrupted halfway finishes on the next boot.
    """
    root = paths.runtime_root()
    report = MigrationReport()
    source = Path(agents_root) if agents_root is not None else paths.stock_agents_root()

    if not (root / MARKER_FILENAME).is_file():
        try:
            _migrate_runtime_stores(report)
            _migrate_delegations(report, source)
        except Exception:  # noqa: BLE001 - a failed migration must not block boot
            log.exception("Runtime migration failed; leaving the old layout in place")
            return report
        _write_marker(root, MARKER_FILENAME, "FEAT-051")

    if not (root / MARKER_V2_FILENAME).is_file():
        try:
            _split_agent_tree(report, source)
        except Exception:  # noqa: BLE001 - same rule: never block a boot
            log.exception("Agent split failed; leaving the single root in place")
            return report
        _write_marker(root, MARKER_V2_FILENAME, "FEAT-115")

    if report.total or report.dropped_stubs:
        log.warning(
            "Runtime migrated to %s: %d conversations, %d delegations, "
            "%d state namespaces, %d telemetry files, %d agent artefacts, "
            "%d agent directories, %d hoisted forks "
            "(%d empty conversation stubs dropped, %d already present)",
            root,
            report.conversations,
            report.delegations,
            report.state,
            report.telemetry,
            report.agent_artefacts,
            report.agent_dirs,
            report.agent_forks,
            report.dropped_stubs,
            report.skipped,
        )
    return report


def _write_marker(root: Path, filename: str, note: str) -> None:
    """Written **last**, so it is a fast path and not the correctness condition."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / filename).write_text(f"{note}\n", encoding="utf-8")
    except OSError:
        log.warning("Could not write the migration marker at %s", root, exc_info=True)


# ── moving ──


def _move(src: Path, dst: Path) -> bool:
    """Move ``src`` onto ``dst``, never over it. True when something moved.

    A rename within one filesystem — both trees are under the repo — with
    ``shutil.move`` as the fallback for the case where they are not.

    A source that is not there is not an error: the v2 steps ask for a fixed set
    of runtime names per agent and most agents hold few of them, so "absent" is
    the common answer rather than the exceptional one. Checked before the
    ``mkdir`` below, which would otherwise leave an empty destination tree
    standing in for every artefact the install never wrote.
    """
    if not src.exists() or dst.exists():
        return False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError:
            shutil.move(str(src), str(dst))
        return True
    except OSError:
        log.warning("Could not migrate %s -> %s", src, dst, exc_info=True)
        return False


def _prune_if_empty(directory: Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass  # not empty, or already gone: both are fine


# ── step 1: the three runtime stores ──


def _migrate_runtime_stores(report: MigrationReport) -> None:
    legacy = paths.LEGACY_RUNTIME_ROOT
    if not legacy.is_dir():
        return

    _migrate_conversations(legacy / "conversations", report)
    _migrate_flat(legacy / "state", paths.runtime_root() / "state", report, "state")
    _migrate_flat(legacy / "telemetry", paths.telemetry_dir(), report, "telemetry")

    for name in ("conversations", "state", "telemetry"):
        _prune_if_empty(legacy / name)
    _prune_if_empty(legacy)


def _migrate_conversations(source: Path, report: MigrationReport) -> None:
    """``conversations/{user}/{conv}`` → ``users/{user}/conversations/{conv}``."""
    if not source.is_dir():
        return

    for user_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        try:
            user_id = paths.safe_id(user_dir.name)
        except paths.UnsafeIdError:
            log.warning("Skipping unrecognisable conversation owner %s", user_dir)
            continue

        for conv_dir in sorted(p for p in user_dir.iterdir() if p.is_dir()):
            try:
                conv_id = paths.safe_id(conv_dir.name)
            except paths.UnsafeIdError:
                continue
            if _is_empty_stub(conv_dir):
                shutil.rmtree(conv_dir, ignore_errors=True)
                report.dropped_stubs += 1
                continue
            if _move(conv_dir, paths.conversation_dir(user_id, conv_id)):
                report.conversations += 1
            else:
                report.skipped += 1

        _prune_if_empty(user_dir)


def _is_empty_stub(conv_dir: Path) -> bool:
    """A conversation that never held a turn: no transcript and none counted."""
    if (conv_dir / "transcript.jsonl").exists():
        return False
    if (conv_dir / "transcript_archive.jsonl").exists():
        return False
    try:
        meta = json.loads((conv_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False  # unreadable is not the same as empty; keep it
    return isinstance(meta, dict) and not meta.get("turn_count")


def _migrate_flat(
    source: Path, destination: Path, report: MigrationReport, field: str
) -> None:
    """Move each child of ``source`` into ``destination``, entry by entry.

    Per entry rather than by renaming the whole directory because the
    destination may already exist — a process that booted on the new build
    before the migration ran has a live telemetry spool there.
    """
    if not source.is_dir():
        return
    for child in sorted(source.iterdir()):
        if _move(child, destination / child.name):
            setattr(report, field, getattr(report, field) + 1)
        else:
            report.skipped += 1


# ── step 2: delegations, re-keyed by the user who asked ──


def _migrate_delegations(
    report: MigrationReport, agents_root: Path | None = None
) -> None:
    """``agents/{slug}/delegations/{task}.*`` → ``users/{id}/delegations/{task}/``."""
    root = Path(agents_root) if agents_root is not None else paths.stock_agents_root()
    if not root.is_dir():
        return

    for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        source = agent_dir / "delegations"
        if not source.is_dir():
            continue
        for status_path in sorted(source.glob(f"*{STATUS_SUFFIX}")):
            task_id = status_path.name[: -len(STATUS_SUFFIX)]
            user_id = _owner_of(status_path)
            if not user_id:
                continue  # belongs to nobody; read in place, forever
            try:
                target = paths.delegation_dir(user_id, task_id)
            except paths.UnsafeIdError:
                log.warning("Skipping unrecognisable delegation %s", status_path)
                continue
            if _move_delegation(source, task_id, target):
                report.delegations += 1
            else:
                report.skipped += 1
        _prune_if_empty(source)


def _owner_of(status_path: Path) -> str:
    """The ``user_id`` a delegation recorded, or '' when it recorded none."""
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    user_id = data.get("user_id") if isinstance(data, dict) else None
    return str(user_id) if user_id else ""


def _move_delegation(source: Path, task_id: str, target: Path) -> bool:
    """The three sidecars into one directory. True when the record moved."""
    if (target / DELEGATION_STATUS_FILENAME).exists():
        return False
    moved = False
    for suffix, new_name in _DELEGATION_FILES:
        if _move(source / f"{task_id}{suffix}", target / new_name):
            moved = True
    return moved


# ── v2: the agent tree splits in two (FEAT-115) ──


def _agent_dirs(root: Path):
    """Every agent directory of a root — never ``_shared``/``_defaults``."""
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in children if not p.name.startswith("_")]


def _split_agent_tree(report: MigrationReport, stock_root: Path) -> None:
    """Empty the product's half of the tracked agent tree, in three steps."""
    if not stock_root.is_dir():
        return

    local_root = paths.local_agents_root()
    _move_runtime_artefacts(report, stock_root, local_root)

    repo_dir = stock_root.parent
    if not (repo_dir / ".git").exists():
        # No git, so no way to tell stock from local and no update conflict to
        # solve either. Leaving it whole is the only answer that cannot be wrong.
        log.info("Agent split: %s is not a git checkout; steps 2-3 skipped", repo_dir)
        return

    prefix = stock_root.name
    _move_untracked_agents(report, stock_root, local_root, repo_dir, prefix)
    _hoist_modified_files(report, stock_root, local_root, repo_dir, prefix)


def _move_runtime_artefacts(
    report: MigrationReport, stock_root: Path, local_root: Path
) -> None:
    """Step 1: the gitignored output of running, out from under the library."""
    for agent_dir in _agent_dirs(stock_root):
        slug = agent_dir.name
        for name in _AGENT_RUNTIME:
            if _move(agent_dir / name, local_root / slug / name):
                report.agent_artefacts += 1

        strategies = agent_dir / "strategies"
        for strategy_dir in _agent_dirs(strategies) if strategies.is_dir() else []:
            destination = local_root / slug / "strategies" / strategy_dir.name
            for name in _STRATEGY_RUNTIME:
                if _move(strategy_dir / name, destination / name):
                    report.agent_artefacts += 1
            _prune_if_empty(strategy_dir)
        _prune_if_empty(strategies)


def _move_untracked_agents(
    report: MigrationReport,
    stock_root: Path,
    local_root: Path,
    repo_dir: Path,
    prefix: str,
) -> None:
    """Step 2: an agent git tracks nothing of is this install's, whole.

    That is exactly the shape of a ``.git/info/exclude``d agent, which is how the
    split was being maintained by hand before this existed. The exclude entries
    go stale rather than wrong once the directory is gone; removing them is a
    manual tidy-up, not something a boot migration should do to someone's
    ``.git/``.
    """
    for agent_dir in _agent_dirs(stock_root):
        slug = agent_dir.name
        tracked = _git(repo_dir, "ls-files", "--", f"{prefix}/{slug}")
        if tracked is None or tracked.strip():
            continue  # git knows it: stock, or unreadable — either way, leave it
        if _move(agent_dir, local_root / slug):
            report.agent_dirs += 1
            log.info("Agent split: %s was untracked and is now this install's", slug)
        else:
            report.skipped += 1


def _hoist_modified_files(
    report: MigrationReport,
    stock_root: Path,
    local_root: Path,
    repo_dir: Path,
    prefix: str,
) -> None:
    """Step 3: an operator's edit to a tracked file becomes an explicit fork.

    The edit stays in force -- local shadows stock -- and the checkout goes back
    to HEAD, which is what makes the *next* update boring. Restored with a plain
    ``git checkout``: ``utils.updater.discard_paths`` does the same restore but
    is a coroutine, and this runs before the event loop has anything else to do.
    """
    from condor.layering import stamp_fork

    changed = _git(repo_dir, "diff", "--name-only", "HEAD", "--", prefix)
    if not changed:
        return

    restored: list[str] = []
    for rel in sorted(set(changed.split("\n"))):
        rel = rel.strip()
        if not rel:
            continue
        source = repo_dir / rel
        try:
            inside = Path(rel).relative_to(prefix)
        except ValueError:
            continue
        if source.is_file():
            destination = local_root / inside
            if not destination.exists():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    stamp_fork(destination, _head_digest(repo_dir, rel))
                    report.agent_forks += 1
                    log.info("Agent split: hoisted the local edit to %s", rel)
                except OSError:
                    log.warning("Could not hoist %s", rel, exc_info=True)
                    continue
            else:
                report.skipped += 1
        restored.append(rel)

    if restored and _git(repo_dir, "checkout", "HEAD", "--", *restored) is None:
        log.warning("Could not restore %d agent file(s) to HEAD", len(restored))


def _head_digest(repo_dir: Path, rel: str) -> str:
    """``sha256:<12>`` of what HEAD holds for ``rel`` — the revision forked from."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return "sha256:unknown"
    return f"sha256:{hashlib.sha256(blob).hexdigest()[:12]}"


def _git(repo_dir: Path, *args: str) -> str | None:
    """Run one git command, returning stdout or ``None`` when it could not run."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        log.warning("git %s failed to start in %s", args[0], repo_dir, exc_info=True)
        return None
    if done.returncode != 0:
        log.warning("git %s failed: %s", args[0], done.stderr.strip())
        return None
    return done.stdout
