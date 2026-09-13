"""Compile all retained edges and a documented, inferred retinal projection."""

import json

import numpy as np
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.ipc as ipc

from .common import DATA
from .transmitters import transmitter_signs


def prepare(dataset="malecns_v1"):
    root = DATA
    nodes = feather.read_table(root / "normalized/neurons.feather").to_pandas()
    report = json.loads((root / "normalized/report.json").read_text())
    edges = ipc.open_file(
        pa.memory_map(str(root / "normalized/edges.arrow"), "r")
    ).read_all()
    pre, post, count = [
        edges.column(k).to_numpy() for k in ["pre_index", "post_index", "synapse_count"]
    ]
    # CSR stores every released edge, including self edges and weak edges.
    order = np.argsort(pre, kind="stable")
    ptr = np.r_[0, np.cumsum(np.bincount(pre, minlength=len(nodes)))].astype(np.int64)
    signs, uncertain = transmitter_signs(nodes.neurotransmitter)
    weight = (count[order].astype(np.float32) * signs[pre[order]] * 0.275).astype(
        np.float32
    )
    a = feather.read_table(root / "annotations.feather").to_pandas()
    if dataset != "malecns_v1":
        raise ValueError(
            "Female retinal projection is unresolved; do not silently copy the male sensory map."
        )
    a = a.set_index("bodyId").loc[nodes.source_id]
    receptor = a.type.eq("R1-R6").to_numpy()
    anchors = (
        a.type.isin(["L1", "L2", "L3"]).to_numpy() & a.assignedOlHex1.notna().to_numpy()
    )
    selected = receptor[pre] & anchors[post]
    # Infer column from the distribution of ALL R1-R6→L1/L2/L3 contact counts.
    # This is a sensory-coordinate estimate, never a filter on the neural graph.
    cols = {}
    for i, j, w in zip(pre[selected], post[selected], count[selected]):
        key = (float(a.assignedOlHex1.iloc[j]), float(a.assignedOlHex2.iloc[j]))
        cols.setdefault(int(i), {})
        cols[int(i)][key] = cols[int(i)].get(key, 0) + int(w)
    indices = []
    xy = []
    confidence = []
    hexes = []
    for i, counts in sorted(cols.items()):
        h = max(counts, key=counts.get)
        total = sum(counts.values())
        indices.append(i)
        hexes.append(h)
        confidence.append(counts[h] / total)
        # Axial grid embedding; angular extent/orientation is an experimental
        # display projection, not a measured eye pose or ommatidial calibration.
        xy.append((h[0] - 0.5 * h[1], np.sqrt(3) / 2 * h[1]))
    xy = np.asarray(xy)
    indices = np.asarray(indices, dtype=np.int32)
    uv = np.empty_like(xy)
    sides = a.rootSide.to_numpy()[indices]
    for side in ["L", "R"]:
        mask = sides == side
        z = xy[mask]
        z = (z - z.min(axis=0)) / (z.max(axis=0) - z.min(axis=0))
        # Split screen into overlapping left/right experimental viewports.
        uv[mask, 0] = 0.60 * z[:, 0] if side == "L" else 0.40 + 0.60 * (1 - z[:, 0])
        uv[mask, 1] = 1 - z[:, 1]
    readouts = []
    for i in np.flatnonzero(
        nodes.cell_type.isin(
            ["DNa02", "DNp09", "MDN", "MN9", "DNp20", "DNpe017"]
        ).to_numpy()
    ):
        readouts.append(
            {
                "index": int(i),
                "id": str(nodes.source_id.iloc[i]),
                "type": str(nodes.cell_type.iloc[i]),
                "side": str(a.somaSide.iloc[i]),
            }
        )
    manifest = {
        "dataset": dataset,
        "neurons": len(nodes),
        "edges": len(pre),
        "synaptic_contacts": int(count.sum(dtype=np.uint64)),
        "retina_total": int(receptor.sum()),
        "retina_mapped": len(indices),
        "retina_unmapped": int(receptor.sum()) - len(indices),
        "projection_confidence_median": float(np.median(confidence)),
        "projection_below_80_percent": int(np.sum(np.asarray(confidence) < 0.8)),
        "readouts": readouts,
        "source_hashes": report["source_hashes"],
        "uncertain_sign_neurons": int(uncertain.sum()),
        "retina_model": "R1-R6 luminance-only. Column inferred from all contacts onto annotated L1/L2/L3; modal column. Experimental overlapping viewport projection, not calibrated retinal angles.",
        "visual_dynamics": "Photoreceptors and lamina are graded in vivo. This experiment uses an explicit LIF proxy, low-pass luminance drive and tonic lamina current; it is not validated fly vision.",
        "motor_interface": "Stonkfly uses DNp20 mean right-minus-left firing with a DNpe017 spike gate for buy/sell/hold. This is an engineered trading interface.",
        "training": "The compiled graph is the baseline. Runtime adds the documented candidate KC-to-MBON07/11 plasticity and R8-to-aMe12 sign assumption.",
    }
    out = DATA
    out.mkdir(parents=True, exist_ok=True)
    np.savez(
        out / "graph.npz",
        ptr=ptr,
        post=post[order].astype(np.int32),
        weight=weight,
        ids=nodes.source_id.to_numpy(dtype=np.int64),
        retina=indices,
        uv=uv.astype(np.float32),
        confidence=np.asarray(confidence),
        hexes=np.asarray(hexes),
        lamina=np.flatnonzero(
            nodes.cell_type.isin(["L1", "L2", "L3", "L5"]).to_numpy()
        ).astype(np.int32),
        sugar=np.flatnonzero(nodes.cell_type.eq("LB3c").to_numpy()).astype(np.int32),
        superclass=np.asarray(
            nodes.superclass.fillna("unassigned").astype(str), dtype="U64"
        ),
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in manifest.items()
                if k not in ["source_hashes", "readouts"]
            }
        )
    )
    return manifest


if __name__ == "__main__":
    prepare()
