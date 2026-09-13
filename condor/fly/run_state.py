"""One fly run's durable state on disk.

Stonkfly's pattern: a lock so two workers never share a run directory, two
alternating brain checkpoints so a crash mid-write leaves the previous one
intact, an append-only ``events.jsonl`` audit trail, ``latest.json`` and
``latest-input.png`` for "what did the fly just see and do", and a provenance
signature that refuses to resume a run under a changed protocol.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from condor.fsutil import atomic_write_bytes, atomic_write_json

FLY_PACKAGE = Path(__file__).resolve().parent


def source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(FLY_PACKAGE)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(FLY_PACKAGE.rglob("*"))
        if path.suffix in (".py", ".cpp") and path.is_file()
    }


def signature(provenance: dict) -> str:
    return hashlib.sha256(
        json.dumps(provenance, sort_keys=True, default=str).encode()
    ).hexdigest()


class RunDir:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.events_path = self.root / "events.jsonl"
        self.latest_path = self.root / "latest.json"
        self.frame_path = self.root / "latest-input.png"
        self.provenance_path = self.root / "provenance.json"
        self.lock_path = self.root / "worker.lock"
        self.stop_path = self.root / "STOP"
        self._lock = None

    # -- exclusivity ---------------------------------------------------------

    def lock(self) -> None:
        handle = self.lock_path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError(f"A fly worker already owns {self.root}")
        self._lock = handle

    def unlock(self) -> None:
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    # -- state ---------------------------------------------------------------

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict) -> None:
        atomic_write_json(self.state_path, state, indent=2, default=str)

    def append_event(self, row: dict) -> None:
        with self.events_path.open("a") as handle:
            handle.write(json.dumps(row, allow_nan=False, default=str) + "\n")

    def write_latest(self, row: dict) -> None:
        atomic_write_json(self.latest_path, row, indent=2, default=str)

    def save_frame(self, frame: np.ndarray) -> None:
        Image.fromarray(frame).save(self.frame_path)

    def recent_events(self, limit: int) -> list[dict]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text().splitlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    # -- checkpoints ---------------------------------------------------------

    def checkpoint_path(self, tick: int) -> Path:
        return self.root / f"brain-{tick % 2}.npz"

    def verify_checkpoint(self, info: dict) -> Path:
        path = self.root / info["file"]
        if not path.exists():
            raise RuntimeError(
                f"Checkpoint {path.name} recorded in state.json is missing"
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
            raise RuntimeError(f"Checkpoint {path.name} integrity mismatch")
        return path

    # -- provenance ----------------------------------------------------------

    def check_provenance(self, provenance: dict) -> str:
        """Write the run's provenance on first start; refuse a changed one."""
        sig = signature(provenance)
        if self.provenance_path.exists():
            recorded = json.loads(self.provenance_path.read_text())
            if recorded.get("signature") != sig:
                raise RuntimeError(
                    "Run protocol changed (settings, decoder, dataset or source); "
                    "use a new run_name or review the migration explicitly"
                )
            return sig
        atomic_write_json(
            self.provenance_path,
            {"signature": sig, **provenance},
            indent=2,
            default=str,
        )
        return sig
