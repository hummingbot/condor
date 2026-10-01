"""Prepare, verify or benchmark the fly's connectome from inside the agent.

``prepare`` downloads the MaleCNS v1.0 release files (~1.1 GB), verifies their
checksums, compiles the retained graph into this agent's home and builds the
C++ kernel (needs ``c++``). ``verify`` re-checks the prepared arrays.
``bench`` loads the brain and times a few observations so ``fly_brain``'s
``neural_ms`` can be sized against its wall interval.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import asyncio
import logging
import time

import numpy as np
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Prepare (download + compile), verify, or bench the fly connectome."""

    action: str = Field(default="verify", description="prepare | verify | bench")
    observations: int = Field(
        default=3, ge=1, le=20, description="bench: observations to time (1–20)"
    )
    neural_ms: float = Field(
        default=500.0,
        ge=200.0,
        le=5000.0,
        description="bench: neural ms per observation (200–5000)",
    )


def _prepare() -> dict:
    from flybrain.data import prepare
    from flybrain.neural.brain import build

    prepare()
    return {"kernel": build()["model"]}


def _verify() -> dict:
    from flybrain.data import verify

    return verify()


def _bench(observations: int, neural_ms: float) -> dict:
    from flybrain.worker import FlyBrain

    started = time.perf_counter()
    brain = FlyBrain(learning=True)
    load = time.perf_counter() - started
    frame = np.full((180, 320, 3), 235, np.uint8)
    rows = []
    for _ in range(observations):
        result = brain.observe(frame, "none", neural_ms=neural_ms)
        rows.append(
            {
                "compute_seconds": round(result["compute_seconds"], 3),
                "total_spikes": result["total_spikes"],
                "kc_spikes": result["kc_spikes"],
                "arousal_hz": round(result["arousal_hz"], 3),
            }
        )
    return {"load_seconds": round(load, 2), "observations": rows}


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    from flybrain.deps import require_pyarrow
    from flybrain.neural.common import DATA

    # Every path from here reads a feather file. Check once, at the front, so
    # the answer is a command to run rather than an ImportError from inside the
    # vendored loader after a 1.1 GB download.
    require_pyarrow()
    if config.action not in ("prepare", "verify", "bench"):
        raise ValueError("action must be prepare, verify or bench")
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    if config.action == "prepare":
        result = await loop.run_in_executor(None, _prepare)
    elif config.action == "verify":
        result = await loop.run_in_executor(None, _verify)
    else:
        result = await loop.run_in_executor(
            None, _bench, config.observations, config.neural_ms
        )
    elapsed = time.perf_counter() - started

    builder = ReportBuilder(f"Fly setup — {config.action}")
    builder.source("routine", "fly_setup")
    builder.tags(["fly", "setup"])
    builder.manual_order()
    builder.section("01 / RESULT", f"{config.action} in {elapsed:.1f} s")
    builder.kpi("Data dir", str(DATA))
    for key, value in result.items():
        if key != "observations":
            builder.kpi(key, str(value))
    if config.action == "bench":
        builder.table(
            result["observations"],
            ["compute_seconds", "total_spikes", "kc_spikes", "arousal_hz"],
        )
    await builder.save()

    lines = [
        f"action: {config.action}",
        f"data_dir: {DATA}",
        f"elapsed_s: {elapsed:.1f}",
    ]
    lines += [f"{k}: {v}" for k, v in result.items() if k != "observations"]
    if config.action == "bench":
        lines += [
            f"obs{i}: compute={r['compute_seconds']}s kc={r['kc_spikes']} arousal={r['arousal_hz']}Hz"
            for i, r in enumerate(result["observations"])
        ]
    return "\n".join(lines)
