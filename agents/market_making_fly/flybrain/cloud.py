"""The connectome's somata, as a point cloud the report can draw.

The graph the fly runs on carries no coordinates — it is pure topology. The
MaleCNS annotations do: 139,662 of the 166,700 retained neurons have a soma
position, which is enough to draw the animal's actual anatomy rather than a
made-up layout.

166,700 points would not survive a report, so this builds a deterministic
subsample once and caches it beside the graph. The sample is stratified rather
than uniform: every cell the decoder and the memory rule actually read — Kenyon
cells, the two dopamine populations, their MBONs, the DNp20/DNpe017 readouts —
is kept in full, because those are the ones worth looking at. The rest is a
seeded draw, so the same install always draws the same brain.
"""

from __future__ import annotations

import numpy as np
from flybrain.neural.common import DATA, GRAPH

CLOUD = DATA / "soma_cloud.npz"
SEED = 7301  # the interface seed, so the whole model shares one
DEFAULT_POINTS = 7000

# Groups are coarse on purpose: the point is where activity sits in the
# animal, not a taxonomy. Order is the colour order in the report.
GROUPS = (
    "mushroom body",  # Kenyon cells — where the memory rule acts
    "dopamine",  # PAM11 / PPL101 — the reward and aversive populations
    "memory output",  # MBON07 / MBON11
    "readout",  # DNp20 / DNpe017 — what the posture is decoded from
    "descending",  # the arousal channel
    "visual",  # optic lobe and visual projection — what the chart drives
    "other",
)


def _positions(ids: np.ndarray) -> np.ndarray:
    """Soma coordinates per graph index; NaN where the release has none."""
    import pyarrow.feather as feather

    table = feather.read_table(
        DATA / "annotations.feather", columns=["bodyId", "somaLocation"]
    )
    frame = table.to_pandas().set_index("bodyId").reindex(ids)
    out = np.full((len(ids), 3), np.nan, dtype=np.float32)
    for row, value in enumerate(frame.somaLocation.to_numpy()):
        if value is None:
            continue
        try:
            out[row] = np.asarray(value, dtype=np.float32)[:3]
        except (TypeError, ValueError):
            continue
    return out


def _group_of(ids: np.ndarray, superclass: np.ndarray) -> np.ndarray:
    """One coarse group per neuron, identified cells taking precedence."""
    import pyarrow.feather as feather
    from flybrain.worker import DESCENDING_SUPERCLASS

    types = (
        feather.read_table(DATA / "annotations.feather", columns=["bodyId", "type"])
        .to_pandas()
        .set_index("bodyId")
        .reindex(ids)
        .type.fillna("")
        .to_numpy()
        .astype(str)
    )
    group = np.full(len(ids), GROUPS.index("other"), dtype=np.int8)
    visual = np.isin(
        superclass,
        ["ol_intrinsic", "ol_sensory", "visual_projection", "visual_centrifugal"],
    )
    group[visual] = GROUPS.index("visual")
    group[superclass == DESCENDING_SUPERCLASS] = GROUPS.index("descending")
    group[np.char.startswith(types, "KC")] = GROUPS.index("mushroom body")
    group[np.isin(types, ["MBON07", "MBON11"])] = GROUPS.index("memory output")
    group[np.isin(types, ["PAM11", "PPL101"])] = GROUPS.index("dopamine")
    group[np.isin(types, ["DNp20", "DNpe017"])] = GROUPS.index("readout")
    return group


def build(max_points: int = DEFAULT_POINTS) -> dict:
    """Subsample the somata and cache the result. Idempotent."""
    with np.load(GRAPH) as graph:
        ids = graph["ids"]
        superclass = graph["superclass"].astype(str)
    xyz = _positions(ids)
    group = _group_of(ids, superclass)
    mapped = np.flatnonzero(np.isfinite(xyz).all(axis=1))

    # Sample per group, not uniformly. Uniform would be all optic lobe — it is
    # half the animal — and the circuit that actually decides anything would
    # vanish. Proportional keeps the silhouette recognisable; the floors keep
    # the mushroom body and the descending channel dense enough to watch; the
    # handful of identified cells are always kept whole.
    rng = np.random.default_rng(SEED)
    floors = {
        GROUPS.index("mushroom body"): 420,
        GROUPS.index("descending"): 420,
    }
    always = [GROUPS.index(name) for name in ("dopamine", "memory output", "readout")]
    picked = [mapped[np.isin(group[mapped], always)]]
    budget = max_points - len(picked[0])
    pool = {
        g: mapped[group[mapped] == g] for g in range(len(GROUPS)) if g not in always
    }
    total = sum(len(v) for v in pool.values()) or 1
    for g, members in pool.items():
        want = min(
            len(members), max(floors.get(g, 0), round(budget * len(members) / total))
        )
        picked.append(
            members
            if want >= len(members)
            else rng.choice(members, size=want, replace=False)
        )
    index = np.sort(np.concatenate(picked))

    np.savez_compressed(
        CLOUD,
        index=index.astype(np.int32),
        xyz=xyz[index],
        group=group[index],
        mapped_total=np.int64(len(mapped)),
        neurons_total=np.int64(len(ids)),
    )
    return load()


def load(max_points: int = DEFAULT_POINTS) -> dict:
    """The cached cloud, building it on first use."""
    if not CLOUD.exists():
        return build(max_points)
    with np.load(CLOUD) as data:
        return {
            "index": data["index"],
            "xyz": data["xyz"],
            "group": data["group"],
            "mapped_total": int(data["mapped_total"]),
            "neurons_total": int(data["neurons_total"]),
        }


def orient(xyz: np.ndarray) -> np.ndarray:
    """Centre the cloud and put the animal the way an atlas figure does.

    MaleCNS voxel axes are (x right, y down, z front-to-back). Negating y puts
    dorsal up, so the brain reads as a brain rather than as its own reflection.
    """
    out = np.stack([xyz[:, 0], xyz[:, 2], -xyz[:, 1]], axis=1)
    centre = np.nanmean(out, axis=0)
    scale = np.nanmax(np.abs(out - centre))
    return (out - centre) / (scale or 1.0)
