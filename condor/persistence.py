"""
Safe pickle persistence with atomic writes, backup recovery,
and ephemeral key filtering.

Subclasses PTB's PicklePersistence to:
1. Write atomically (temp file → fsync → rename) so a crash mid-write
   never corrupts the main pickle.
2. Keep a .bak copy for recovery if the main file is unreadable.
3. Strip ephemeral/cache keys from user_data before serialization
   to prevent pickle bloat.
"""

import logging
import os
import pickle
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from telegram.ext import PicklePersistence
from telegram.ext._picklepersistence import _BotPickler, _BotUnpickler

logger = logging.getLogger(__name__)

# Keys that live in user_data at runtime but should NOT be persisted.
# They are rebuilt on demand (caches) or are large transient snapshots.
EPHEMERAL_KEYS = frozenset(
    {
        # Cache namespaces (rebuilt on demand, TTL-based)
        "_cache",
        "_cex_cache",
        "_bots_cache",
        "_executors_cache",
        "token_cache",
        # Portfolio snapshots (large data, rebuilt on /portfolio)
        "portfolio_balances",
        "portfolio_accounts_distribution",
        "portfolio_changes_24h",
        "portfolio_pnl_indicators",
    }
)


class SafePicklePersistence(PicklePersistence):
    """PicklePersistence with atomic writes and corruption recovery."""

    # ------------------------------------------------------------------
    # Write: atomic temp-file → fsync → rename
    # ------------------------------------------------------------------

    def _dump_singlefile(self) -> None:
        """Override to write atomically with a .bak safety net."""
        data = {
            "conversations": self.conversations,
            "user_data": self._strip_ephemeral(self.user_data),
            "chat_data": self.chat_data,
            "bot_data": self.bot_data,
            "callback_data": self.callback_data,
        }

        target = self.filepath
        tmp_fd = None
        tmp_path = None

        try:
            # 1. Write to a temp file in the same directory
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp",
                dir=str(target.parent),
            )
            with os.fdopen(tmp_fd, "wb") as f:
                tmp_fd = None  # os.fdopen takes ownership
                _BotPickler(self.bot, f, protocol=pickle.HIGHEST_PROTOCOL).dump(data)
                f.flush()
                os.fsync(f.fileno())

            # 2. Rotate current file to .bak (best-effort)
            bak_path = target.with_suffix(target.suffix + ".bak")
            if target.exists():
                try:
                    os.replace(str(target), str(bak_path))
                except OSError:
                    logger.warning("Failed to create backup of pickle file")

            # 3. Atomic rename of temp → target
            os.replace(tmp_path, str(target))
            tmp_path = None  # success – nothing to clean up

        except Exception:
            logger.exception("Failed to write persistence file")
            raise
        finally:
            # Clean up temp file on failure
            if tmp_fd is not None:
                os.close(tmp_fd)
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Load: fall back to .bak on corruption
    # ------------------------------------------------------------------

    def _load_singlefile(self) -> None:
        """Override to recover from .bak if main file is corrupt."""
        bak_path = self.filepath.with_suffix(self.filepath.suffix + ".bak")

        # Try main file first
        data = self._try_load(self.filepath)

        # Fall back to backup
        if data is None and bak_path.exists():
            logger.warning(
                "Main pickle unreadable, attempting recovery from %s", bak_path
            )
            data = self._try_load(bak_path)
            if data is not None:
                logger.info("Successfully recovered state from backup pickle")

        if data is not None:
            self.conversations = data.get("conversations", {})
            self.user_data = data.get("user_data", {})
            self.chat_data = data.get("chat_data", {})
            self.bot_data = data.get(
                "bot_data", self.context_types.bot_data()
            )
            self.callback_data = data.get("callback_data", None)
        else:
            # Both files missing or corrupt – start fresh
            if self.filepath.exists() or bak_path.exists():
                logger.error(
                    "Both pickle and backup are corrupt/unreadable. "
                    "Starting with empty state."
                )
            self.conversations = {}
            self.user_data = {}
            self.chat_data = {}
            self.bot_data = self.context_types.bot_data()
            self.callback_data = None

    def _try_load(self, path: Path) -> Optional[Dict[str, Any]]:
        """Attempt to load a pickle file, returning None on failure."""
        try:
            with path.open("rb") as f:
                return _BotUnpickler(self.bot, f).load()
        except (OSError, pickle.UnpicklingError, EOFError, Exception) as exc:
            logger.warning("Could not load %s: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Ephemeral key filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_ephemeral(
        user_data: Optional[Dict[int, Any]],
    ) -> Optional[Dict[int, Any]]:
        """Return a shallow copy of user_data with ephemeral keys removed.

        The original in-memory dict is NOT modified so cache keys remain
        available for the running session.
        """
        if not user_data:
            return user_data

        cleaned: Dict[int, Any] = {}
        for uid, data in user_data.items():
            if not isinstance(data, dict):
                cleaned[uid] = data
                continue

            # Only copy if there are keys to strip
            if EPHEMERAL_KEYS.isdisjoint(data.keys()):
                cleaned[uid] = data
            else:
                cleaned[uid] = {
                    k: v for k, v in data.items() if k not in EPHEMERAL_KEYS
                }
        return cleaned
