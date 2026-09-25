"""Unit tests for the venue check ([[FEAT-113]]).

The comparison is pure, so this file is the whole feature's correctness: the
five verdicts, the sum across controllers, the HEDGE/ONEWAY net, the tolerance
boundary, and the one rule that matters most — a venue that did not answer is
never scored as agreement.
"""

import pytest

from condor import venue_drift
from condor.venue_drift import (
    ABS_TOLERANCE_QUOTE,
    REL_TOLERANCE,
    VERDICTS,
    check,
    drifting,
    summarize,
    tracked_from_active,
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


# ── CORR-708: running executors' open inventory on the tracked side ──


def _running(
    kind="grid_executor",
    pair="SOL-USDT",
    side="TradeType.BUY",
    quote=51.0,
    price=100.0,
    controller="s7.grid_1",
    connector="binance_perpetual",
    status="RUNNING",
    ex_id="j5B2VAi4",
):
    """A running executor as ``search_executors`` returns it (in-memory shape)."""
    info = {"side": side, "current_position_average_price": price}
    row = {
        "executor_id": ex_id,
        "executor_type": kind,
        "account_name": "master",
        "connector_name": connector,
        "trading_pair": pair,
        "controller_id": controller,
        "status": status,
        "side": "BUY",
        "custom_info": info,
    }
    if kind == "grid_executor":
        info["position_size_quote"] = quote
        row["filled_amount_quote"] = 999.0  # traded volume, not inventory
    else:
        row["filled_amount_quote"] = quote
    return row


def test_a_running_long_grid_agrees_with_its_venue_position_and_is_mine():
    rows, unmeasured = tracked_from_active([_running()])
    assert unmeasured == []
    report = check(rows, [_venue(pair="SOL-USDT", amount=0.51)])
    row = _one(report)
    assert row.verdict == "agreed"
    assert row.tracked_base == pytest.approx(0.51)
    assert row.controller_ids == ("s7.grid_1",)
    assert drifting(report, ["s7.grid_1"]) == ()


def test_a_running_short_position_executor_nets_negative():
    rows, _ = tracked_from_active(
        [_running(kind="position_executor", side="SELL", quote=200.0, price=50.0)]
    )
    assert rows[0]["position_side"] == "SHORT"
    report = check(rows, [_venue(pair="SOL-USDT", side="SHORT", amount=4.0)])
    row = _one(report)
    assert row.tracked_base == pytest.approx(-4.0)
    assert row.verdict == "agreed"


def test_running_inventory_sums_with_a_held_position_on_the_same_pair():
    rows, _ = tracked_from_active([_running(pair="SOL-PERP", quote=500.0)])
    report = check([_held(amount=10.0)] + rows, [_venue(amount=15.0)])
    row = _one(report)
    assert row.verdict == "agreed"
    assert set(row.controller_ids) == {"brigado.mm_1", "s7.grid_1"}


def test_a_venue_position_no_running_or_held_executor_explains_is_an_orphan():
    rows, _ = tracked_from_active([_running(pair="ETH-USDT")])
    report = check(rows, [_venue(pair="SOL-USDT", amount=0.51)])
    by_pair = {r.pair: r.verdict for r in report.rows}
    assert by_pair == {"ETH-USDT": "ghost", "SOL-USDT": "orphan"}


def test_a_running_executor_covering_only_part_of_the_venue_is_a_mismatch():
    rows, _ = tracked_from_active([_running(quote=51.0)])
    report = check(rows, [_venue(pair="SOL-USDT", amount=5.0)])
    assert _one(report).verdict == "mismatch"


def test_a_running_executor_with_nothing_filled_contributes_nothing():
    rows, unmeasured = tracked_from_active([_running(quote=0.0)])
    assert rows == [] and unmeasured == []
    assert _one(check(rows, [_venue(pair="SOL-USDT", amount=0.51)])).verdict == (
        "orphan"
    )


def test_unreadable_running_executors_are_named_and_never_counted():
    """No guessing: an executor whose inventory cannot be read explains nothing."""
    no_price = _running(ex_id="p1", price=0.0)
    no_side = _running(ex_id="s1", side=None)
    no_side["side"] = None
    no_amount = _running(ex_id="a1")
    del no_amount["custom_info"]["position_size_quote"]
    dca = _running(kind="dca_executor", ex_id="d1")
    stale_db_record = _running(ex_id="db1")
    stale_db_record["custom_info"] = None  # a RUNNING DB row has no final_state

    rows, unmeasured = tracked_from_active(
        [no_price, no_side, no_amount, dca, stale_db_record]
    )
    assert rows == []
    assert unmeasured == [
        "p1 (grid SOL-USDT)",
        "s1 (grid SOL-USDT)",
        "a1 (grid SOL-USDT)",
        "d1 (dca SOL-USDT)",
        "db1 (grid SOL-USDT)",
    ]
    report = check(rows, [_venue(pair="SOL-USDT", amount=0.51)], unmeasured=unmeasured)
    assert _one(report).verdict == "orphan"
    assert "5 active executor(s) expose no readable inventory" in summarize(report)
    assert "d1 (dca SOL-USDT)" in summarize(report)


def test_spot_and_inactive_executors_are_out_of_scope():
    rows, unmeasured = tracked_from_active(
        [
            _running(connector="binance"),  # spot: the venue side is perps only
            _running(kind="lp_executor", connector="meteora/clmm"),
            _running(status="TERMINATED"),
            _running(kind="position_executor", status="TERMINATED"),
            _running(connector="binance", status="SHUTTING_DOWN"),
            "not a row",  # type: ignore[list-item]
        ]
    )
    assert rows == [] and unmeasured == []


# ── CORR-710: a shutting-down executor's fills are still on the venue ──


def test_a_shutting_down_grid_still_counts_its_residual_inventory():
    """Its close order has not flattened it yet: the fills are the grid's, not an
    orphan's. ``position_size_quote`` still reads the residual open base."""
    rows, unmeasured = tracked_from_active(
        [_running(status="SHUTTING_DOWN", quote=76.5, price=150.0)]
    )
    assert unmeasured == []
    assert len(rows) == 1
    assert rows[0]["position_side"] == "LONG"
    assert rows[0]["net_amount_base"] == pytest.approx(0.51)
    assert rows[0]["controller_id"] == "s7.grid_1"
    report = check(rows, [_venue(pair="SOL-USDT", amount=0.51)])
    assert _one(report).verdict == "agreed"


def test_a_shutting_down_position_executor_is_named_never_read():
    """Its ``filled_amount_quote`` now includes the close order's fills, so it
    is not inventory: the executor is named, and the orphan it may explain
    stays an orphan rather than a guessed agreement."""
    stopping = _running(
        kind="position_executor", status="SHUTTING_DOWN", quote=76.5, price=150.0
    )
    rows, unmeasured = tracked_from_active([stopping])
    assert rows == []
    assert unmeasured == ["j5B2VAi4 (position SOL-USDT) shutting down"]
    report = check(rows, [_venue(pair="SOL-USDT", amount=0.51)], unmeasured=unmeasured)
    assert _one(report).verdict == "orphan"
    text = summarize(report)
    assert "1 active executor(s) expose no readable inventory" in text
    assert "j5B2VAi4 (position SOL-USDT) shutting down" in text


def test_side_encodings_all_read():
    for side, want in (
        ("TradeType.BUY", "LONG"),
        ("BUY", "LONG"),
        (1, "LONG"),
        ("TradeType.SELL", "SHORT"),
        ("sell", "SHORT"),
        (2, "SHORT"),
    ):
        rows, _ = tracked_from_active([_running(side=side)])
        assert rows[0]["position_side"] == want, side


def test_held_positions_behave_as_before_without_running_executors():
    rows, unmeasured = tracked_from_active([])
    assert rows == [] and unmeasured == []
    report = check([_held(amount=10.0)] + rows, [_venue(amount=10.0)])
    assert _one(report).verdict == "agreed"
    assert report.unmeasured == ()
    assert "active executor" not in summarize(report)


def test_unmeasured_survives_an_unanswered_venue():
    report = check([_held()], None, reason="down", unmeasured=["x (dca P)"])
    assert report.unmeasured == ("x (dca P)",)
    assert "x (dca P)" in summarize(report)
