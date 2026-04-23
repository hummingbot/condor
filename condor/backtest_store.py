"""Simple JSON file persistence for backtest results."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path("data")
_STORE_FILE = _DATA_DIR / "backtests.json"


class BacktestStore:
    """Persist backtest results to a local JSON file so they survive hummingbot-api restarts."""

    def __init__(self, path: Path = _STORE_FILE) -> None:
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}  # task_id -> result dict
        self._load()

    # ── public API ──

    def save_result(self, server: str, task_id: str, result: dict[str, Any]) -> None:
        self._data[task_id] = {"server": server, **result}
        self._persist()

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        return self._data.get(task_id)

    def list_results(self, server: str) -> list[dict[str, Any]]:
        return [
            {"task_id": tid, **entry}
            for tid, entry in self._data.items()
            if entry.get("server") == server
        ]

    def delete_result(self, task_id: str) -> bool:
        if task_id in self._data:
            del self._data[task_id]
            self._persist()
            return True
        return False

    # ── internals ──

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except Exception:
                logger.warning("Failed to load backtest store, starting fresh")
                self._data = {}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data))
        tmp.replace(self._path)


# Singleton
_store: BacktestStore | None = None


def get_backtest_store() -> BacktestStore:
    global _store
    if _store is None:
        _store = BacktestStore()
    return _store
