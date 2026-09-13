"""The brain lives here, in its own process.

``FlyBrain`` wraps stonkfly's ``VisualMemoryBrain`` and measures the three
channels the decoder reads. The module-level ``_init`` / ``_observe`` /
``_checkpoint`` / ``_restore`` functions are the ``ProcessPoolExecutor``
targets the ``fly_brain`` routine submits, so the C++ integration never runs on
Condor's event loop. One worker holds one brain; the routine creates the pool
with ``max_workers=1`` and the ``spawn`` context.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

# MaleCNS v1.0 superclass label of the 1,314 descending neurons in graph.npz.
DESCENDING_SUPERCLASS = "descending_neuron"


class FlyBrain:
    def __init__(
        self,
        learning: bool = True,
        neural_bin_ms: float = 10.0,
        pulse_ms: float = 200.0,
        pulse_current: float = 20.0,
    ):
        from flybrain.neural.common import annotations
        from flybrain.neural.visual import VisualMemoryBrain

        if neural_bin_ms <= 0 or neural_bin_ms > 10:
            raise ValueError("neural_bin_ms must be in (0, 10]")
        if pulse_ms <= 0 or pulse_current <= 0:
            raise ValueError("pulse_ms and pulse_current must be positive")
        self.neural_bin_ms = neural_bin_ms
        self.pulse_ms = pulse_ms
        self.pulse_current = pulse_current
        self.brain = VisualMemoryBrain()
        self.brain.weights_frozen = not learning
        a = annotations(self.brain.ids)
        types = a.type.fillna("")
        sides = a.somaSide.fillna("")
        self.left = np.flatnonzero(types.eq("DNp20") & sides.eq("L"))
        self.right = np.flatnonzero(types.eq("DNp20") & sides.eq("R"))
        self.gate = np.flatnonzero(types.eq("DNpe017"))
        if not len(self.left) or not len(self.right) or not len(self.gate):
            raise RuntimeError("Missing annotated DNp20 / DNpe017 readout cells")
        superclass = np.asarray(self.brain.superclass).astype(str)
        readouts = np.concatenate([self.left, self.right, self.gate])
        descending = np.flatnonzero(superclass == DESCENDING_SUPERCLASS)
        self.descending = np.setdiff1d(descending, readouts)
        if not len(self.descending):
            raise RuntimeError(
                f"No {DESCENDING_SUPERCLASS!r} superclass in graph.npz; "
                f"available: {sorted(set(superclass.tolist()))}"
            )
        # The report draws the connectome's somata; carrying their spike counts
        # back is what lets it colour the animal by what actually fired, rather
        # than by cell class alone. A few thousand ints per observation.
        from flybrain.cloud import load as load_cloud

        self.cloud_index = load_cloud()["index"].astype(np.int64)
        self.cell_ids = {
            "left": [str(self.brain.ids[i]) for i in self.left],
            "right": [str(self.brain.ids[i]) for i in self.right],
            "gate": [str(self.brain.ids[i]) for i in self.gate],
            "descending_count": int(len(self.descending)),
        }

    def observe(
        self, frame: np.ndarray, reinforcement: str, neural_ms: float = 500.0
    ) -> dict:
        if reinforcement not in ("none", "reward", "aversive"):
            raise ValueError("Unknown reinforcement")
        if not math.isfinite(neural_ms) or neural_ms < self.pulse_ms:
            raise ValueError("neural_ms must be finite and >= pulse_ms")
        frame = np.asarray(frame)
        if frame.shape != (180, 320, 3) or frame.dtype != np.uint8:
            raise ValueError("frame must be uint8[180, 320, 3]")
        b = self.brain
        counts = np.zeros(b.n, dtype=np.int32)
        wall = 0.0
        remaining = round(neural_ms / b.dt)
        pulse = round(self.pulse_ms / b.dt) if reinforcement != "none" else 0
        delivered = 0
        learning = not b.weights_frozen
        while remaining:
            n = min(remaining, round(self.neural_bin_ms / b.dt))
            if pulse:
                n = min(n, pulse)
            stimulus = (b.circuit[reinforcement], self.pulse_current) if pulse else None
            c, elapsed = b.rgb_step(
                frame, n * b.dt, learning=learning, stimulation=stimulus
            )
            counts += c
            wall += elapsed
            remaining -= n
            if pulse:
                delivered += n
                pulse -= n
        b.counts[:] = counts
        seconds = neural_ms / 1000
        left = float(np.mean(counts[self.left]) / seconds)
        right = float(np.mean(counts[self.right]) / seconds)
        return {
            "trend_hz": right - left,
            "left_hz": left,
            "right_hz": right,
            "arousal_hz": float(np.mean(counts[self.descending]) / seconds),
            "gate_spikes": int(counts[self.gate].sum()),
            # Over the whole network, not extrapolated from the drawn sample:
            # the report's cloud is stratified, so a fraction of it would not be
            # a fraction of the animal.
            "active_neurons": int((counts > 0).sum()),
            "mean_rate_hz": float(counts.mean() / seconds),
            "kc_spikes": int(counts[b.circuit["kc"]].sum()),
            "reward_spikes": int(counts[b.circuit["reward"]].sum()),
            "aversive_spikes": int(counts[b.circuit["aversive"]].sum()),
            "total_spikes": int(counts.sum()),
            "stimulus": reinforcement,
            "stimulus_ms": delivered * b.dt,
            # This observation's neural time, and the brain's total since it
            # was seeded. Reporting the second as the first says a 500 ms
            # observation ran for ten seconds once twenty of them have gone by.
            "observation_ms": float(neural_ms),
            "brain_ms": b.sim_ms,
            "compute_seconds": wall,
            "spike_sha256": hashlib.sha256(counts.tobytes()).hexdigest(),
            "input_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
            "memory": b.memory(),
            "cell_ids": self.cell_ids,
            "activity": counts[self.cloud_index].astype(int).tolist(),
        }

    def save(self, path: Path) -> None:
        self.brain.checkpoint(path)

    def restore(self, path: Path) -> None:
        self.brain.restore(path)

    def provenance(self) -> dict:
        return {
            "circuit": self.brain.circuit["report"],
            "vision": self.brain.visual_report,
            "readout": {
                "trend": "DNp20 mean right minus left rate",
                "arousal": f"mean rate of superclass={DESCENDING_SUPERCLASS!r} minus readouts",
                "gate": "DNpe017 spike count >= 1",
                "cells": self.cell_ids,
                "validated": False,
            },
        }


# ---- ProcessPoolExecutor targets (one brain per worker process) ----------

_BRAIN: FlyBrain | None = None


def _init(
    learning: bool, neural_bin_ms: float, pulse_ms: float, pulse_current: float
) -> None:
    global _BRAIN
    _BRAIN = FlyBrain(
        learning=learning,
        neural_bin_ms=neural_bin_ms,
        pulse_ms=pulse_ms,
        pulse_current=pulse_current,
    )


def _brain() -> FlyBrain:
    if _BRAIN is None:
        raise RuntimeError("worker not initialized")
    return _BRAIN


def _observe(frame: np.ndarray, reinforcement: str, neural_ms: float) -> dict:
    return _brain().observe(frame, reinforcement, neural_ms)


def _checkpoint(path: str) -> str:
    _brain().save(Path(path))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _restore(path: str) -> None:
    _brain().restore(Path(path))


def _provenance() -> dict:
    return _brain().provenance()
