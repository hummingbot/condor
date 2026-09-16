"""Candidate R8 display adapter and target-specific cotransmission.

Anatomy remains exactly MaleCNS. R8->aMe12 net excitation is supported by
Xiao et al. Nature 2023, doi:10.1038/s41586-023-06681-6 (AMA includes aMe12).
The transferred type correspondence, unitary magnitude, LIF photoreceptor
proxy and RGB-to-opsin transfer are explicit modeling assumptions. No UV
channel or sensitivity is invented for R7 or untyped photoreceptors.
"""

import math

import numpy as np

from .brain import MemoryBrain
from .common import annotations, digest


def projection(brain, a):
    known = np.flatnonzero(a.type.isin(["R8p", "R8y"]))
    mapped = []
    hexes = []
    confidence = []
    for i in known:
        edges = np.arange(brain.ptr[i], brain.ptr[i + 1])
        targets = brain.post[edges]
        valid = (
            a.assignedOlHex1.iloc[targets].notna().to_numpy()
            & a.assignedOlHex2.iloc[targets].notna().to_numpy()
        )
        votes = {}
        for e, j in zip(edges[valid], targets[valid]):
            h = (float(a.assignedOlHex1.iloc[j]), float(a.assignedOlHex2.iloc[j]))
            votes[h] = votes.get(h, 0) + float(abs(brain.weight[e]))
        if not votes:
            continue
        h = max(votes, key=votes.get)
        mapped.append(i)
        hexes.append(h)
        confidence.append(votes[h] / sum(votes.values()))
    mapped = np.asarray(mapped, dtype=np.int32)
    hexes = np.asarray(hexes)
    xy = np.column_stack(
        [hexes[:, 0] - 0.5 * hexes[:, 1], np.sqrt(3) / 2 * hexes[:, 1]]
    )
    # Fit the original display projection from the existing R1-R6 anchors so
    # R8 does not get a separately stretched eye or arbitrary target position.
    from .common import GRAPH

    with np.load(GRAPH) as g:
        oldhex = g["hexes"]
    oldxy = np.column_stack(
        [oldhex[:, 0] - 0.5 * oldhex[:, 1], np.sqrt(3) / 2 * oldhex[:, 1]]
    )
    uv = np.empty_like(xy)
    for side in ["L", "R"]:
        original = a.rootSide.iloc[brain.retina].eq(side).to_numpy()
        select = a.rootSide.iloc[mapped].eq(side).to_numpy()
        lo = oldxy[original].min(axis=0)
        span = np.ptp(oldxy[original], axis=0)
        z = (xy[select] - lo) / span
        uv[select, 0] = 0.6 * z[:, 0] if side == "L" else 0.4 + 0.6 * (1 - z[:, 0])
        uv[select, 1] = 1 - z[:, 1]
    if not a.rootSide.iloc[mapped].isin(["L", "R"]).all():
        raise ValueError("Unresolved eye side")
    return (
        mapped,
        np.clip(uv, 0, 1).astype(np.float32),
        np.asarray(confidence, dtype=np.float32),
    )


class VisualMemoryBrain(MemoryBrain):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        a = annotations(self.ids)
        self.r8, self.r8_uv, self.r8_confidence = projection(self, a)
        self.r8_channel = np.where(a.type.iloc[self.r8].eq("R8p"), 2, 1).astype(
            np.int32
        )
        self.r8_light = np.zeros(len(self.r8), dtype=np.float32)
        corrected = []
        for i in np.flatnonzero(a.type.fillna("").str.startswith("R8")):
            edges = np.arange(self.ptr[i], self.ptr[i + 1])
            e = edges[a.type.iloc[self.post[edges]].eq("aMe12").to_numpy()]
            corrected.extend(e.tolist())
            self.weight[e] = np.abs(self.weight[e])
        self.corrected_edges = np.asarray(corrected, dtype=np.int64)
        self.initial_weight_sha256 = digest(self.weight)
        self.fields.append("r8_light")
        self.initial["r8_light"] = self.r8_light.copy()
        self.visual_report = {
            "model": "r8-rgb-ame12-v1",
            "mapped_R8p": int((self.r8_channel == 2).sum()),
            "mapped_R8y": int((self.r8_channel == 1).sum()),
            "known_unmapped": int(a.type.isin(["R8p", "R8y"]).sum() - len(self.r8)),
            "projection_confidence_median": float(np.median(self.r8_confidence)),
            "projection_below_80_percent": int((self.r8_confidence < 0.8).sum()),
            "corrected_existing_edges": len(corrected),
            "corrected_edge_sha256": digest(self.corrected_edges),
            "coordinate_inference": "Modal column of all outgoing contacts to any column-annotated target; same viewport transform as R1-R6.",
            "spectrum": "Linear sRGB B for R8p; G for R8y. Display proxy, not calibrated photon flux or spectral sensitivity. R7, dorsal and untyped R8 receive no invented optical drive.",
            "physiology": "R8 to aMe12 net sign positive; original contact magnitudes retained. Other R8 targets retain baseline sign. Photoreceptors still use a LIF rate proxy, not graded in-vivo dynamics.",
            "evidence": [
                "https://doi.org/10.1038/s41586-023-06681-6",
                "https://doi.org/10.1038/s41467-024-49616-z",
                "https://doi.org/10.7554/eLife.71858",
            ],
            "validated": False,
        }

    def rgb_step(self, frame, duration_ms, **kwargs):
        if duration_ms > 10:
            ticks = round(duration_ms / self.dt)
            total = np.zeros(self.n, dtype=np.int32)
            wall = 0.0
            while ticks:
                n = min(100, ticks)
                c, t = self.rgb_step(frame, n * self.dt, **kwargs)
                total += c
                wall += t
                ticks -= n
            self.counts[:] = total
            return total, wall
        from .sensory import retinal_samples

        frame = np.asarray(frame)
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("RGB uint8 required")
        h, w = frame.shape[:2]
        x = np.minimum((self.r8_uv[:, 0] * (w - 1)).astype(int), w - 1)
        y = np.minimum((self.r8_uv[:, 1] * (h - 1)).astype(int), h - 1)
        values = frame[y, x, self.r8_channel].astype(np.float32) / 255
        values = np.where(
            values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4
        )
        self.r8_light += (
            1 - math.exp(-round(duration_ms / self.dt) * self.dt / 10)
        ) * (values - self.r8_light)
        extra = kwargs.pop("stimulation", None)
        pulses = (
            [] if extra is None else list(extra) if isinstance(extra, list) else [extra]
        )
        pulses.append((self.r8, 30 * self.r8_light / (0.02 + self.r8_light)))
        return self.step(
            retinal_samples(frame, self.uv), duration_ms, stimulation=pulses, **kwargs
        )

    def configuration_signature(self):
        return {
            **super().configuration_signature(),
            **{
                k: digest(getattr(self, k))
                for k in ["r8", "r8_uv", "r8_channel", "corrected_edges"]
            },
        }
