"""The connectome, drawn, and coloured by what just fired.

The anatomy comes from :mod:`flybrain.cloud` — real soma coordinates from the
MaleCNS release, not a layout invented to look plausible. The colour comes from
the spike counts the last observation actually produced, so the mushroom body
lighting up is the mushroom body having fired, not an illustration of the idea.
"""

from __future__ import annotations

import numpy as np
from flybrain.cloud import GROUPS, load, orient
from flybrain.fly3d import GROUND, LIMB

# Silent slate through to a hot filament: a neuron that did nothing should
# recede into the anatomy, and one that fired hard should be the brightest
# thing on the panel.
ACTIVITY_SCALE = [
    [0.0, "#1b2536"],
    [0.12, "#2f4a6d"],
    [0.35, "#4f8fd0"],
    [0.62, "#ff9a54"],
    [0.85, "#ffd166"],
    [1.0, "#fff6e0"],
]

# Used only when a run has no activity snapshot: colour by what a cell is
# rather than by what it did.
GROUP_COLORS = {
    "mushroom body": "#ff9a54",
    "dopamine": "#ff4d6d",
    "memory output": "#ffd166",
    "readout": "#c9f24d",
    "descending": "#7ee0ff",
    "visual": "#5aa0ff",
    "other": "#55637d",
}


def brain_figure(
    activity: list[int] | None = None,
    title: str = "",
    height: int = 460,
):
    """The somata of the retained graph, orbitable, coloured by firing."""
    import plotly.graph_objects as go

    cloud = load()
    points = orient(cloud["xyz"])
    group = cloud["group"]
    hidden = dict(
        showbackground=False,
        showgrid=False,
        zeroline=False,
        showticklabels=False,
        visible=False,
    )
    layout = dict(
        height=height,
        margin=dict(l=0, r=0, t=36 if title else 6, b=6),
        paper_bgcolor=GROUND,
        plot_bgcolor=GROUND,
        font=dict(color="#c8d4e8", family="monospace", size=11),
        title=(
            dict(
                text=title, x=0.02, xanchor="left", font=dict(color="#c9f24d", size=13)
            )
            if title
            else None
        ),
        scene=dict(
            xaxis=hidden,
            yaxis=hidden,
            zaxis=hidden,
            bgcolor=GROUND,
            aspectmode="data",
            camera=dict(eye=dict(x=0, y=-1.15, z=0.22), up=dict(x=0, y=0, z=1)),
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.02,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
        ),
    )

    if activity is not None and len(activity) == len(group):
        rate = np.asarray(activity, dtype=float)
        firing = rate > 0
        ceiling = float(np.percentile(rate[firing], 97)) if firing.any() else 1.0
        shade = np.clip(rate / (ceiling or 1.0), 0, 1)
        # Silent and firing are drawn separately on purpose. Nineteen cells in
        # twenty are silent in a given window, and running them through the
        # same colour scale turns the anatomy into a bright haze that buries
        # the few cells that actually did something.
        quiet = go.Scatter3d(
            x=points[~firing, 0],
            y=points[~firing, 1],
            z=points[~firing, 2],
            mode="markers",
            marker=dict(size=1.1, color="#1d2738", opacity=0.5),
            hoverinfo="skip",
            name="silent",
            showlegend=False,
        )
        order = np.argsort(shade[firing])  # brightest drawn last
        live = go.Scatter3d(
            x=points[firing][order, 0],
            y=points[firing][order, 1],
            z=points[firing][order, 2],
            mode="markers",
            marker=dict(
                size=2.4 + 4.2 * shade[firing][order],
                color=shade[firing][order],
                colorscale=ACTIVITY_SCALE,
                cmin=0,
                cmax=1,
                opacity=0.95,
                showscale=False,
            ),
            text=[
                f"{GROUPS[g]} · {int(c)} spikes"
                for g, c in zip(group[firing][order], rate[firing][order])
            ],
            hoverinfo="text",
            name="firing",
            showlegend=False,
        )
        fig = go.Figure([quiet, live])
        fig.update_layout(showlegend=False, **layout)
        return fig

    # No activity recorded for this run: show what each cell is instead.
    traces = []
    for slot, name in enumerate(GROUPS):
        picked = group == slot
        if not picked.any():
            continue
        traces.append(
            go.Scatter3d(
                x=points[picked, 0],
                y=points[picked, 1],
                z=points[picked, 2],
                mode="markers",
                marker=dict(
                    size=1.6 if name in ("visual", "other") else 3.0,
                    color=GROUP_COLORS[name],
                    opacity=0.55 if name in ("visual", "other") else 0.95,
                ),
                name=name,
                hoverinfo="name",
            )
        )
    fig = go.Figure(traces)
    fig.update_layout(showlegend=True, **layout)
    return fig


def coverage() -> tuple[int, int, int]:
    """``(drawn, mapped somata, retained neurons)`` — what the picture covers."""
    cloud = load()
    return len(cloud["index"]), cloud["mapped_total"], cloud["neurons_total"]


READOUT_TOP, READOUT_BOTTOM = 36, 64


def _note_y(height: int) -> float:
    """Paper-coordinate y that puts a note clear of the tick labels at any
    height. 56 px: the axis numbers sit about 20 below the axis."""
    return -56 / max(1, height - READOUT_TOP - READOUT_BOTTOM)


def readout_figure(neural: dict, posture: dict | None = None, height: int = 460):
    """The three channels the posture is decoded from, as they were measured.

    DNp20 left against DNp20 right is the whole of the lean: the posture reads
    their difference, not either one. The descending population mean is the
    arousal that sets spread width. The gate is a count, not a rate, so it is
    stated rather than drawn — a bar of 4 next to a bar of 38 Hz would invite
    a comparison that means nothing.
    """
    import plotly.graph_objects as go

    left = float(neural.get("left_hz") or 0.0)
    right = float(neural.get("right_hz") or 0.0)
    arousal = float(neural.get("arousal_hz") or 0.0)
    posture = posture or {}
    labels = ["DNp20 left", "DNp20 right", "descending mean"]
    values = [left, right, arousal]
    colors = ["#5aa0ff", "#7ee0ff", "#ff9a54"]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker=dict(color=colors),
            text=[f"{v:.1f} Hz" for v in values],
            textposition="outside",
            textfont=dict(color="#c8d4e8", size=11),
            hoverinfo="skip",
        )
    )
    gate = neural.get("gate_spikes")
    trend = right - left
    fig.update_layout(
        height=height,
        margin=dict(l=96, r=20, t=READOUT_TOP, b=READOUT_BOTTOM),
        paper_bgcolor=GROUND,
        plot_bgcolor=GROUND,
        font=dict(color="#c8d4e8", family="monospace", size=11),
        title=dict(
            text="READOUT", x=0.02, xanchor="left", font=dict(color="#c9f24d", size=13)
        ),
        xaxis=dict(
            title="firing rate (Hz)",
            gridcolor="#18202e",
            zerolinecolor="#243044",
            range=[0, max(values + [1]) * 1.28],
        ),
        yaxis=dict(gridcolor="#18202e", automargin=True),
        showlegend=False,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        annotations=[
            dict(
                x=0,
                y=_note_y(height),
                xref="paper",
                yref="paper",
                showarrow=False,
                align="left",
                xanchor="left",
                font=dict(color=LIMB, size=11, family="monospace"),
                text=(
                    f"right − left = {trend:+.1f} Hz"
                    + (
                        f"  (z {posture['trend_z']:+.2f})"
                        if "trend_z" in posture
                        else ""
                    )
                    + f"<br>arousal z {posture.get('arousal_z', float('nan')):+.2f}"
                    if "arousal_z" in posture
                    else f"right − left = {trend:+.1f} Hz"
                ),
            ),
            dict(
                x=1,
                y=_note_y(height),
                xref="paper",
                yref="paper",
                showarrow=False,
                align="right",
                xanchor="right",
                font=dict(
                    color="#c9f24d" if gate else LIMB, size=11, family="monospace"
                ),
                text=f"DNpe017 gate: {gate if gate is not None else '—'} spike(s)"
                + ("" if gate else " — no lean without it"),
            ),
        ],
    )
    return fig
