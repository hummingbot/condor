"""Loss-accounted import of the retained MaleCNS v1.0 graph for Stonkfly.

This module stores topology and annotations. It does not infer missing dynamics,
neurotransmitter receptors, muscle mappings, or behavior from a wiring graph.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .common import DATA

REGISTRY = Path(__file__).with_name("datasets.json")


def exact_ids(values) -> np.ndarray:
    """Never round 64-bit biological IDs through floating point or JavaScript."""
    items = np.asarray(values)
    if items.dtype.kind == "f":
        raise ValueError(
            "Neuron IDs must be integers or decimal strings, never floats."
        )
    if items.dtype.kind in "iu":
        if np.any(items < 0):
            raise ValueError("Neuron IDs cannot be negative.")
        return items.astype(np.uint64)
    text = [str(value) for value in items]
    if any(not value.isascii() or not value.isdecimal() for value in text):
        raise ValueError("Neuron IDs must be nonnegative decimal integers.")
    return np.asarray(text, dtype=np.uint64)


def index_edges(ids: np.ndarray, pre, post, counts):
    """Return retained edges plus a mask accounting for every excluded row."""
    if not len(ids) or np.any(ids[1:] <= ids[:-1]):
        raise ValueError("Node IDs must be nonempty, unique, and sorted.")
    pre, post = exact_ids(pre), exact_ids(post)
    counts = np.asarray(counts)
    if len(pre) != len(post) or len(pre) != len(counts):
        raise ValueError("Edge columns have different lengths.")
    if (
        not np.all(np.isfinite(counts))
        or np.any(counts < 1)
        or np.any(counts != np.floor(counts))
        or np.any(counts > 2**32 - 1)
    ):
        raise ValueError("Synapse counts must be positive uint32-compatible integers.")
    i, j = np.searchsorted(ids, pre), np.searchsorted(ids, post)
    keep = (i < len(ids)) & (j < len(ids))
    keep &= ids[np.minimum(i, len(ids) - 1)] == pre
    keep &= ids[np.minimum(j, len(ids) - 1)] == post
    return (
        i[keep].astype(np.uint32),
        j[keep].astype(np.uint32),
        counts[keep].astype(np.uint32),
        keep,
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_nodes(dataset_id: str, frame, nt_frame=None):
    """Normalize fields while keeping the original annotations in raw files."""
    import pandas as pd

    if dataset_id == "malecns_v1":
        source = exact_ids(frame.bodyId)
        retain = frame.superclass.notna() & frame.superclass.astype(str).ne("")
        quality = frame.statusLabel.astype(object).fillna("unknown")
        superclasses, types = frame.superclass, frame.type
        nonneural = frame.status.eq("Glia")
        retain = retain & ~nonneural
        predicted = pd.Series([None] * len(frame))
        nt_source = pd.Series(["missing"] * len(frame))
        if nt_frame is not None:
            nt_frame = nt_frame.set_index("body")
            if not nt_frame.index.is_unique:
                raise ValueError("Duplicate male neurotransmitter IDs.")
            predicted = frame.bodyId.map(nt_frame.consensus_nt)
            nt_source = pd.Series(
                ["source_consensus_prediction_or_ground_truth"] * len(frame)
            )
        candidate_reason = np.where(
            retain, "assigned_neuronal_superclass", "unresolved_object"
        )
    else:
        raise ValueError(f"Unsupported dataset: {dataset_id}")
    if len(np.unique(source)) != len(source):
        raise ValueError("Duplicate versioned neuron IDs in source annotations.")
    catalog = pd.DataFrame(
        {
            "source_id": source,
            "retained": np.asarray(retain, dtype=bool),
            "object_kind": np.where(
                nonneural,
                "non_neuronal",
                np.where(retain, "neuron_candidate", "unresolved_object"),
            ),
            "inclusion_reason": np.where(
                nonneural, "explicit_non_neuronal_annotation", candidate_reason
            ),
            "quality": np.asarray(quality),
            "superclass": np.asarray(superclasses),
            "cell_type": np.asarray(types),
            "neurotransmitter": np.asarray(predicted),
            "neurotransmitter_source": np.asarray(nt_source),
        }
    ).sort_values("source_id", ignore_index=True)
    nodes = catalog.loc[catalog.retained].reset_index(drop=True)
    nodes.insert(0, "node_index", np.arange(len(nodes), dtype=np.uint32))
    return catalog, nodes


def import_graph(dataset_id: str = "malecns_v1") -> dict:
    import pyarrow as pa
    import pyarrow.feather as feather
    import pyarrow.ipc as ipc

    config = json.loads(REGISTRY.read_text())["datasets"][dataset_id]
    source_dir = DATA
    output = source_dir / "normalized"
    output.mkdir(parents=True, exist_ok=True)
    lock_path = source_dir / "source.lock.json"
    hashes = {
        name: {
            "url": url,
            "bytes": (source_dir / name).stat().st_size,
            "sha256": file_digest(source_dir / name),
        }
        for name, url in config["files"].items()
    }
    if lock_path.exists():
        locked = json.loads(lock_path.read_text())
        if any(
            locked[name]["sha256"] != info["sha256"] for name, info in hashes.items()
        ):
            raise ValueError(
                "Source files changed since the lock was created; use a new versioned dataset directory."
            )
    else:
        lock_path.write_text(json.dumps(hashes, indent=2) + "\n")
    frame = feather.read_table(source_dir / "annotations.feather").to_pandas()
    nt_frame = feather.read_table(source_dir / "neurotransmitters.feather").to_pandas()
    catalog, nodes = normalize_nodes(dataset_id, frame, nt_frame)
    feather.write_feather(catalog, output / "catalog.feather")
    feather.write_feather(nodes, output / "neurons.feather")
    ids = exact_ids(nodes.source_id)
    np.save(output / "neuron_ids.npy", ids)
    edge_columns = ("body_pre", "body_post", "weight")
    reader = ipc.open_file(pa.memory_map(str(source_dir / "edges.feather"), "r"))
    schema = pa.schema(
        [
            ("pre_index", pa.uint32()),
            ("post_index", pa.uint32()),
            ("synapse_count", pa.uint32()),
        ]
    )
    stats = {
        key: 0
        for key in [
            "source_edge_rows",
            "retained_edge_rows",
            "excluded_edge_rows",
            "source_synaptic_contacts",
            "retained_synaptic_contacts",
            "excluded_synaptic_contacts",
            "retained_weight_one_edges",
            "retained_self_edges",
        ]
    }
    incoming, outgoing = (
        np.zeros(len(ids), dtype=np.int64),
        np.zeros(len(ids), dtype=np.int64),
    )
    temporary = output / "edges.arrow.partial"
    with pa.OSFile(str(temporary), "wb") as sink, ipc.new_file(sink, schema) as writer:
        for number in range(reader.num_record_batches):
            batch = reader.get_batch(number)
            pre, post, weights = [
                batch.column(batch.schema.get_field_index(c)).to_numpy(
                    zero_copy_only=False
                )
                for c in edge_columns
            ]
            i, j, count, keep = index_edges(ids, pre, post, weights)
            stats["source_edge_rows"] += len(pre)
            stats["retained_edge_rows"] += len(i)
            stats["source_synaptic_contacts"] += int(weights.sum(dtype=np.uint64))
            stats["retained_synaptic_contacts"] += int(count.sum(dtype=np.uint64))
            stats["retained_weight_one_edges"] += int(np.count_nonzero(count == 1))
            stats["retained_self_edges"] += int(np.count_nonzero(i == j))
            np.add.at(incoming, j, count)
            np.add.at(outgoing, i, count)
            writer.write_batch(
                pa.record_batch(
                    [pa.array(i), pa.array(j), pa.array(count)], schema=schema
                )
            )
    temporary.replace(output / "edges.arrow")
    stats["excluded_edge_rows"] = (
        stats["source_edge_rows"] - stats["retained_edge_rows"]
    )
    stats["excluded_synaptic_contacts"] = (
        stats["source_synaptic_contacts"] - stats["retained_synaptic_contacts"]
    )
    assert (
        int(incoming.sum())
        == int(outgoing.sum())
        == stats["retained_synaptic_contacts"]
    )
    np.save(output / "incoming_synapse_counts.npy", incoming)
    np.save(output / "outgoing_synapse_counts.npy", outgoing)
    report = {
        "dataset_id": dataset_id,
        "sex": config["sex"],
        "release": config["release"],
        "coverage": config["coverage"],
        "source_annotation_rows": len(catalog),
        "retained_neuron_candidates": len(nodes),
        "excluded_object_counts": catalog.loc[~catalog.retained]
        .object_kind.value_counts()
        .to_dict(),
        "quality_counts": nodes.quality.value_counts(dropna=False).to_dict(),
        "superclass_counts": nodes.superclass.fillna("unknown")
        .value_counts()
        .to_dict(),
        "neurotransmitter_counts": nodes.neurotransmitter.fillna("missing")
        .astype(str)
        .value_counts()
        .to_dict(),
        "isolated_neurons": int(np.count_nonzero((incoming == 0) & (outgoing == 0))),
        "node_policy": config["node_policy"],
        "edge_policy": config["edge_policy"],
        "upstream_filters": config["upstream_filters"],
        "upstream_autapses_excluded": config["upstream_autapses_excluded"],
        "additional_edge_strength_threshold": None,
        "synaptic_weights_are_contact_counts": True,
        "source_hashes": hashes,
        "graph": stats,
        "neural_dynamics_validated": False,
        "embodied_behavior_implemented": False,
        "remaining_gaps": [
            "Receptor-dependent synapse dynamics and neuromodulation",
            "Calibrated retinal dynamics",
            "Validated learning and controller interpretation",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    public_report = DATA / "connectome-import.json"
    public_report.parent.mkdir(parents=True, exist_ok=True)
    public_report.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset", choices=["malecns_v1"], nargs="?", default="malecns_v1"
    )
    args = parser.parse_args()
    report = import_graph(args.dataset)
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "dataset_id",
                    "retained_neuron_candidates",
                    "graph",
                    "neural_dynamics_validated",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
