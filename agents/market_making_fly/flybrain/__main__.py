"""``uv run python agents/market_making_fly/flybrain/__main__.py prepare | verify | bench``.

``prepare`` downloads the MaleCNS v1.0 release files (~1.1 GB), verifies their
checksums, compiles the retained graph and builds the C++ kernel. ``verify``
re-checks the prepared arrays. ``bench`` loads the brain and times a few
observations on a synthetic frame so ``fly_brain``'s ``neural_ms`` can be sized
against its wall interval.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m flybrain", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", help="download, verify and compile the connectome")
    sub.add_parser("verify", help="re-check the prepared connectome arrays")
    bench = sub.add_parser("bench", help="time observations on a synthetic frame")
    bench.add_argument("--observations", type=int, default=3)
    bench.add_argument("--neural-ms", type=float, default=500.0)
    args = parser.parse_args(argv)

    from flybrain.neural.common import DATA

    if args.command == "prepare":
        from flybrain.data import prepare

        print(f"Data directory: {DATA}", flush=True)
        prepare()
        from flybrain.neural.brain import build

        print(json.dumps({"kernel": build()["model"], "data": str(DATA)}))
        return 0
    if args.command == "verify":
        from flybrain.data import verify

        print(json.dumps({**verify(), "data": str(DATA)}))
        return 0
    if args.command == "bench":
        import numpy as np
        from flybrain.worker import FlyBrain

        started = time.perf_counter()
        brain = FlyBrain(learning=True)
        load = time.perf_counter() - started
        frame = np.full((180, 320, 3), 235, np.uint8)
        rows = []
        for _ in range(args.observations):
            result = brain.observe(frame, "none", neural_ms=args.neural_ms)
            rows.append(
                {
                    "compute_seconds": round(result["compute_seconds"], 3),
                    "total_spikes": result["total_spikes"],
                    "kc_spikes": result["kc_spikes"],
                }
            )
        print(
            json.dumps({"load_seconds": round(load, 2), "observations": rows}, indent=2)
        )
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
