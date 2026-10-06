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

DESK = "#1b2432"
DESK_EDGE = "#2b3950"
BEZEL = "#151d29"
KEYBOARD = "#222c3d"
MUG = "#c5254e"
CITY = "#121a27"

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
    """A closed ellipsoid as (x, y, z, i, j, k) for ``Mesh3d``.

    Each pole is ONE vertex with a triangle fan around it. Sweeping v through
    0 and pi on a full u grid instead would place n_u coincident vertices at
    each pole, and the quads there collapse to zero-area slivers — which WebGL
    renders as a white sawtooth along the body, even though a static export
    hides it.
    """
    rings = np.linspace(0, np.pi, n_v)[1:-1]
    n_rings = len(rings)
    u = np.linspace(0, 2 * np.pi, n_u, endpoint=False)
    uu, vv = np.meshgrid(u, rings, indexing="ij")
    x = np.append(
        (radii[0] * np.sin(vv) * np.cos(uu) + center[0]).ravel(),
        [center[0], center[0]],
    )
    y = np.append(
        (radii[1] * np.sin(vv) * np.sin(uu) + center[1]).ravel(),
        [center[1], center[1]],
    )
    z = np.append(
        (radii[2] * np.cos(vv) + center[2]).ravel(),
        [center[2] + radii[2], center[2] - radii[2]],
    )
    north, south = n_u * n_rings, n_u * n_rings + 1
    faces_i, faces_j, faces_k = [], [], []
    for a in range(n_u):
        b = (a + 1) % n_u  # wrap around the waist
        faces_i += [north, south]
        faces_j += [b * n_rings, a * n_rings + n_rings - 1]
        faces_k += [a * n_rings, b * n_rings + n_rings - 1]
        for c in range(n_rings - 1):
            p0, p1 = a * n_rings + c, b * n_rings + c
            p2, p3 = b * n_rings + c + 1, a * n_rings + c + 1
            faces_i += [p0, p0]
            faces_j += [p1, p2]
            faces_k += [p2, p3]
    return (
        x,
        y,
        z,
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
            dict(ambient=0.66, diffuse=0.42, specular=0.10, roughness=0.90)
            if lighting
            else dict(ambient=1.0, diffuse=0.0, specular=0.0)
        ),
        lightposition=dict(x=-60, y=-140, z=180),
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
        _mesh(
            _ellipsoid((-0.72, 0, 0.02), (0.62, 0.30, 0.29), n_u=26, n_v=15),
            BODY,
            name="abdomen",
        ),
        _mesh(
            _ellipsoid((0.02, 0, 0.06), (0.40, 0.33, 0.32), n_u=24, n_v=14),
            THORAX,
            name="thorax",
        ),
        _mesh(
            _ellipsoid((0.56, 0, 0.10), (0.27, 0.27, 0.26), n_u=22, n_v=13),
            BODY,
            name="head",
        ),
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


def _box(x0, x1, y0, y1, z0, z1):
    """An axis-aligned box as (x, y, z, i, j, k)."""
    x = np.array([x0, x1, x1, x0, x0, x1, x1, x0], dtype=float)
    y = np.array([y0, y0, y1, y1, y0, y0, y1, y1], dtype=float)
    z = np.array([z0, z0, z0, z0, z1, z1, z1, z1], dtype=float)
    i = np.array([0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3])
    j = np.array([1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4])
    k = np.array([2, 3, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7])
    return x, y, z, i, j, k


def _cylinder(cx, cy, z0, z1, radius, sides=14):
    """A capped cylinder — the mug."""
    a = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    rim_x, rim_y = cx + radius * np.cos(a), cy + radius * np.sin(a)
    x = np.concatenate([rim_x, rim_x, [cx], [cx]])
    y = np.concatenate([rim_y, rim_y, [cy], [cy]])
    z = np.concatenate([np.full(sides, z0), np.full(sides, z1), [z0], [z1]])
    low_c, high_c = 2 * sides, 2 * sides + 1
    i, j, k = [], [], []
    for a0 in range(sides):
        a1 = (a0 + 1) % sides
        i += [a0, a0, low_c, high_c]
        j += [a1, a1 + sides, a1, a0 + sides]
        k += [a1 + sides, a0 + sides, a0, a1 + sides]
    return x, y, z, np.array(i), np.array(j), np.array(k)


def _quantize(rgb: np.ndarray, colors: int = 10):
    """Map the frame onto a small palette and return (index grid, palette).

    The chart is drawn from a fixed palette — background, header, grid, up,
    down, volume, text — so six colours already cover ~99 % of it and the rest
    is text antialiasing. That collapses to an exact stepped colorscale, which
    is how a Plotly surface can show the real chart rather than a stand-in.
    """
    # int32, not int16: a squared channel difference reaches 255**2 = 65025,
    # which wraps in int16 and silently assigns pixels the wrong colour —
    # rendering the chart with its background and header swapped.
    flat = rgb.reshape(-1, 3).astype(np.int32)
    unique, counts = np.unique(flat, axis=0, return_counts=True)
    palette = unique[np.argsort(-counts)[:colors]]
    # Nearest palette colour per pixel, in chunks so a full frame stays cheap.
    index = np.empty(len(flat), dtype=np.int32)
    for start in range(0, len(flat), 8192):
        chunk = flat[start : start + 8192]
        d = ((chunk[:, None, :] - palette[None, :, :]) ** 2).sum(-1)
        index[start : start + 8192] = d.argmin(1)
    return index.reshape(rgb.shape[:2]), palette


def _screen(
    rgb: np.ndarray,
    x: float,
    y0: float,
    y1: float,
    z0: float,
    z1: float,
    width: int = 104,
):
    """The monitor's glass: the fly's own input frame, as flat-shaded cells.

    Not a ``Surface``: that interpolates ``surfacecolor`` between grid points,
    and a midpoint between the chart's light background and its grid lines
    lands in the header's slot, drawing dark bars through the chart. ``Mesh3d``
    with ``intensitymode="cell"`` colours each face outright, so every pixel is
    the colour it actually is.
    """
    import plotly.graph_objects as go
    from PIL import Image

    height = max(2, round(width * rgb.shape[0] / rgb.shape[1]))
    small = np.asarray(
        Image.fromarray(rgb).resize((width, height), Image.NEAREST), dtype=np.uint8
    )
    index, palette = _quantize(small)
    n = len(palette)
    scale = []
    for slot, colour in enumerate(palette):
        css = f"rgb({colour[0]},{colour[1]},{colour[2]})"
        scale += [[slot / n, css], [(slot + 1) / n, css]]

    # Seen from the fly's side (-x looking toward +x), image column 0 is at +y
    # and row 0 at the top, so both axes run backwards from the array order.
    ys = np.linspace(y1, y0, width + 1)
    zs = np.linspace(z1, z0, height + 1)
    grid_y, grid_z = np.meshgrid(ys, zs, indexing="xy")
    vy, vz = grid_y.ravel(), grid_z.ravel()
    stride = width + 1
    rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    v00 = (rows * stride + cols).ravel()
    v01, v10 = v00 + 1, v00 + stride
    v11 = v10 + 1
    cell = index.ravel()
    faces_i = np.concatenate([v00, v00])
    faces_j = np.concatenate([v01, v11])
    faces_k = np.concatenate([v11, v10])
    intensity = np.concatenate([cell, cell]).astype(float)
    # Three quarters of the chart is its background. Painting that once as a
    # single quad behind the mesh, and keeping only the faces that differ from
    # it, drops this from ~12,000 triangles to ~3,000 without losing a pixel.
    keep = intensity != 0.0
    faces_i, faces_j, faces_k, intensity = (
        faces_i[keep],
        faces_j[keep],
        faces_k[keep],
        intensity[keep],
    )
    backdrop = _mesh(
        _box(x + 0.004, x + 0.006, min(y0, y1), max(y0, y1), min(z0, z1), max(z0, z1)),
        f"rgb({palette[0][0]},{palette[0][1]},{palette[0][2]})",
        lighting=False,
        name="screen backdrop",
    )

    detail = go.Mesh3d(
        x=np.full_like(vy, x),
        y=vy,
        z=vz,
        i=faces_i,
        j=faces_j,
        k=faces_k,
        intensity=(intensity + 0.5) / n,
        intensitymode="cell",
        cmin=0,
        cmax=1,
        colorscale=scale,
        showscale=False,
        flatshading=True,
        hoverinfo="skip",
        lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0),
        name="screen",
    )
    return [backdrop, detail]


def desk_parts(chart: np.ndarray | None = None, scale: float = 0.52):
    """The desk the fly works at: surface, monitor, keyboard, mug, speakers.

    Modelled on stonkfly's scene, including its proportions — the fly is the
    size of the monitor, not of a person. ``scale`` shrinks the furniture about
    the desk top, which stays where the fly's legs land. The monitor is the
    point of it: the fly is looking at the same frame the report shows beside
    it.
    """
    import plotly.graph_objects as go

    top = -0.74  # where the fly's legs land

    def box(x0, x1, y0, y1, z0, z1):
        return _box(
            x0 * scale,
            x1 * scale,
            y0 * scale,
            y1 * scale,
            top + (z0 - top) * scale,
            top + (z1 - top) * scale,
        )

    def up(z):
        return top + (z - top) * scale

    parts = [
        _mesh(box(-3.6, 2.6, -3.3, 3.3, top - 0.18, top), DESK, name="desk"),
        _mesh(box(2.52, 2.6, -3.3, 3.3, top, top + 0.06), DESK_EDGE, name="desk edge"),
        _mesh(box(2.16, 2.30, -0.16, 0.16, top, top + 0.46), BEZEL, name="stand"),
        _mesh(box(2.00, 2.44, -0.70, 0.70, top, top + 0.07), BEZEL, name="foot"),
        _mesh(
            box(2.20, 2.32, -2.10, 2.10, top + 0.40, top + 2.40), BEZEL, name="bezel"
        ),
        _mesh(box(0.95, 1.80, -1.15, 1.15, top, top + 0.08), KEYBOARD, name="keyboard"),
        _mesh(
            _cylinder(1.60 * scale, -1.85 * scale, top, up(top + 0.38), 0.22 * scale),
            MUG,
            name="mug",
        ),
        _mesh(box(2.00, 2.38, -2.95, -2.40, top, top + 0.80), BEZEL, name="speaker"),
        _mesh(box(2.00, 2.38, 2.40, 2.95, top, top + 0.80), BEZEL, name="speaker"),
    ]
    # Silhouettes behind the desk, for the room the reference has.
    for n, (yy, w, h) in enumerate(
        (
            (-3.6, 0.8, 3.0),
            (-2.2, 0.6, 4.2),
            (-0.9, 0.7, 2.6),
            (0.5, 0.6, 3.8),
            (1.8, 0.9, 2.8),
            (3.0, 0.7, 4.0),
        )
    ):
        parts.append(
            _mesh(
                box(3.5 + 0.10 * n, 3.8 + 0.10 * n, yy, yy + w, top, top + h),
                CITY,
                name="city",
            )
        )
    if chart is not None:
        parts.extend(
            _screen(
                chart,
                2.19 * scale,
                -2.02 * scale,
                2.02 * scale,
                up(top + 0.48),
                up(top + 2.32),
            )
        )
    else:
        parts.append(
            _mesh(
                box(2.17, 2.20, -2.02, 2.02, top + 0.48, top + 2.32),
                "#0d1622",
                name="screen off",
            )
        )
    parts.append(
        go.Scatter3d(
            x=[0.95 * scale, 1.80 * scale, None, -3.6 * scale, -3.6 * scale],
            y=[-1.15 * scale, -1.15 * scale, None, -3.3 * scale, 3.3 * scale],
            z=[up(top + 0.085), up(top + 0.085), None, up(top + 0.02), up(top + 0.02)],
            mode="lines",
            line=dict(color=WING, width=3),
            opacity=0.7,
            hoverinfo="skip",
            showlegend=False,
            name="glow",
        )
    )
    return parts


def fly_figure(
    title: str = "",
    subtitle: str = "",
    height: int = 460,
    chart: np.ndarray | None = None,
    desk: bool = True,
):
    """An orbitable low-poly fly at its desk, wings beating when you press play.

    ``chart`` is the fly's own input frame; pass it and the monitor shows the
    same picture the decoder was reading.
    """
    import plotly.graph_objects as go

    static = (desk_parts(chart) if desk else []) + _static_parts()
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
            # Over the fly's shoulder, so both it and what it is looking at
            # are in frame — the reference's own framing.
            camera=dict(
                eye=dict(x=-0.06, y=-1.10, z=0.38),
                center=dict(x=0.22, y=0, z=-0.16),
                up=dict(x=0, y=0, z=1),
            ),
            annotations=(
                [
                    dict(
                        showarrow=False,
                        x=-0.4,
                        y=0,
                        z=-1.05,
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
