"""Run directory: lock, state, events, checkpoint slots, provenance refusal."""

import json

import numpy as np
import pytest
from flybrain.run_state import RunDir, signature, source_hashes


def test_lock_is_exclusive(tmp_path):
    a = RunDir(tmp_path / "run")
    a.lock()
    b = RunDir(tmp_path / "run")
    with pytest.raises(RuntimeError):
        b.lock()
    a.unlock()
    b.lock()
    b.unlock()


def test_state_events_latest_frame(tmp_path):
    run = RunDir(tmp_path / "run")
    assert run.load_state() == {}
    run.save_state({"tick": 3, "anchor": 1.5})
    assert run.load_state() == {"tick": 3, "anchor": 1.5}
    for i in range(5):
        run.append_event({"tick": i})
    assert [e["tick"] for e in run.recent_events(3)] == [2, 3, 4]
    run.write_latest({"tick": 4})
    assert json.loads(run.latest_path.read_text()) == {"tick": 4}
    run.save_frame(np.zeros((180, 320, 3), np.uint8))
    assert run.frame_path.exists()
    assert not run.stop_requested()
    run.stop_path.touch()
    assert run.stop_requested()


def test_checkpoint_slots_and_integrity(tmp_path):
    run = RunDir(tmp_path / "run")
    assert run.checkpoint_path(4).name == "brain-0.npz"
    assert run.checkpoint_path(5).name == "brain-1.npz"
    path = run.checkpoint_path(1)
    path.write_bytes(b"brain")
    import hashlib

    good = {"file": path.name, "sha256": hashlib.sha256(b"brain").hexdigest()}
    assert run.verify_checkpoint(good) == path
    with pytest.raises(RuntimeError):
        run.verify_checkpoint({"file": path.name, "sha256": "0" * 64})
    with pytest.raises(RuntimeError):
        run.verify_checkpoint({"file": "missing.npz", "sha256": "0" * 64})


def test_provenance_refuses_changed_protocol(tmp_path):
    run = RunDir(tmp_path / "run")
    prov = {"decoder": {"window": 60}, "source_sha256": {"a.py": "1"}}
    sig = run.check_provenance(prov)
    assert sig == signature(prov)
    assert run.check_provenance(dict(prov)) == sig
    with pytest.raises(RuntimeError):
        run.check_provenance({**prov, "decoder": {"window": 30}})
    # source hashes are recorded but a code change does not refuse the resume
    assert run.check_provenance({**prov, "source_sha256": {"a.py": "2"}}) == sig


def test_source_hashes_cover_the_package():
    hashes = source_hashes()
    assert "decoder.py" in hashes and "neural/kernel.cpp" in hashes
