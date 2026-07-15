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
def test_static_gallery_is_consistent_and_data_driven(reports_dir, history_hours):
    datasets = gallery.make_gallery_data(history_hours)
    builder = reports.ReportBuilder("Gallery")
    gallery.build_static_gallery(builder, datasets)

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

    assert document.count('<div class="section report-section-heading">') == 6
    assert "Builder Code" not in document
    assert "Estimated BTC volume footprint" in document
    assert "not actual trade-side records" in document
    assert len(spec["datasets"]["candles"]) == history_hours
    assert charts["trade-cost-bars"]["aggregate"] == "mean"
    assert charts["trade-cost-bars"]["category_order"] == [
        "Under $2k",
        "$2k-$5k",
        "$5k-$10k",
        "$10k+",
    ]
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
