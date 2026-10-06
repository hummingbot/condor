"""Two explicit, literature-motivated memory compartments in MaleCNS v1.0.

PAM11 → alpha1 / MBON07 and PPL101 → gamma1pedc / MBON11.
Using the same centered rule in both is a new, unvalidated model assumption.
"""

import numpy as np

from .common import annotations, digest


def identify(brain):
    a = annotations(brain.ids)
    types = a.type.fillna("")
    kc = np.flatnonzero(types.str.startswith("KC")).astype(np.int32)
    reward = np.flatnonzero(types.eq("PAM11")).astype(np.int32)
    aversive = np.flatnonzero(types.eq("PPL101")).astype(np.int32)
    reward_mb = np.flatnonzero(types.eq("MBON07")).astype(np.int32)
    aversive_mb = np.flatnonzero(types.eq("MBON11")).astype(np.int32)
    if (len(reward), len(aversive), len(reward_mb), len(aversive_mb)) != (15, 2, 4, 2):
        raise ValueError("Unexpected cell identities/counts for MaleCNS v1.0")
    dan = np.r_[reward, aversive].astype(np.int32)
    mb = np.r_[reward_mb, aversive_mb].astype(np.int32)
    edges = np.flatnonzero(np.isin(brain.post, mb)).astype(np.int64)
    pre = (np.searchsorted(brain.ptr, edges, side="right") - 1).astype(np.int32)
    keep = np.isin(pre, kc)
    edges = edges[keep]
    pre = pre[keep]
    if not len(edges) or np.any(brain.weight[edges] <= 0):
        raise ValueError("Invalid reconstructed KC inputs")
    gains = np.zeros((len(dan), len(edges)), dtype=np.float32)
    for targets, drivers in [(reward_mb, reward), (aversive_mb, aversive)]:
        for target in targets:
            contact = []
            for d in drivers:
                sl = slice(brain.ptr[d], brain.ptr[d + 1])
                contact.append(
                    float(np.abs(brain.weight[sl][brain.post[sl] == target]).sum())
                )
            contact = np.asarray(contact)
            if contact.sum() <= 0:
                raise ValueError("Missing direct DAN-to-MBON anatomical support")
            selected = np.flatnonzero(brain.post[edges] == target)
            for d, value in zip(drivers, contact / contact.sum()):
                gains[np.flatnonzero(dan == d)[0], selected] = value
    mask = np.zeros(brain.n, dtype=np.uint8)
    mask[kc] = 1
    dan_index = np.full(brain.n, -1, dtype=np.int8)
    dan_index[dan] = np.arange(len(dan))

    def cells(ix):
        return [
            {
                "index": int(i),
                "id": str(brain.ids[i]),
                "type": str(types.iloc[i]),
                "instance": str(a.instance.iloc[i]),
            }
            for i in ix
        ]

    report = {
        "release": "MaleCNS v1.0",
        "neurons": brain.n,
        "directed_edges": len(brain.post),
        "plastic_edges": len(edges),
        "plastic_edges_sha256": digest(edges),
        "reward_cells": cells(reward),
        "aversive_cells": cells(aversive),
        "memory_outputs": cells(mb),
        "selection": "All existing KC-to-MBON07/11 connections; no graph cropping or added edges.",
        "gain": "Within-compartment DAN-to-MBON contact fractions; not measured receptor/dopamine kinetics.",
        "validated": False,
    }
    return {
        "kc": kc,
        "mb": mb,
        "dan": dan,
        "reward": reward,
        "aversive": aversive,
        "edges": edges,
        "pre": pre,
        "gain": gains,
        "kc_mask": mask,
        "dan_index": dan_index,
        "report": report,
    }
