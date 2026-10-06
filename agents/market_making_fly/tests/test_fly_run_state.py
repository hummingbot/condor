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
    # a run recorded under an older signing rule is re-signed, not refused
    stale = json.loads(run.provenance_path.read_text())
    stale["signature"] = "0" * 64
    run.provenance_path.write_text(json.dumps(stale))
    assert run.check_provenance(prov) == sig


def test_cadence_may_change_but_the_books_own_terms_may_not(tmp_path):
    """A resume restores the anchor, the session high and the carried P&L.
    Anything that denominates those must be signed, or a run judges one
    deployment's numbers by another's thresholds."""
    run = RunDir(tmp_path / "run")
    base = {
        "settings": {
            "neural_ms": 500,
            "interval_sec": 60,
            "total_amount_quote": 200,
            "leverage": 3,
            "portfolio_allocation": 0.2,
            "connector_name": "hyperliquid_perpetual",
        }
    }
    sig = run.check_provenance(base)
    # cadence moves no money and reinterprets no accounting
    slower = {"settings": {**base["settings"], "interval_sec": 300}}
    assert run.check_provenance(slower) == sig
    for changed in (
        {"total_amount_quote": 1000},
        {"leverage": 5},
        {"portfolio_allocation": 1.0},
        {"connector_name": "binance_perpetual"},
        {"neural_ms": 700},
    ):
        with pytest.raises(RuntimeError):
            run.check_provenance({"settings": {**base["settings"], **changed}})


def test_state_version_is_signed(tmp_path):
    """A persisted-state change must orphan the run rather than let old state
    be reinterpreted: session_high_net 0.0 -> None already proved why."""
    import flybrain.run_state as run_state

    run = RunDir(tmp_path / "run")
    prov = {"settings": {"neural_ms": 500}}
    sig = run.check_provenance(prov)
    recorded = json.loads(run.provenance_path.read_text())
    assert recorded["state_version"] == run_state.STATE_VERSION
    original = run_state.STATE_VERSION
    try:
        run_state.STATE_VERSION = original + 1
        with pytest.raises(RuntimeError):
            run.check_provenance(prov)
    finally:
        run_state.STATE_VERSION = original
    assert run.check_provenance(prov) == sig


def test_source_hashes_cover_the_package():
    hashes = source_hashes()
    assert "decoder.py" in hashes and "neural/kernel.cpp" in hashes
