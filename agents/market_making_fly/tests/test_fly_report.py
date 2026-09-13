"""The run dashboard: the fly mesh, and the panels' own formatting."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from flybrain import fly3d


def _module(name):
    path = Path(__file__).resolve().parents[1] / "routines" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_mesh_face_indexes_a_real_vertex():
    """A Mesh3d with an out-of-range face renders nothing and says nothing."""
    x, y, z, i, j, k = fly3d._ellipsoid((0, 0, 0), (1, 0.5, 0.5), n_u=12, n_v=7)
    assert len(x) == len(y) == len(z) == 12 * 5 + 2  # 5 rings plus two poles
    assert len(i) == len(j) == len(k)
    for faces in (i, j, k):
        assert faces.min() >= 0 and faces.max() < len(x)
    assert np.isfinite(np.concatenate([x, y, z])).all()


def test_no_face_collapses_to_a_sliver():
    """Coincident pole vertices make zero-area triangles, which WebGL draws as
    a white sawtooth across the body even though a static export hides them."""
    x, y, z, i, j, k = fly3d._ellipsoid((0, 0, 0), (1, 0.5, 0.5), n_u=12, n_v=7)
    p = np.stack([x, y, z], axis=1)
    a, b, c = p[i], p[j], p[k]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    assert area.min() > 1e-9, "degenerate face in the mesh"
    # and each vertex is actually used, so nothing is left stranded
    assert set(np.concatenate([i, j, k]).tolist()) == set(range(len(x)))


def test_the_wings_actually_move():
    """The flap is the animation; identical frames would be a still image."""
    up = fly3d._wing(1, fly3d.FLAP_DEGREES)
    down = fly3d._wing(1, -fly3d.FLAP_DEGREES)
    assert not np.allclose(up[2], down[2])  # z differs
    # and the two wings are mirrored, not stacked on one side
    left, right = fly3d._wing(1, 0.0), fly3d._wing(-1, 0.0)
    assert left[1].mean() > 0 > right[1].mean()


def test_the_figure_animates_and_orbits():
    fig = fly3d.fly_figure(title="FLY.EXE", subtitle="XYZ:ORCL-USD")
    assert len(fig.frames) == fly3d.FLAP_FRAMES
    assert len(fig.data) >= 7  # abdomen, thorax, head, 2 eyes, legs, antennae, wings
    # every frame retargets exactly the two wing traces
    wing_traces = list(fig.frames[0].traces)
    assert len(wing_traces) == 2
    assert all(list(f.traces) == wing_traces for f in fig.frames)
    # a play button exists, and the scene is a 3D one (drag-to-orbit is native)
    assert fig.layout.updatemenus and fig.layout.updatemenus[0].buttons
    assert fig.layout.scene.camera.eye.x is not None


def test_numbers_are_formatted_or_visibly_absent():
    report = _module("fly_report")
    assert report._fmt(None) == "—"
    assert report._fmt(float("nan")) == "nan"
    assert report._fmt(1234.5678, 2) == "1,234.57"
    assert report._fmt(-0.5, 2, plus=True) == "-0.50"
    assert report._fmt(0.5, 2, plus=True) == "+0.50"
    assert report._clock(None) == "—"
    assert ":" in report._clock(1789279456.0)


def test_the_pnl_curve_only_plots_reported_ticks():
    """An unreported tick has no P&L to plot; charting its stale figure would
    draw a flat line that looks like a result."""
    report = _module("fly_report")
    events = [
        {"tick": 1, "equity": 1.0, "pnl_known": True},
        {"tick": 2, "equity": 9.9, "pnl_known": False},
        {"tick": 3, "equity": 2.0, "pnl_known": True},
    ]
    fig = report._pnl_figure(events)
    assert list(fig.data[0].x) == [1, 3] and list(fig.data[0].y) == [1.0, 2.0]
    assert report._pnl_figure(events[:1]) is None


def test_every_execution_status_has_a_word():
    report = _module("fly_report")
    for status in (
        "APPLIED",
        "SHADOW",
        "HOLD",
        "VETO",
        "CLOSED",
        "STOP_BOT",
        "ERROR",
        "HALT",
        "TICK_ERROR",
    ):
        assert report.RESULT_WORDS[status]
