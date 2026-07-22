import asyncio
import json
import re

import pytest

import condor.reports as reports
from condor.reports import rendering
from routines import report_component_gallery as gallery


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(reports, "INDEX_FILE", tmp_path / "reports_index.json")
    monkeypatch.setattr(
        rendering, "plotly_script", lambda: "<script>window.Plotly={};</script>"
    )
    return tmp_path


def _report_spec(document: str) -> dict:
    match = re.search(
        r'<script id="condor-report-spec" type="application/json">(.*?)</script>',
        document,
        re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


@pytest.mark.parametrize("history_hours", [72, 168])
def test_component_gallery_is_consistent_and_data_driven(reports_dir, history_hours):
    datasets = gallery.make_gallery_data(history_hours)
    builder = reports.ReportBuilder("Gallery")
    gallery.build_component_gallery(builder, datasets)

    change_24h = (
        datasets["candles"][-1]["close"] / datasets["candles"][-25]["close"] - 1
    ) * 100
    price_kpi = next(
        section
        for section in builder._sections
        if section.get("type") == "kpi" and section.get("label") == "Latest BTC Price"
    )
    assert price_kpi["trend"] == ("up" if change_24h >= 0 else "down")

    report_id = asyncio.run(builder.save())
    entry = reports.get_report(report_id)
    document = (reports_dir / entry["filename"]).read_text(encoding="utf-8")
    spec = _report_spec(document)
    charts = {
        component["id"]: component
        for component in spec["components"]
        if component["type"] == "chart"
    }

    assert document.count('<div class="section report-section-heading">') == 7
    assert document.count('<div class="section plotly-chart report-panel">') == 4
    assert "Builder Code" not in document
    assert "BTC-USDT Hourly Return Distribution" in document
    assert "Market Heatmap: 24h Trend by Volume" in document
    assert "Cross-exchange BTC-USDT order books" in document
    assert "Estimated BTC volume footprint" in document
    assert "not actual trade-side records" in document
    assert len(spec["datasets"]["candles"]) == history_hours
    return_stats = gallery._return_distribution_stats(datasets["candles"])
    expected_returns = [row["return_pct"] for row in datasets["candles"][1:]]
    assert return_stats["count"] == history_hours - 1
    assert return_stats["returns"] == expected_returns
    return_figure = gallery._build_return_distribution_figure(datasets["candles"])
    assert list(return_figure.data[0].x) == expected_returns
    assert return_figure.data[0].nbinsx == 40
    assert len(spec["datasets"]["market_scan"]) == 18
    assert len(spec["datasets"]["cross_exchange_books"]) == 72

    scanner_chart = charts["market-scanner-scatter"]
    assert scanner_chart["chart_type"] == "scatter"
    assert scanner_chart["encodings"] == {
        "text": "symbol_label",
        "text_position": "top center",
        "size": "marker_size",
        "y_scale": "log",
    }
    heatmap = charts["market-trend-heatmap"]
    assert heatmap["chart_type"] == "treemap"
    assert heatmap["x"] == "symbol_label"
    assert heatmap["y"] == "volume_24h_usd"
    assert heatmap["color"] == "price_change_24h"
    assert heatmap["encodings"] == {
        "value_label": "24h Volume",
        "value_prefix": "$",
        "value_format": ",.0f",
        "color_label": "24h Change",
        "color_format": ".2f",
        "color_suffix": "%",
    }
    assert {row["classification"] for row in spec["datasets"]["market_scan"]} == {
        "Mature",
        "Degen",
        "Other",
    }

    exchange_books = spec["datasets"]["cross_exchange_books"]
    assert {row["exchange"] for row in exchange_books} == {
        "Binance",
        "KuCoin",
        "OKX",
    }
    for exchange in {row["exchange"] for row in exchange_books}:
        for side in ("Bid", "Ask"):
            levels = sorted(
                (
                    row
                    for row in exchange_books
                    if row["exchange"] == exchange and row["side"] == side
                ),
                key=lambda row: row["level"],
            )
            assert len(levels) == 12
            assert [row["cumulative_amount"] for row in levels] == sorted(
                row["cumulative_amount"] for row in levels
            )
    arb_figure = gallery._build_cross_exchange_figure(exchange_books)
    reference_mid = exchange_books[0]["reference_mid"]
    kucoin_best_bid = next(
        row
        for row in exchange_books
        if row["exchange"] == "KuCoin" and row["side"] == "Bid" and row["level"] == 1
    )
    bid_traces = [trace for trace in arb_figure.data if trace.name == "L1 Bid"]
    assert len(bid_traces) == 3
    assert bid_traces[1].x[0] == pytest.approx(
        (kucoin_best_bid["price"] / reference_mid - 1) * 10_000
    )
    quote_bps = [
        trace.x[0] for trace in arb_figure.data if trace.name in {"L1 Bid", "L1 Ask"}
    ]
    right_range = arb_figure.layout.xaxis2.range
    assert right_range[0] == pytest.approx(-right_range[1])
    assert right_range[0] < min(quote_bps)
    assert right_range[1] > max(quote_bps)
    assert arb_figure.layout.xaxis4.range == right_range
    assert arb_figure.layout.xaxis6.range == right_range
    assert len([trace for trace in arb_figure.data if trace.name == "Bids"]) == 3
    assert len(arb_figure.layout.shapes) == 6
    assert charts["trade-cost-bars"]["aggregate"] == "mean"
    assert charts["trade-cost-bars"]["category_order"] == [
        "Under $2k",
        "$2k-$5k",
        "$5k-$10k",
        "$10k+",
    ]
    assert charts["trade-cost-box"]["chart_type"] == "box"
    assert charts["trade-cost-box"]["x"] == "liquidity"
    assert charts["trade-cost-box"]["y"] == "execution_cost_bps"
    assert charts["trade-cost-box"]["selection_mode"] == "drilldown"
    risk_chart = charts["fleet-risk-utilization"]
    assert risk_chart["encodings"]["x_range"] == [0, 90]
    assert risk_chart["encodings"]["reference_lines"][0]["label"] == (
        "At-limit threshold (80%)"
    )

    executor_ids = {row["executor_id"] for row in spec["datasets"]["executors"]}
    assert {row["executor_id"] for row in spec["datasets"]["trades"]} <= executor_ids
    assert {row["executor_id"] for row in spec["datasets"]["active_orders"]} <= (
        executor_ids
    )
    primary_bot = next(
        row
        for row in spec["datasets"]["bot_fleet"]
        if row["bot_name"] == "btc_mm_alpha"
    )
    assert primary_bot["net_pnl_quote"] == pytest.approx(
        sum(row["net_pnl_quote"] for row in spec["datasets"]["executors"]),
        abs=0.02,
    )


def test_gallery_run_uses_component_gallery_identity(reports_dir):
    result = asyncio.run(gallery.run(gallery.Config(), None))
    report_id = re.search(r"\(([a-z0-9]+)\)", result).group(1)
    entry = reports.get_report(report_id)

    assert result == f"Report Component Gallery saved ({report_id})."
    assert entry["title"] == "Report Component Gallery"
    assert "components" in entry["tags"]
    assert "static" not in entry["tags"]
