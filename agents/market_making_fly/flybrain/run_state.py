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


# Recorded in provenance.json for the audit trail, but not part of the
# signature a resume is checked against: a bug fix in the loop must not orphan
# a brain lineage. Settings, decoder, guard, dataset and circuit are.
# The shape of what ``state.json`` holds, and how the code reads it back.
# BUMP THIS whenever a persisted field is added, removed or reinterpreted:
# resuming an old run on code that reads its state differently is how a fixed
# bug comes back. ``session_high_net`` going from ``0.0`` to ``None`` did
# exactly that — a resumed run read its first reported figure as a drawdown
# from zero and halted.
STATE_VERSION = 1

# Recorded in provenance.json for the audit trail, but not signed: a bug fix
# in the loop must not orphan a brain lineage. What the code *means* by the
# persisted state is pinned by STATE_VERSION instead.
# ``state_version`` is compared directly in check_provenance, so it is kept
# out of the signature — inside it, re-signing would overwrite it and the
# comparison would never fire.
UNSIGNED_KEYS = ("source_sha256", "state_version")

# Cadence is the operator's to change mid-lineage: it moves no money and
# reinterprets no accounting. Everything else about the deployment is signed.
# Sizing, leverage, allocation and venue all denominate the anchor, the session
# high and the carried P&L that a resume restores, so changing one while
# restoring the other half of the books is how a run halts on a mix of two
# deployments' numbers. Changing them needs a new run_name.
UNSIGNED_SETTINGS = ("interval_sec",)


def signature(provenance: dict) -> str:
    signed = {k: v for k, v in provenance.items() if k not in UNSIGNED_KEYS}
    settings = signed.get("settings")
    if isinstance(settings, dict):
        signed["settings"] = {
            k: v for k, v in settings.items() if k not in UNSIGNED_SETTINGS
        }
    return hashlib.sha256(
        json.dumps(signed, sort_keys=True, default=str).encode()
    ).hexdigest()


class RunDir:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.events_path = self.root / "events.jsonl"
        self.latest_path = self.root / "latest.json"
        self.frame_path = self.root / "latest-input.png"
        self.activity_path = self.root / "latest-activity.json"
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

    def save_activity(self, counts: list[int]) -> None:
        """Spike counts at the cloud's neurons, for the report to colour by.

        Overwritten each observation rather than appended: it is a few thousand
        numbers, which would bury `events.jsonl` within an hour, and only the
        latest is ever drawn.
        """
        atomic_write_json(self.activity_path, counts)

    def load_activity(self) -> list[int] | None:
        if not self.activity_path.exists():
            return None
        return json.loads(self.activity_path.read_text())

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
            # Compared directly, never through the signature: re-signing the
            # recorded provenance below rewrites whatever the current rule
            # controls, which would make this check silently inert.
            recorded_version = recorded.get("state_version")
            if recorded_version != STATE_VERSION:
                raise RuntimeError(
                    f"Run state is version {recorded_version}, this code reads "
                    f"version {STATE_VERSION}; its persisted state would be "
                    "reinterpreted. Use a new run_name."
                )
            # Re-sign what was recorded under the current rule, so a change to
            # which keys are signed does not itself refuse every existing run.
            recorded_sig = signature(
                {k: v for k, v in recorded.items() if k != "signature"}
            )
            if recorded_sig != sig:
                raise RuntimeError(
                    "Run protocol changed (settings, decoder, dataset or source); "
                    "use a new run_name or review the migration explicitly"
                )
            return sig
        atomic_write_json(
            self.provenance_path,
            {"signature": sig, "state_version": STATE_VERSION, **provenance},
            indent=2,
            default=str,
        )
        return sig
