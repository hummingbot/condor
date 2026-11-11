"""The quote-denominated risk limits must actually bind.

A pack that writes `entry_stop_loss_usd: 1` with `entry_stop_basis_usd: 10` has
stated a 10% stop. Before this, RiskLimits only knew the *_pct form, so the key
was reported at startup as "bounds nothing" -- and it bounded nothing.
"""

import unittest

from condor.agents.risk import RiskEngine, RiskLimits


class _Tracker:
    """Minimal tracker: the only thing the engine reads is the drawdown."""

    def __init__(self, pct):
        self._pct = pct

    def get_drawdown_pct_checked(self):
        return self._pct, True


def _state(limits: RiskLimits, drawdown_pct: float):
    engine = RiskEngine(limits=limits)
    return engine.get_state(
        _Tracker(drawdown_pct), nothing_at_stake=True, reading_in_hand=True
    )


class RiskQuoteLimitsTest(unittest.TestCase):
    def test_entry_stop_usd_becomes_a_percentage(self):
        limits = RiskLimits.from_dict(
            {"entry_stop_loss_usd": 1, "entry_stop_basis_usd": 10}
        )
        self.assertEqual(limits.drawdown_limits_pct(), (10.0, -1.0))
        self.assertFalse(_state(limits, 9.9).is_blocked)
        self.assertTrue(_state(limits, 10.1).is_blocked)

    def test_max_global_drawdown_quote_blocks(self):
        limits = RiskLimits.from_dict(
            {"max_global_drawdown_quote": 1, "entry_stop_basis_usd": 10}
        )
        self.assertTrue(_state(limits, 11.0).is_blocked)
        self.assertIn("exceeds limit", _state(limits, 11.0).block_reason)

    def test_shutdown_drawdown_quote_winds_down(self):
        limits = RiskLimits.from_dict(
            {"shutdown_drawdown_quote": 2, "entry_stop_basis_usd": 10}
        )
        state = _state(limits, 21.0)
        self.assertTrue(state.should_shutdown)
        self.assertIn("shutdown limit", state.shutdown_reason)

    def test_the_tightest_limit_wins(self):
        limits = RiskLimits.from_dict(
            {
                "max_drawdown_pct": 5,
                "entry_stop_loss_usd": 3,
                "entry_stop_basis_usd": 10,
            }
        )
        self.assertEqual(limits.drawdown_limits_pct()[0], 5)
        self.assertTrue(_state(limits, 6).is_blocked)

    def test_without_a_basis_it_is_reported_not_silently_dropped(self):
        limits = RiskLimits.from_dict({"entry_stop_loss_usd": 1})
        self.assertEqual(limits.drawdown_limits_pct(), (-1.0, -1.0))
        self.assertIn("entry_stop_loss_usd", limits.unrecognized)
        self.assertIn(
            "entry_stop_basis_usd", limits.unrecognized["entry_stop_loss_usd"]
        )

    def test_a_configured_pack_no_longer_warns_as_unenforced(self):
        """The real delta-raptor block must produce no 'bounds nothing' entry."""
        limits = RiskLimits.from_dict(
            {
                "max_position_size_quote": 100000,
                "max_open_executors": 2,
                "entry_stop_loss_pct": 10,
                "entry_stop_loss_usd": 1,
                "entry_stop_basis_usd": 10,
                "max_drawdown_pct": 10,
                "max_global_drawdown_quote": 1,
                "shutdown_drawdown_pct": 15,
                "shutdown_drawdown_quote": 2,
            }
        )
        self.assertEqual(limits.unrecognized, {})
        self.assertEqual(limits.drawdown_limits_pct(), (10.0, 15.0))


if __name__ == "__main__":
    unittest.main()
