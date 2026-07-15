import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import condor.reports as reports
from condor.reports import rendering
from condor.reports.footprint import (
    build_estimated_footprint_figure,
    candle_timestamps,
    estimate_volume_profile,
)


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


def test_interactive_report_embeds_safe_runtime(reports_dir):
    rows = [
        {"timestamp": "2026-01-01T00:00:00Z", "pair": "BTC-USDT", "price": 100},
        {"timestamp": "2026-01-01T01:00:00Z", "pair": "BTC-USDT", "price": 102},
    ]
    builder = reports.ReportBuilder("Interactive <Report>").manual_order()
    builder.source("routine", "report_test").tags(["safe"])
    builder.section("Overview", "Linked components")
    builder.markdown("## Safe\n<script>alert(1)</script> [bad](javascript:alert(2))")
    builder.dataset("prices", rows)
    builder.range_filter("period", "prices", "timestamp", value_type="datetime")
    builder.metric("End", "prices", "price", aggregate="last")
    builder.chart(
        "line",
        "Price",
        "prices",
        "timestamp",
        "price",
        selection_mode="drilldown",
        component_id="price-chart",
    )
    builder.data_table("prices", component_id="price-table")
    builder.drilldown("prices", component_id="price-drilldown")

    report_id = asyncio.run(builder.save())
    entry = reports.get_report(report_id)
    document = (reports_dir / entry["filename"]).read_text(encoding="utf-8")
    spec = _report_spec(document)

    assert "Interactive &lt;Report&gt;" in document
    assert "<h2>Safe</h2>" in document
    assert "<script>alert(1)</script>" not in document
    assert "javascript:" not in document
    assert "CondorReportRuntime" in document
    assert spec["datasets"] == {"prices": rows}
    assert {component["id"] for component in spec["components"]} == {
        "period",
        "metric-4",
        "price-chart",
        "price-table",
        "price-drilldown",
    }
    assert not list(reports_dir.glob("*.tmp"))


def test_live_report_updates_in_place_and_recovers_after_deletion(reports_dir):
    live = reports.LiveReport("Live", source_name="live_test", auto_refresh_seconds=3)
    live.builder.markdown("First")
    report_id = asyncio.run(live.update())
    live.clear()
    live.builder.markdown("Second")
    assert asyncio.run(live.update()) == report_id

    entry = reports.get_report(report_id)
    document = (reports_dir / entry["filename"]).read_text(encoding="utf-8")
    assert "Second" in document
    assert "First" not in document
    assert _report_spec(document)["auto_refresh_seconds"] == 3

    assert asyncio.run(reports.delete_report(report_id)) is True
    live.clear()
    live.builder.markdown("Replacement")
    replacement_id = asyncio.run(live.update())
    assert replacement_id != report_id
    assert reports.get_report(replacement_id) is not None


def test_footprint_filters_invalid_rows_and_keeps_zero_visible():
    candles = [
        {
            "timestamp": "1767225600000",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 101,
            "volume": 10,
        },
        {
            "timestamp": "1767225660000",
            "open": 101,
            "high": 102,
            "low": 100,
            "close": 102,
            "volume": 20,
        },
        {
            "timestamp": "bad",
            "open": "bad",
            "high": None,
            "low": None,
            "close": None,
            "volume": 999,
        },
    ]
    prices, buys, sells = estimate_volume_profile(candles, buckets=4)
    figure = build_estimated_footprint_figure(candles, buckets=4)

    assert len(prices) == len(buys) == len(sells) == 4
    assert sum(buys) + sum(sells) == pytest.approx(30)
    assert figure is not None
    assert figure.layout.xaxis2.range[0] == 0
    assert figure.layout.yaxis.range[0] > 0
    assert all(hasattr(value, "tzinfo") for value in candle_timestamps(candles[:2]))
    assert candle_timestamps(candles) == [0, 1, 2]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_report_runtime_core_with_node(tmp_path):
    runtime_path = Path(rendering.__file__).with_name("report_runtime.js")
    harness = tmp_path / "runtime_test.js"
    harness.write_text(
        "const fs = require('fs');\n"
        "global.window = {location: {protocol: 'file:'}, innerWidth: 1200};\n"
        "global.document = {documentElement: {classList: {contains: () => false}}, getElementById: (id) => id === 'condor-report-spec' ? {textContent: '{\"datasets\":{},\"components\":[]}'} : null};\n"
        f"eval(fs.readFileSync({json.dumps(str(runtime_path))}, 'utf8'));\n"
        "const core = window.CondorReportRuntime.__test;\n"
        "const state = core.initialRangeState(null, 0.8, 52.1, 'number');\n"
        "if (state.min !== 0.8 || state.max !== 52.1 || state.touched !== false) process.exit(1);\n"
        "const rows = [{id: 'a', row: {name: 'E1', pnl: -10, label: '-$10', sign: 'Loss'}}];\n"
        "const trace = core.chartTraces({chart_type: 'horizontal_bar', x: 'name', y: 'pnl', color: 'sign', encodings: {text: 'label'}, title: 'PnL'}, rows)[0];\n"
        "if (trace.textposition !== 'inside') process.exit(2);\n"
        "const shape = core.referenceLineShape({axis: 'x', value: 80, label: 'Limit'});\n"
        "if (shape.x0 !== 80 || shape.name !== 'Limit' || !shape.showlegend) process.exit(3);\n",
        encoding="utf-8",
    )
    subprocess.run(["node", str(harness)], check=True)
