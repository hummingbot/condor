"""Opt-in full-connectome checks — stonkfly's integration test transposed.

Needs the prepared dataset (``python -m condor.fly prepare``) and about a
minute: ``CONDOR_FLY_FULL_TEST=1 uv run pytest tests/test_fly_full_graph.py``.
"""

import os
from pathlib import Path

import numpy as np
import pytest

# The suite isolates CONDOR_RUNTIME_ROOT into a temp dir; the prepared
# connectome lives in the real one unless CONDOR_FLY_DATA says otherwise.
os.environ.setdefault(
    "CONDOR_FLY_DATA",
    str(Path(__file__).resolve().parent.parent / ".condor" / "fly" / "data"),
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CONDOR_FLY_FULL_TEST") != "1",
    reason="Uses the full MaleCNS graph; set CONDOR_FLY_FULL_TEST=1",
)


def test_chart_reaches_kenyon_cells_and_pulses_hit_dopamine_cells(tmp_path):
    from condor.fly.chart import market_frame
    from condor.fly.data import verify
    from condor.fly.market import FixtureMarket
    from condor.fly.worker import FlyBrain

    assert verify()["neurons"] == 166700
    brain = FlyBrain(learning=True)
    assert len(brain.brain.post) == 25582938
    assert len(brain.descending) > 1000 and len(brain.left) and len(brain.right)

    market = FixtureMarket(["XYZ:A-USD"], 72)
    import asyncio

    obs = asyncio.run(market.observe("XYZ:A-USD"))
    frame = market_frame(obs.pair, obs.candles, obs.bid, obs.ask)
    first = brain.observe(frame, "none")
    assert first["kc_spikes"] > 0, "the rendered chart must activate Kenyon cells"
    assert first["total_spikes"] > 0 and first["stimulus_ms"] == 0

    brain.save(tmp_path / "before.npz")
    before = brain.brain.weight[brain.brain.circuit["edges"]].copy()

    reward = brain.observe(frame, "reward")
    assert reward["reward_spikes"] > 0 and reward["stimulus_ms"] == 200
    assert reward["memory"]["changed_edges"] > 0
    rewarded = brain.brain.weight[brain.brain.circuit["edges"]].copy()

    brain.restore(tmp_path / "before.npz")
    brain.observe(frame, "none")
    assert not np.array_equal(
        rewarded, brain.brain.weight[brain.brain.circuit["edges"]]
    )

    brain.restore(tmp_path / "before.npz")
    brain.brain.weights_frozen = True
    brain.observe(frame, "reward")
    assert np.array_equal(before, brain.brain.weight[brain.brain.circuit["edges"]])

    brain.restore(tmp_path / "before.npz")
    brain.brain.weights_frozen = False
    loss = brain.observe(frame, "aversive")
    assert loss["aversive_spikes"] > 0 and loss["stimulus_ms"] == 200
    assert np.isfinite(brain.brain.weight).all()
