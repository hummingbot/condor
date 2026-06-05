"""Per-file JSON persistence for backtest results.

Each backtest is stored as an individual JSON file under data/backtests/.
A lightweight index (_index.json) tracks task_id -> server mapping for fast listing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path("data") / "backtests"
_INDEX_FILE = _DATA_DIR / "_index.json"
_LEGACY_FILE = Path("data") / "backtests.json"


class BacktestStore:
    """Persist backtest results to individual JSON files."""

    def __init__(self, data_dir: Path = _DATA_DIR) -> None:
        self._dir = data_dir
        self._index_path = data_dir / "_index.json"
        self._index: dict[str, dict[str, str]] = {}  # task_id -> {server, ...light meta}
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_index()
        self._migrate_legacy()

    # -- public API --

    def save_result(self, server: str, task_id: str, result: dict[str, Any]) -> None:
        # Write full result to individual file
        self._write_file(task_id, {"server": server, **result})
        # Update index with lightweight metadata
        self._index[task_id] = {
            "server": server,
            "config": result.get("config", {}).get("id", ""),
        }
        self._persist_index()

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        if task_id not in self._index:
            return None
        return self._read_file(task_id)

    def list_results(self, server: str) -> list[dict[str, Any]]:
        results = []
        for tid, meta in self._index.items():
            if meta.get("server") == server:
                data = self._read_file(tid)
                if data:
                    results.append({"task_id": tid, **data})
        return results

    def delete_result(self, task_id: str) -> bool:
        if task_id not in self._index:
            return False
        del self._index[task_id]
        self._persist_index()
        path = self._task_path(task_id)
        if path.exists():
            path.unlink()
        return True

    # -- internals --

    def _task_path(self, task_id: str) -> Path:
        # Sanitize task_id for use as filename
        safe = task_id.replace("/", "_").replace("..", "_")
        return self._dir / f"{safe}.json"

    def _read_file(self, task_id: str) -> dict[str, Any] | None:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            logger.warning("Failed to read backtest file %s", path)
            return None

    def _write_file(self, task_id: str, data: dict[str, Any]) -> None:
        path = self._task_path(task_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)

    def _load_index(self) -> None:
        if self._index_path.exists():
            try:
                self._index = json.loads(self._index_path.read_text())
            except Exception:
                logger.warning("Failed to load backtest index, rebuilding")
                self._rebuild_index()

    def _persist_index(self) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index))
        tmp.replace(self._index_path)

    def _rebuild_index(self) -> None:
        """Rebuild index from individual files on disk."""
        self._index = {}
        for path in self._dir.glob("*.json"):
            if path.name == "_index.json":
                continue
            try:
                data = json.loads(path.read_text())
                task_id = path.stem
                self._index[task_id] = {
                    "server": data.get("server", ""),
                    "config": data.get("config", {}).get("id", "") if isinstance(data.get("config"), dict) else "",
                }
            except Exception:
                logger.warning("Skipping corrupt backtest file %s", path)
        self._persist_index()

    def _migrate_legacy(self) -> None:
        """Migrate from single backtests.json to per-file storage."""
        if not _LEGACY_FILE.exists():
            return
        try:
            legacy_data = json.loads(_LEGACY_FILE.read_text())
            if not isinstance(legacy_data, dict) or not legacy_data:
                _LEGACY_FILE.unlink()
                return
            logger.info("Migrating %d backtest results from legacy store", len(legacy_data))
            for task_id, entry in legacy_data.items():
                server = entry.pop("server", "")
                self._write_file(task_id, {"server": server, **entry})
                self._index[task_id] = {
                    "server": server,
                    "config": entry.get("config", {}).get("id", "") if isinstance(entry.get("config"), dict) else "",
                }
            self._persist_index()
            # Remove legacy file after successful migration
            _LEGACY_FILE.unlink()
            logger.info("Legacy backtest store migrated and removed")
        except Exception:
            logger.warning("Failed to migrate legacy backtest store", exc_info=True)


# Singleton
_store: BacktestStore | None = None


def get_backtest_store() -> BacktestStore:
    global _store
    if _store is None:
        _store = BacktestStore()
    return _store
