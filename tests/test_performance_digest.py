"""Tests for strategy performance digest analytics."""

import pytest
from datetime import datetime, timedelta, timezone

from condor.trading_agent.performance_digest import (
    build_digest,
    classify_entry_class,
    compute_category_stats,
    compute_window_stats,
    exit_bucket,
    format_performance_report,
    format_performance_report_html,
    generate_insights,
    parse_journal_decisions,
    row_has_reliable_notional,
    row_notional,
    side_label,
)


def _closed_row(
    pnl: float,
    *,
    closed_at: str,
    close_type: str = "TAKE_PROFIT",
    pair: str = "BTC-USD",
    side: str = "1",
    amount: float = 200.0,
) -> dict:
    return {
        "id": f"id-{pair}-{closed_at}",
        "status": "TERMINATED",
        "pnl": pnl,
        "close_type": close_type,
        "closed_at": closed_at,
        "created_at": closed_at,
        "pair": pair,
        "side": side,
        "amount": amount,
    }


def test_exit_bucket():
    assert exit_bucket("STOP_LOSS") == "SL"
    assert exit_bucket("TAKE_PROFIT") == "TP"
    assert exit_bucket("EARLY_STOP") == "Agent"


def test_side_label():
    assert side_label("1") == "LONG"
    assert side_label("2") == "SHORT"


def test_parse_journal_decisions():
    text = """## Decisions

- **#12** (14:30) entry_class=formal pair=ASTER-USD trigger=long2
- **#13** (14:35) entry_class=hold hold_reason=no_formal_signal
- **#14** (15:00) entry_class=regime_adaptive_half_size pair=ETH-USD trigger=adaptive_long
"""
    entries = parse_journal_decisions(text)
    assert len(entries) == 2
    assert entries[0]["entry_class"] == "formal"
    assert entries[0]["pair"] == "ASTER-USD"
    assert entries[1]["entry_class"] == "regime_adaptive_half_size"


def test_compute_window_stats_filters_by_time():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        _closed_row(10.0, closed_at=(now - timedelta(minutes=30)).isoformat()),
        _closed_row(-5.0, closed_at=(now - timedelta(hours=2)).isoformat()),
        _closed_row(3.0, closed_at=(now - timedelta(days=3)).isoformat()),
    ]
    windows = compute_window_stats(rows, now=now)
    assert windows["1h"].count == 1
    assert windows["1h"].pnl == 10.0
    assert windows["24h"].count == 2
    assert windows["7d"].count == 3
    assert windows["1h"].return_pct == 5.0  # 10/200*100


def test_compute_category_stats():
    journal = [
        {"entry_class": "formal", "pair": "BTC-USD"},
        {"entry_class": "regime_adaptive_half_size", "pair": "ETH-USD"},
    ]
    rows = [
        _closed_row(8.0, closed_at="2026-05-25T10:00:00+00:00", pair="BTC-USD", amount=200),
        _closed_row(-4.0, closed_at="2026-05-25T09:00:00+00:00", pair="ETH-USD", amount=100, close_type="STOP_LOSS"),
    ]
    by_exit, by_entry, by_side = compute_category_stats(rows, journal, total_amount_quote=200)
    assert by_exit["TP"].count == 1
    assert by_exit["SL"].count == 1
    assert by_entry["Formal"].pnl == 8.0
    assert by_entry["Adaptive"].pnl == -4.0
    assert by_side["LONG"].count == 2


def test_classify_entry_class_heuristic_adaptive():
    row = _closed_row(1.0, closed_at="2026-05-25T10:00:00+00:00", amount=100)
    assert classify_entry_class(row, [], total_amount_quote=200) == "Adaptive"


def test_row_notional_fallback_for_entry_class_heuristic():
    assert row_notional({"pnl": 6.0, "amount": 0}, default_quote=200.0) == 200.0
    assert row_notional({"pnl": 6.0, "amount": 0, "net_pnl_pct": 3.0}, default_quote=999) == 200.0


def test_row_has_reliable_notional():
    assert row_has_reliable_notional({"amount": 200}) is True
    assert row_has_reliable_notional({"amount": 0, "net_pnl_pct": 1.5}) is True
    assert row_has_reliable_notional({"amount": 0, "net_pnl_pct": 0}) is False


def test_window_return_pct_ignores_unsized_trades():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        _closed_row(6.0, closed_at=(now - timedelta(hours=2)).isoformat(), amount=200),
        _closed_row(1.57, closed_at=(now - timedelta(days=5)).isoformat(), amount=0),
    ]
    windows = compute_window_stats(rows, now=now)
    ws = windows["7d"]
    assert ws.count == 2
    assert ws.pnl == pytest.approx(7.57)
    assert ws.sized_count == 1
    assert ws.return_pct == pytest.approx(3.0)  # 6 / 200 * 100
    assert windows["7d"].sized_count == 1


def test_unsized_window_shows_dash_for_return_pct():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    rows = [_closed_row(7.57, closed_at=(now - timedelta(days=5)).isoformat(), amount=0)]
    windows = compute_window_stats(rows, now=now)
    assert windows["7d"].sized_count == 0
    from condor.trading_agent.performance_digest import _fmt_pct

    assert _fmt_pct(windows["7d"].return_pct, windows["7d"].sized_count) == "—"


def test_generate_insights_no_tp_win_rate():
    from condor.trading_agent.performance_digest import BucketStats

    by_exit = {
        "SL": BucketStats("SL", count=15, pnl=-28.22, notional=3000, wins=0),
        "TP": BucketStats("TP", count=9, pnl=27.41, notional=1800, wins=9),
    }
    tips = generate_insights(by_exit, {}, {})
    assert not any("100% WR" in t or "WR" in t and "Take-profit" in t for t in tips)
    assert any("stop-losses" in t.lower() or "Stop-loss" in t for t in tips)


def test_generate_insights_adaptive_losses():
    from condor.trading_agent.performance_digest import BucketStats

    by_exit = {
        "SL": BucketStats("SL", count=3, pnl=-12.0, notional=600, wins=0),
    }
    by_entry = {
        "Adaptive": BucketStats("Adaptive", count=4, pnl=-8.0, notional=400, wins=0),
        "Formal": BucketStats("Formal", count=2, pnl=2.0, notional=400, wins=1),
    }
    by_side = {
        "LONG": BucketStats("LONG", count=3, pnl=-6.0, notional=600, wins=1),
        "SHORT": BucketStats("SHORT", count=3, pnl=4.0, notional=600, wins=2),
    }
    tips = generate_insights(by_exit, by_entry, by_side)
    assert any("Adaptive" in t for t in tips)
    assert len(tips) <= 3


def test_format_performance_report_html():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    digest = build_digest(
        slug="macdbb_scanner_aggressive_hl",
        perf_rows=[_closed_row(6.0, closed_at=(now - timedelta(hours=1)).isoformat())],
        journal_entries=[],
        total_amount_quote=200,
        now=now,
    )
    html = format_performance_report_html(digest)
    assert "<b>📊 Performance</b>" in html
    assert "<code>macdbb_scanner_aggressive_hl</code>" in html
    assert "sized trades only" in html


def test_build_digest_and_format():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        _closed_row(5.0, closed_at=(now - timedelta(hours=1)).isoformat()),
        {
            "id": "open-1",
            "status": "RUNNING",
            "pnl": 1.5,
            "pair": "SOL-USD",
            "side": "1",
            "amount": 200,
            "close_type": "",
            "closed_at": "",
        },
    ]
    digest = build_digest(
        slug="macdbb_scanner_aggressive_hl",
        perf_rows=rows,
        journal_entries=[],
        total_amount_quote=200,
        running_agent_id="macdbb_scanner_aggressive_hl_15",
        running_tick=40,
        now=now,
    )
    text = format_performance_report(digest)
    assert "PERFORMANCE" in text
    assert "macdbb_scanner_aggressive_hl_15" in text
    assert "Realized" in text
    assert digest.unrealized_pnl == 1.5
    assert digest.realized_pnl == 5.0
