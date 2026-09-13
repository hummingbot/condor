"""A low-poly fly, built as meshes, for the run report.

Stonkfly's dashboard renders its fly with Three.js — WebGL into an offscreen
canvas, blitted to a 2D canvas with smoothing off for the pixel look. A Condor
report cannot carry a 586 KB ES module: ``ReportBuilder`` has no raw-HTML or
script escape hatch, and its markdown is sanitized. What it does carry is
Plotly, serialized through ``pio.to_html`` with frames intact — and a Plotly 3D
scene is natively drag-to-orbit, which is the affordance that matters here.

So the fly is ``Mesh3d`` geometry with a wing-flap animation, orbitable by
dragging, in the same palette as the chart the fly is shown. It is decoration
with one honest job: making it obvious at a glance which run you are looking at
and whether it is alive.
"""

from __future__ import annotations

import numpy as np

# The chart's palette, so the report and the fly's own input agree.
BODY = "#c8d4e8"
THORAX = "#8fa3c4"
EYE = "#c5254e"
WING = "#5ce0d8"
LIMB = "#4a5a78"
GROUND = "#0a0e16"
ACCENT = "#c9f24d"

FLAP_FRAMES = 10
FLAP_DEGREES = 34.0


def _ellipsoid(
    center: tuple[float, float, float],
    radii: tuple[float, float, float],
    n_u: int = 18,
    n_v: int = 11,
) -> tuple[np.ndarray, ...]:
    """A closed ellipsoid as (x, y, z, i, j, k) for ``Mesh3d``."""
    u = np.linspace(0, 2 * np.pi, n_u, endpoint=False)
    v = np.linspace(0, np.pi, n_v)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    x = radii[0] * np.sin(vv) * np.cos(uu) + center[0]
    y = radii[1] * np.sin(vv) * np.sin(uu) + center[1]
    z = radii[2] * np.cos(vv) + center[2]
    faces_i, faces_j, faces_k = [], [], []
    for a in range(n_u):
        b = (a + 1) % n_u  # wrap around the waist
        for c in range(n_v - 1):
            p0, p1 = a * n_v + c, b * n_v + c
            p2, p3 = b * n_v + c + 1, a * n_v + c + 1
            faces_i += [p0, p0]
            faces_j += [p1, p2]
            faces_k += [p2, p3]
    return (
        x.ravel(),
        y.ravel(),
        z.ravel(),
        np.array(faces_i),
        np.array(faces_j),
        np.array(faces_k),
    )


def _rotate_x(y: np.ndarray, z: np.ndarray, pivot: tuple[float, float], degrees: float):
    """Rotate (y, z) about a pivot — the wing hinge on the thorax."""
    rad = np.radians(degrees)
    cy, cz = pivot
    dy, dz = y - cy, z - cz
    return (
        cy + dy * np.cos(rad) - dz * np.sin(rad),
        cz + dy * np.sin(rad) + dz * np.cos(rad),
    )


def _mesh(xyzijk, color, opacity=1.0, name="", lighting=True):
    import plotly.graph_objects as go

    x, y, z, i, j, k = xyzijk
    return go.Mesh3d(
        x=x,
        y=y,
        z=z,
        i=i,
        j=j,
        k=k,
        color=color,
        opacity=opacity,
        name=name,
        flatshading=True,  # low-poly: no smoothing between faces
        hoverinfo="skip",
        showscale=False,
        lighting=(
            dict(ambient=0.45, diffuse=0.85, specular=0.22, roughness=0.75)
            if lighting
            else dict(ambient=1.0, diffuse=0.0, specular=0.0)
        ),
        lightposition=dict(x=120, y=80, z=160),
    )


def _wing(side: int, degrees: float):
    """One wing, hinged on the thorax and rotated by the flap angle."""
    # Swept back along the body and narrow, the way a resting fly holds them,
    # so the wings read as wings rather than as two discs over the thorax.
    x, y, z, i, j, k = _ellipsoid(
        center=(-0.46, side * 0.40, 0.34), radii=(0.60, 0.21, 0.025), n_u=16, n_v=9
    )
    y, z = _rotate_x(y, z, pivot=(side * 0.12, 0.24), degrees=side * degrees)
    return x, y, z, i, j, k


def _static_parts():
    """Everything that does not move: abdomen, thorax, head, eyes, legs."""
    import plotly.graph_objects as go

    parts = [
        _mesh(_ellipsoid((-0.72, 0, 0.02), (0.62, 0.30, 0.29)), BODY, name="abdomen"),
        _mesh(_ellipsoid((0.02, 0, 0.06), (0.40, 0.33, 0.32)), THORAX, name="thorax"),
        _mesh(_ellipsoid((0.56, 0, 0.10), (0.27, 0.27, 0.26)), BODY, name="head"),
        _mesh(
            _ellipsoid((0.66, 0.19, 0.17), (0.17, 0.13, 0.17), 12, 8), EYE, name="eye"
        ),
        _mesh(
            _ellipsoid((0.66, -0.19, 0.17), (0.17, 0.13, 0.17), 12, 8), EYE, name="eye"
        ),
    ]
    legs_x, legs_y, legs_z = [], [], []
    for hip, reach in ((0.26, 0.30), (-0.02, 0.02), (-0.30, -0.26)):
        for side in (1, -1):
            legs_x += [hip, hip + reach * 0.6, hip + reach, None]
            legs_y += [side * 0.24, side * 0.52, side * 0.66, None]
            legs_z += [-0.12, -0.48, -0.74, None]
    parts.append(
        go.Scatter3d(
            x=legs_x,
            y=legs_y,
            z=legs_z,
            mode="lines",
            line=dict(color=LIMB, width=5),
            hoverinfo="skip",
            showlegend=False,
            name="legs",
        )
    )
    # Antennae
    parts.append(
        go.Scatter3d(
            x=[0.70, 0.86, None, 0.70, 0.86],
            y=[0.10, 0.16, None, -0.10, -0.16],
            z=[0.30, 0.46, None, 0.30, 0.46],
            mode="lines",
            line=dict(color=LIMB, width=4),
            hoverinfo="skip",
            showlegend=False,
            name="antennae",
        )
    )
    return parts


def fly_figure(title: str = "", subtitle: str = "", height: int = 420):
    """An orbitable low-poly fly whose wings beat when you press play."""
    import plotly.graph_objects as go

    static = _static_parts()
    angles = [
        FLAP_DEGREES * np.sin(2 * np.pi * n / FLAP_FRAMES) for n in range(FLAP_FRAMES)
    ]
    wing_traces = [
        _mesh(_wing(1, angles[0]), WING, opacity=0.34, name="wing"),
        _mesh(_wing(-1, angles[0]), WING, opacity=0.34, name="wing"),
    ]
    fig = go.Figure(data=static + wing_traces)

    wing_index = [len(static), len(static) + 1]
    fig.frames = [
        go.Frame(
            name=f"f{n}",
            traces=wing_index,
            data=[
                _mesh(_wing(1, angle), WING, opacity=0.34),
                _mesh(_wing(-1, angle), WING, opacity=0.34),
            ],
        )
        for n, angle in enumerate(angles)
    ]

    hidden = dict(
        showbackground=False,
        showgrid=False,
        zeroline=False,
        showticklabels=False,
        title="",
        visible=False,
    )
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=40 if title else 8, b=8),
        paper_bgcolor=GROUND,
        plot_bgcolor=GROUND,
        font=dict(color=BODY, family="monospace", size=11),
        title=(
            dict(text=title, x=0.02, xanchor="left", font=dict(color=ACCENT, size=13))
            if title
            else None
        ),
        showlegend=False,
        scene=dict(
            xaxis=hidden,
            yaxis=hidden,
            zaxis=hidden,
            bgcolor=GROUND,
            aspectmode="data",
            camera=dict(eye=dict(x=1.30, y=-1.50, z=0.80)),
            annotations=(
                [
                    dict(
                        showarrow=False,
                        x=0,
                        y=0,
                        z=-1.15,
                        text=subtitle,
                        font=dict(color=LIMB, size=11, family="monospace"),
                    )
                ]
                if subtitle
                else []
            ),
        ),
        updatemenus=[
            dict(
                type="buttons",
                showactive=False,
                x=0.01,
                y=0.03,
                xanchor="left",
                yanchor="bottom",
                bgcolor="#141c28",
                bordercolor=LIMB,
                font=dict(color=ACCENT, size=10, family="monospace"),
                buttons=[
                    dict(
                        label="▶ BEAT WINGS",
                        method="animate",
                        args=[
                            None,
                            dict(
                                frame=dict(duration=55, redraw=True),
                                fromcurrent=True,
                                transition=dict(duration=0),
                                mode="immediate",
                            ),
                        ],
                    ),
                    dict(
                        label="❚❚ PAUSE",
                        method="animate",
                        args=[
                            [None],
                            dict(
                                frame=dict(duration=0, redraw=False),
                                mode="immediate",
                            ),
                        ],
                    ),
                ],
            )
        ],
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    return fig
