import pytest

from condor.trading_agent.journal import JournalManager
from condor.trading_agent.performance import _executor_row
from condor.trading_agent.risk import RiskEngine, RiskLimits, RiskState


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
