#!/usr/bin/env python3
"""Migrate to per-assistant memory/skill stores (FEAT-003).

Before FEAT-003 memory + skills were per-user and shared across the chat and all
of a user's trading agents, living under ``data/memory/user_{id}/``. FEAT-003
makes them per-assistant, co-located with each assistant's definition. By the
user's decision **every assistant starts empty**: this script does not copy the
old content — it archives it as a backup so nothing is lost.

What it does (idempotent):
- Move ``data/memory/user_*`` into ``data/memory/_archive_pre_FEAT003/``.
- Leave it there; do not delete. New per-assistant stores are created lazily on
  the first write, so there is nothing to seed.

Re-running is a no-op once the legacy stores have been archived.

Usage:
    uv run python scripts/migrate_to_per_assistant_stores.py [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_LEGACY_MEMORY = _PROJECT_ROOT / "data" / "memory"
_ARCHIVE = _LEGACY_MEMORY / "_archive_pre_FEAT003"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be archived without moving anything.",
    )
    args = parser.parse_args()

    if not _LEGACY_MEMORY.exists():
        print(f"Nothing to migrate: {_LEGACY_MEMORY} does not exist.")
        return 0

    legacy_stores = sorted(d for d in _LEGACY_MEMORY.glob("user_*") if d.is_dir())
    if not legacy_stores:
        print("Nothing to migrate: no legacy data/memory/user_* stores found.")
        return 0

    print(f"Found {len(legacy_stores)} legacy store(s) to archive:")
    for d in legacy_stores:
        print(f"  - {d.relative_to(_PROJECT_ROOT)}")

    if args.dry_run:
        print(f"\n[dry-run] Would archive into {_ARCHIVE.relative_to(_PROJECT_ROOT)}/")
        return 0

    _ARCHIVE.mkdir(parents=True, exist_ok=True)
    for d in legacy_stores:
        dest = _ARCHIVE / d.name
        if dest.exists():
            print(f"  skip (already archived): {d.name}")
            continue
        shutil.move(str(d), str(dest))
        print(f"  archived: {d.name} -> {dest.relative_to(_PROJECT_ROOT)}/")

    print(
        "\nDone. Assistants start empty; per-assistant stores are created lazily "
        "on the first write. The old content is preserved under "
        f"{_ARCHIVE.relative_to(_PROJECT_ROOT)}/ (not deleted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
