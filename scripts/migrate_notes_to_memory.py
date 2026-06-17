"""One-shot migration: data/notes/chat_*.json -> user memory store.

Legacy notes were keyed by chat; user memory is keyed by user. For each
chat_{id}.json we resolve a user_id (chat_id itself when it is a known user —
the DM case — else the owner of the chat's default server) and create one
memory per key/value pair as type="reference".

Idempotent: a key whose slug already exists for that user is skipped. The
source JSON files are left in place as a backup.

Run with:  uv run python scripts/migrate_notes_to_memory.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a script from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

from condor.memory import MemoryStore  # noqa: E402
from condor.memory.store import _slugify  # noqa: E402

_NOTES_DIR = Path(__file__).parent.parent / "data" / "notes"


def _resolve_user_id(chat_id: int) -> int | None:
    """Map a notes chat_id to a user_id, or None if it can't be resolved."""
    from config_manager import get_config_manager

    cm = get_config_manager()

    # DM case: chat_id == user_id, and the user exists.
    if cm.get_user_role(chat_id) is not None:
        return chat_id

    # Group chat: fall back to the owner of the chat's default server.
    server = cm.get_chat_default_server(chat_id)
    if server:
        owner = cm.get_server_owner(server)
        if owner:
            return owner

    return None


def migrate() -> None:
    if not _NOTES_DIR.exists():
        print(f"No notes directory at {_NOTES_DIR} — nothing to migrate.")
        return

    files = sorted(_NOTES_DIR.glob("chat_*.json"))
    if not files:
        print("No chat_*.json note files found — nothing to migrate.")
        return

    total_migrated = 0
    total_skipped = 0

    for f in files:
        try:
            chat_id = int(f.stem.replace("chat_", ""))
        except ValueError:
            print(f"! Skipping {f.name}: cannot parse chat_id")
            continue

        try:
            notes = json.loads(f.read_text())
        except Exception as e:
            print(f"! Skipping {f.name}: cannot read JSON ({e})")
            continue

        if not isinstance(notes, dict) or not notes:
            print(f"- {f.name}: no notes, skipping")
            continue

        user_id = _resolve_user_id(chat_id)
        if user_id is None:
            print(
                f"! {f.name}: could not resolve a user_id for chat {chat_id}, skipping"
            )
            continue

        store = MemoryStore(user_id)
        for key, value in notes.items():
            slug = _slugify(key)
            if store.read(slug) is not None:
                print(f"  = user {user_id}: '{slug}' already exists, skipping")
                total_skipped += 1
                continue
            store.write(
                name=key,
                content=str(value),
                description=key,
                type="reference",
                source="chat",
            )
            print(f"  + user {user_id}: migrated '{key}' -> memory '{slug}'")
            total_migrated += 1

    print(
        f"\nDone. Migrated {total_migrated} note(s), skipped {total_skipped} existing."
    )
    print("Source JSON files left in place as backup.")


if __name__ == "__main__":
    migrate()
