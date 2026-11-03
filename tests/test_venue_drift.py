"""Unit tests for the venue check ([[FEAT-113]]).

The comparison is pure, so this file is the whole feature's correctness: the
five verdicts, the sum across controllers, the HEDGE/ONEWAY net, the tolerance
boundary, and the one rule that matters most — a venue that did not answer is
never scored as agreement.
"""

from condor import venue_drift
from condor.venue_drift import (
    ABS_TOLERANCE_QUOTE,
    REL_TOLERANCE,
    VERDICTS,
    check,
    drifting,
    summarize,
    worst_quote,
)


def _held(
    pair="SOL-PERP",
    amount=10.0,
    side="LONG",
    controller="brigado.mm_1",
    account="master",
    connector="binance_perpetual",
    price=100.0,
):
    """A ``PositionHold`` row as ``get_positions_summary`` returns it."""
    return {
        "account_name": account,
        "connector_name": connector,
        "trading_pair": pair,
        "position_side": side,
        "net_amount_base": amount,
        "buy_breakeven_price": price,
        "controller_id": controller,
        "executor_ids": ["e1"],
    }


def _venue(
    pair="SOL-PERP",
    amount=10.0,
    side="LONG",
    account="master",
    connector="binance_perpetual",
    price=100.0,
):
    """An exchange row as ``/trading/positions`` returns it."""
    return {
        "account_name": account,
        "connector_name": connector,
        "trading_pair": pair,
        "side": side,
        "amount": amount,
        "entry_price": price,
        "unrealized_pnl": 0.0,
        "leverage": 5,
    }


def _one(report):
    assert len(report.rows) == 1, report.rows
    return report.rows[0]


# ── The five verdicts ──


def test_verdicts_are_the_five_named():
    assert VERDICTS == ("agreed", "mismatch", "ghost", "orphan", "unanswered")


def test_agreed_when_both_sides_say_the_same_thing():
    report = check([_held()], [_venue()])
    row = _one(report)
    assert row.verdict == "agreed"
    assert row.delta_base == 0.0
    assert report.trusted
    assert drifting(report) == ()


def test_mismatch_names_both_sizes_and_the_delta():
    report = check([_held(amount=100.0)], [_venue(amount=90.0)])
    row = _one(report)
    assert row.verdict == "mismatch"
    assert row.tracked_base == 100.0
    assert row.venue_base == 90.0
    assert row.delta_base == 10.0
    assert row.delta_quote == 1000.0  # 10 base × the venue's entry price
    assert len(drifting(report)) == 1


def test_ghost_is_tracked_and_not_on_the_venue():
    report = check([_held(amount=1542.0, side="SHORT")], [])
    row = _one(report)
    assert row.verdict == "ghost"
    assert row.tracked_base == -1542.0
    assert row.venue_base == 0.0
    assert row.delta_base == -1542.0


def test_orphan_is_on_the_venue_and_tracked_by_nobody():
    report = check([], [_venue(amount=12.6)])
    row = _one(report)
    assert row.verdict == "orphan"
    assert row.tracked_base == 0.0
    assert row.venue_base == 12.6
    assert row.controller_ids == ()


def test_unanswered_venue_is_never_agreement():
    report = check([_held()], None, reason="connection reset by peer")
    row = _one(report)
    assert row.verdict == "unanswered"
    assert report.trusted is False
    assert "connection reset" in report.reason
    # Never a delta_quote of 0.0 standing in for unknown.
    assert row.delta_quote is None
    assert drifting(report) == (row,)


def test_unanswered_with_nothing_tracked_is_still_untrusted():
    report = check([], None, reason="timeout")
    assert report.trusted is False
    assert report.rows == ()
    assert worst_quote(report) is None


def test_unanswered_defaults_its_reason():
    assert check([], None).reason == "venue did not answer"


# ── Rule 1: sum the tracked side across controllers ──


def test_two_controllers_one_pair_make_one_row_and_no_drift():
    tracked = [
        _held(amount=6.0, controller="brigado.mm_1"),
        _held(amount=4.0, controller="brigado.mm_2"),
    ]
    report = check(tracked, [_venue(amount=10.0)])
    row = _one(report)
    assert row.verdict == "agreed"
    assert row.tracked_base == 10.0
    assert set(row.controller_ids) == {"brigado.mm_1", "brigado.mm_2"}


def test_two_accounts_on_the_same_pair_stay_two_rows():
    report = check(
        [_held(account="a1"), _held(account="a2")],
        [_venue(account="a1"), _venue(account="a2")],
    )
    assert len(report.rows) == 2
    assert report.accounts == ("a1", "a2")


# ── Rule 2: signed net per key, never per side ──


def test_hedge_mode_longs_and_shorts_net_before_comparing():
    """A venue holding both legs nets to what a ONEWAY tracked book says."""
    tracked = [_held(amount=4.0, side="LONG")]
    venue = [
        _venue(amount=10.0, side="LONG"),
        _venue(amount=6.0, side="SHORT"),
    ]
    report = check(tracked, venue)
    row = _one(report)
    assert row.venue_base == 4.0
    assert row.verdict == "agreed"
    assert set(row.sides) == {"long", "short"}


def test_an_already_signed_short_and_an_unsigned_short_agree():
    report = check(
        [_held(amount=-25.0, side="SHORT")], [_venue(amount=25.0, side="SHORT")]
    )
    row = _one(report)
    assert row.tracked_base == -25.0
    assert row.venue_base == -25.0
    assert row.verdict == "agreed"


def test_a_flat_side_contributes_nothing():
    report = check([_held(amount=7.0, side="FLAT")], [])
    row = _one(report)
    assert row.tracked_base == 0.0
    assert row.verdict == "agreed"


# ── The tolerance boundary ──


def test_drift_inside_the_relative_tolerance_reads_agreed():
    tracked_base = 1000.0
    inside = tracked_base * (1 - REL_TOLERANCE / 2)
    row = _one(check([_held(amount=tracked_base)], [_venue(amount=inside)]))
    assert row.verdict == "agreed"


def test_drift_outside_both_tolerances_reads_mismatch():
    # 5% off and worth far more than a dollar.
    row = _one(check([_held(amount=1000.0)], [_venue(amount=950.0)]))
    assert row.verdict == "mismatch"


def test_a_sub_dollar_delta_is_dust_however_relatively_large():
    """Under ABS_TOLERANCE_QUOTE of notional, whichever tolerance is kinder."""
    row = _one(
        check(
            [_held(amount=0.02, price=10.0)],
            [_venue(amount=0.01, price=10.0)],
        )
    )
    assert abs(row.delta_quote) < ABS_TOLERANCE_QUOTE
    assert row.verdict == "agreed"


# ── Pricing: no statement is not zero ──


def test_delta_quote_is_none_when_neither_side_priced_it():
    row = _one(
        check(
            [_held(amount=100.0, price=0)],
            [_venue(amount=50.0, price=0)],
        )
    )
    assert row.delta_quote is None
    assert row.verdict == "mismatch"
    assert worst_quote(check([_held(amount=100.0, price=0)], [])) is None


def test_the_venue_price_wins_over_the_tracked_breakeven():
    row = _one(
        check([_held(amount=2.0, price=50.0)], [_venue(amount=1.0, price=200.0)])
    )
    assert row.delta_quote == 200.0


def test_the_tracked_breakeven_prices_a_ghost():
    row = _one(check([_held(amount=2.0, price=50.0)], []))
    assert row.delta_quote == 100.0


# ── Reading a report ──


def test_worst_quote_is_the_largest_absolute_drift():
    report = check(
        [_held(pair="A-PERP", amount=10.0), _held(pair="B-PERP", amount=1.0)],
        [_venue(pair="A-PERP", amount=8.0), _venue(pair="B-PERP", amount=5.0)],
    )
    # A drifts by 2 × 100 = 200; B drifts by -4 × 100 = -400.
    assert worst_quote(report) == 400.0


def test_drifting_narrows_to_the_controllers_a_caller_owns():
    report = check(
        [
            _held(pair="MINE-PERP", amount=10.0, controller="brigado.mm_1"),
            _held(pair="THEIRS-PERP", amount=10.0, controller="other.strat_3"),
        ],
        [],
    )
    assert len(drifting(report)) == 2
    mine = drifting(report, ["brigado.mm_1"])
    assert [r.pair for r in mine] == ["MINE-PERP"]
    assert worst_quote(report, ["brigado.mm_1"]) == 1000.0


def test_an_orphan_is_never_claimed_by_anyone():
    report = check([], [_venue(amount=5.0)])
    assert drifting(report, ["brigado.mm_1"]) == ()


# ── The prompt block ──


def test_summary_names_every_drifting_row_with_both_sizes():
    report = check(
        [
            _held(pair="DOGE-PERP", amount=-25460.0, side="SHORT", price=0.1),
            _held(pair="XRP-PERP", amount=-1542.0, side="SHORT", price=2.0),
            _held(pair="OK-PERP", amount=10.0),
        ],
        [
            _venue(pair="DOGE-PERP", amount=25000.0, side="SHORT", price=0.1),
            _venue(pair="SOL-PERP", amount=12.6, price=200.0),
            _venue(pair="OK-PERP", amount=10.0),
        ],
    )
    text = summarize(report, ["brigado.mm_1"])
    assert "Book vs venue" in text
    assert "MISMATCH" in text and "DOGE-PERP" in text
    assert "GHOST" in text and "XRP-PERP" in text
    assert "ORPHAN" in text and "SOL-PERP" in text
    assert "OK-PERP" not in text  # agreed rows are counted, not listed
    assert "1 agreed." in text
    assert "2 of 3 involves your controllers." in text
    assert "← yours" in text


def test_summary_of_an_unanswered_venue_says_so_loudly():
    text = summarize(check([_held()], None, reason="502 Bad Gateway"))
    assert "DID NOT ANSWER" in text
    assert "502 Bad Gateway" in text
    assert "UNANSWERED" in text
    assert "agreed" not in text.lower()


def test_summary_of_an_empty_book_is_one_line():
    assert "nothing tracked" in summarize(check([], []))


def test_summary_when_everything_agrees_lists_nothing():
    text = summarize(check([_held()], [_venue()]))
    assert "all 1 agreed." in text
    assert "SOL-PERP" not in text


def test_summary_without_controllers_omits_the_ownership_tail():
    text = summarize(check([_held(amount=100.0)], [_venue(amount=50.0)]))
    assert "your controllers" not in text
    assert "← yours" not in text


# ── Robustness: neither side is trusted to be well-formed ──


def test_junk_rows_are_ignored_rather_than_crashing_the_check():
    report = check(
        [_held(), "not a row", None],  # type: ignore[list-item]
        [_venue(), 42],  # type: ignore[list-item]
    )
    assert _one(report).verdict == "agreed"


def test_unparseable_amounts_read_as_flat():
    report = check([_held(amount="abc")], [])
    assert _one(report).tracked_base == 0.0


def test_module_does_no_io():
    """The check is pure: it imports nothing that talks to a client."""
    source = venue_drift.__file__
    with open(source) as fh:
        text = fh.read()
    assert "import httpx" not in text
    assert "await " not in text
