"""The sweep's arithmetic and its one-signature-per-executor rule.

The sweep spends the vault's capital, so the two things that must hold are that
it never spends more than a fee earned and that it never spends for the same
executor twice. Both are cheap to get wrong in a way no integration test would
catch — a restart, a retried webhook, a rounding direction — so they are pinned
here.
"""


import pytest

from condor import vault_sweep


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point the ledger at a temp dir so tests cannot touch a real vault's."""
    monkeypatch.setattr(vault_sweep.paths, "state_dir", lambda ns: tmp_path / ns)
    return tmp_path


VAULT = "3wm644fHe3ekULLCPov4vkDeVCJEaQmnCfn5k2HhMS4B"


# ── the share ─────────────────────────────────────────────────────────────────


def test_the_share_is_fee_bps_of_realised_fees():
    # 1.5 SOL of fees at 50% → 0.75 SOL.
    assert vault_sweep.share_of(1.5, 5000) == 750_000_000


def test_rounding_is_down_so_a_sweep_never_outruns_the_fee():
    """Up would spend a lamport no fee earned — holders' assets buying holders'
    assets, once per close, forever."""
    # 1 lamport of fees at 50% is half a lamport, which is nothing.
    assert vault_sweep.share_of(1e-9, 5000) == 0


@pytest.mark.parametrize("fees,bps", [(0, 5000), (-1.0, 5000), (1.0, 0), (1.0, -1)])
def test_nothing_to_sweep_is_zero_not_an_error(fees, bps):
    assert vault_sweep.share_of(fees, bps) == 0


# ── the ledger ────────────────────────────────────────────────────────────────


def test_an_executor_is_swept_once_however_often_it_is_reported():
    """A crank restart re-reads the same terminal executors. The second read
    must accrue nothing."""
    for _ in range(5):
        vault_sweep.record_close(VAULT, "exec-1", 1.0, 5000)
    ledger = vault_sweep.read_ledger(VAULT)
    assert ledger["pending_lamports"] == 500_000_000
    assert list(ledger["swept"]) == ["exec-1"]


def test_separate_executors_accumulate():
    vault_sweep.record_close(VAULT, "exec-1", 1.0, 5000)
    vault_sweep.record_close(VAULT, "exec-2", 0.5, 5000)
    assert vault_sweep.read_ledger(VAULT)["pending_lamports"] == 750_000_000


def test_nothing_is_due_below_the_floor():
    """A 0.0001 SOL buy pays more in fees than it burns."""
    vault_sweep.record_close(VAULT, "exec-1", 0.000_02, 5000)
    assert vault_sweep.due(VAULT) == 0
    vault_sweep.record_close(VAULT, "exec-2", 1.0, 5000)
    assert vault_sweep.due(VAULT) == 500_010_000


def test_taking_pending_is_what_a_sweep_spends():
    vault_sweep.record_close(VAULT, "exec-1", 1.0, 5000)
    vault_sweep.take_pending(VAULT, 500_000_000)
    assert vault_sweep.read_ledger(VAULT)["pending_lamports"] == 0


def test_a_corrupt_ledger_raises_rather_than_reading_as_empty(isolated_state):
    """Reading as empty would re-sweep every executor the vault has ever
    closed — real capital, spent twice."""
    path = isolated_state / "vaults" / f"{VAULT}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    with pytest.raises(RuntimeError) as exc:
        vault_sweep.read_ledger(VAULT)
    assert "double-sweep" in str(exc.value)


def test_sweep_history_is_capped():
    for index in range(250):
        vault_sweep.record_sweep(VAULT, f"sig{index}", 1, None)
    ledger = vault_sweep.read_ledger(VAULT)
    assert len(ledger["sweeps"]) == 200
    assert ledger["sweeps"][-1]["signature"] == "sig249"
