"""Boot migration onto the one runtime root (FEAT-051).

A self-hoster pulls and restarts; nobody tells them to run a script. FEAT-003
shipped ``scripts/migrate_to_per_assistant_stores.py`` and it is gone from the
tree, which is exactly the failure mode to avoid here — this moves a person's
chat history, so it cannot depend on anyone reading a release note.

:func:`ensure_migrated` is therefore called from ``startup()``, before anything
has read a conversation and before boot reconciliation. It moves
``condor/.runtime/{conversations,state,telemetry}`` to ``.condor/…``, with the
conversations re-keyed under ``users/{id}/conversations/``.

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
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from condor import paths

log = logging.getLogger(__name__)

MARKER_FILENAME = ".migrated-v1"


@dataclass
class MigrationReport:
    """What one boot's migration actually moved. Logged, and asserted in tests."""

    conversations: int = 0
    dropped_stubs: int = 0
    state: int = 0
    telemetry: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.conversations + self.state + self.telemetry


def ensure_migrated() -> MigrationReport:
    """Bring this install onto ``.condor/``. Safe to call on every boot."""
    root = paths.runtime_root()
    report = MigrationReport()

    if (root / MARKER_FILENAME).is_file():
        return report

    try:
        _migrate_runtime_stores(report)
    except Exception:  # noqa: BLE001 - a failed migration must not block boot
        log.exception("Runtime migration failed; leaving the old layout in place")
        return report

    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / MARKER_FILENAME).write_text("FEAT-051\n", encoding="utf-8")
    except OSError:
        log.warning("Could not write the migration marker at %s", root, exc_info=True)

    if report.total or report.dropped_stubs:
        log.warning(
            "Runtime migrated to %s: %d conversations, "
            "%d state namespaces, %d telemetry files "
            "(%d empty conversation stubs dropped, %d already present)",
            root,
            report.conversations,
            report.state,
            report.telemetry,
            report.dropped_stubs,
            report.skipped,
        )
    return report


# ── moving ──


def _move(src: Path, dst: Path) -> bool:
    """Move ``src`` onto ``dst``, never over it. True when something moved.

    A rename within one filesystem — both trees are under the repo — with
    ``shutil.move`` as the fallback for the case where they are not.
    """
    if dst.exists():
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
