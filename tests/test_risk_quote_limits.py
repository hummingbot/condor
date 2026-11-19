"""Quote-denominated risk limits must bind, in their own units.

The first cut converted a quote cap into a percentage via
``entry_stop_basis_usd``, while the journal divides the real loss by the live
exposure. Those are two different denominators: with a basis of 10, an exposure
of 100 and ``shutdown_drawdown_quote: 2``, a loss of 3 became a 20% threshold
and read as 3%, so the cap could be exceeded without the shutdown firing. A cap
stated in quote is compared against the drawdown stated in quote, and no basis
is involved at all.
"""

import unittest

from condor.agents.risk import RiskEngine, RiskLimits


class _Tracker:
    """The engine reads two things: the drawdown as a percent, and in quote."""

    def __init__(self, pct, quote=None):
        self._pct = pct
        self._quote = pct if quote is None else quote

    def get_drawdown_pct_checked(self):
        return self._pct, True

    def get_drawdown_quote_checked(self):
        return self._quote, True


def _state(
    limits: RiskLimits, drawdown_pct: float, drawdown_quote: float | None = None
):
    engine = RiskEngine(limits=limits)
    return engine.get_state(
        _Tracker(drawdown_pct, drawdown_quote),
        nothing_at_stake=True,
        reading_in_hand=True,
    )


class RiskQuoteLimitsTest(unittest.TestCase):
    def test_quote_caps_stay_in_quote(self):
        limits = RiskLimits.from_dict({"entry_stop_loss_usd": 1})
        self.assertEqual(limits.drawdown_limits_pct(), (-1.0, -1.0))
        self.assertEqual(limits.drawdown_quote_limits(), (1.0, -1.0))

    def test_a_quote_cap_needs_no_basis(self):
        limits = RiskLimits.from_dict({"entry_stop_loss_usd": 1})
        self.assertEqual(limits.unrecognized, {})

    def test_entry_stop_usd_blocks_at_the_amount(self):
        limits = RiskLimits.from_dict({"entry_stop_loss_usd": 1})
        self.assertFalse(_state(limits, 0.0, 0.9).is_blocked)
        state = _state(limits, 0.0, 1.1)
        self.assertTrue(state.is_blocked)
        self.assertIn("exceeds limit", state.block_reason)

    def test_max_global_drawdown_quote_blocks(self):
        limits = RiskLimits.from_dict({"max_global_drawdown_quote": 1})
        self.assertTrue(_state(limits, 0.0, 1.5).is_blocked)

    def test_shutdown_drawdown_quote_winds_down(self):
        limits = RiskLimits.from_dict({"shutdown_drawdown_quote": 2})
        state = _state(limits, 0.0, 2.5)
        self.assertTrue(state.should_shutdown)
        self.assertIn("shutdown limit", state.shutdown_reason)

    def test_a_small_loss_on_a_large_book_still_winds_down(self):
        """The defect Greptile found: 3 lost against a cap of 2, on a 100 book.

        Scaled by a basis of 10 the cap read as 20% and the loss as 3% -- no
        shutdown, though the cap was passed.
        """
        limits = RiskLimits.from_dict({"shutdown_drawdown_quote": 2})
        state = _state(limits, drawdown_pct=3.0, drawdown_quote=3.0)
        self.assertTrue(state.should_shutdown)
        self.assertIn("3.00", state.shutdown_reason)

    def test_the_tightest_limit_wins_within_each_unit(self):
        limits = RiskLimits.from_dict({"max_drawdown_pct": 5, "entry_stop_loss_usd": 3})
        self.assertEqual(limits.drawdown_limits_pct()[0], 5)
        self.assertEqual(limits.drawdown_quote_limits()[0], 3)
        self.assertTrue(_state(limits, 6, 0).is_blocked)
        self.assertTrue(_state(limits, 0, 4).is_blocked)

    def test_a_configured_pack_produces_no_unenforced_entry(self):
        limits = RiskLimits.from_dict(
            {
                "max_position_size_quote": 100000,
                "max_open_executors": 2,
                "entry_stop_loss_pct": 10,
                "entry_stop_loss_usd": 1,
                "max_drawdown_pct": 10,
                "max_global_drawdown_quote": 1,
                "shutdown_drawdown_pct": 15,
                "shutdown_drawdown_quote": 2,
            }
        )
        self.assertEqual(limits.unrecognized, {})
        self.assertEqual(limits.drawdown_limits_pct(), (10.0, 15.0))
        self.assertEqual(limits.drawdown_quote_limits(), (1.0, 2.0))

    def test_no_quote_reading_leaves_the_quote_caps_quiet(self):
        """A tracker without the quote reading must not invent one."""
        limits = RiskLimits.from_dict({"shutdown_drawdown_quote": 2})
        engine = RiskEngine(limits=limits)

        class _PctOnly:
            def get_drawdown_pct_checked(self):
                return 1.0, True

        state = engine.get_state(
            _PctOnly(), nothing_at_stake=True, reading_in_hand=True
        )
        self.assertFalse(state.should_shutdown)


if __name__ == "__main__":
    unittest.main()
