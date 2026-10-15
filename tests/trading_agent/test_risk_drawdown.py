import pytest

from condor.agents.journal import JournalManager
from condor.agents.performance import _executor_row
from condor.agents.risk import RiskEngine, RiskLimits


def test_drawdown_uses_exposure_not_peak_profit(tmp_path):
    journal = JournalManager("agent_1", session_dir=tmp_path)
    journal.record_snapshot(
        total_pnl=0.00, total_volume=10, open_count=1, position_size=10
    )
    journal.record_snapshot(
        total_pnl=0.05, total_volume=10, open_count=1, position_size=10
    )
    journal.record_snapshot(
        total_pnl=0.03, total_volume=10, open_count=1, position_size=10
    )

    assert journal.get_drawdown_pct() == pytest.approx(0.2)


def test_drawdown_is_zero_without_exposure(tmp_path):
    journal = JournalManager("agent_1", session_dir=tmp_path)
    journal.record_snapshot(
        total_pnl=0.05, total_volume=10, open_count=1, position_size=0
    )
    journal.record_snapshot(
        total_pnl=0.03, total_volume=10, open_count=1, position_size=0
    )

    assert journal.get_drawdown_pct() == 0.0


def test_lp_executor_amount_uses_current_position_value():
    row = _executor_row(
        {
            "status": "RUNNING",
            "config": {"type": "lp_executor", "trading_pair": "Fartcoin-USDC"},
            "custom_info": {"total_value_quote": 9.97, "current_price": 0.12},
        }
    )

    assert row["amount"] == 9.97
    assert row["current_price"] == 0.12


# ---------------------------------------------------------------------------
# CORR-725: "no reading" reaches the same fail-closed arm as a raise
# ---------------------------------------------------------------------------


def test_a_ticked_session_with_no_snapshot_blocks_instead_of_reading_zero(tmp_path):
    """The failure GitHub #272 cost a race entry on: a strategy that stopped
    producing readings was handed 0.0, the one drawdown that clears every
    limit, and read as a strategy sitting safely at zero."""
    journal = JournalManager("agent_1", session_dir=tmp_path)
    journal.record_tick("ran")
    assert journal.get_drawdown_pct_checked() == (0.0, False)

    state = RiskEngine(RiskLimits(max_drawdown_pct=10.0)).get_state(journal)

    assert state.drawdown_known is False
    assert state.is_blocked
    assert "no drawdown reading" in state.block_reason


def test_a_session_that_has_not_ticked_yet_is_not_blocked(tmp_path):
    """Before the first tick there is nothing to have fallen from, and a
    session blocked on its own missing history could never take the tick that
    writes the first snapshot."""
    journal = JournalManager("agent_1", session_dir=tmp_path)
    assert journal.tick_count == 0
    assert journal.get_drawdown_pct_checked() == (0.0, True)

    state = RiskEngine(RiskLimits(max_drawdown_pct=10.0)).get_state(journal)

    assert state.drawdown_known is True
    assert not state.is_blocked


def test_a_genuinely_flat_session_still_reads_ok_and_does_not_alert(tmp_path):
    """The easy version of this fix makes every idle market maker alert, which
    is the failure mode that gets a guard switched off. Quiet and dead are told
    apart by whether anything was read, never by the number."""
    journal = JournalManager("agent_1", session_dir=tmp_path)
    journal.record_tick("ran")
    journal.record_snapshot(
        total_pnl=0.0, total_volume=0.0, open_count=0, position_size=0.0
    )

    assert journal.get_drawdown_pct_checked() == (0.0, True)

    state = RiskEngine(RiskLimits(max_drawdown_pct=10.0)).get_state(journal)

    assert state.drawdown_known is True
    assert not state.is_blocked
    assert state.block_reason == ""


def test_the_real_drawdown_still_reads_known(tmp_path):
    journal = JournalManager("agent_1", session_dir=tmp_path)
    for pnl in (0.00, 0.05, 0.03):
        journal.record_snapshot(
            total_pnl=pnl, total_volume=10, open_count=1, position_size=10
        )

    pct, known = journal.get_drawdown_pct_checked()

    assert known is True
    assert pct == pytest.approx(0.2)
