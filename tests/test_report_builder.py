import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import condor.reports as reports
from condor.reports import rendering, store
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
        rendering,
        "plotly_bundle",
        lambda: "/**\n* plotly.js v0.0.0\n*/\nwindow.Plotly={};",
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


def test_markdown_preserves_single_newlines():
    rendered = rendering.markdown_to_html(
        "**Market Analysis**\nOverall Regime: ranging\nSpread Recommendation: moderate"
    )

    assert rendered.count("<br") == 2
    assert "<strong>Market Analysis</strong>" in rendered


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
    kpi_value_rule = re.search(r"\.kpi-card \.value \{([^}]+)\}", document)
    assert kpi_value_rule
    assert "white-space: normal" in kpi_value_rule.group(1)
    assert "overflow-wrap: anywhere" in kpi_value_rule.group(1)
    assert spec["datasets"] == {"prices": rows}
    assert {component["id"] for component in spec["components"]} == {
        "period",
        "metric-4",
        "price-chart",
        "price-table",
        "price-drilldown",
    }
    assert not list(reports_dir.glob("*.tmp"))


def test_treemap_requires_values_and_rejects_aggregation():
    builder = reports.ReportBuilder("Treemap")
    builder.dataset("markets", [])
    builder.chart(
        "treemap",
        "Market Trends",
        "markets",
        "symbol",
        "volume",
        color="change",
    )

    assert builder._sections[-1]["chart_type"] == "treemap"
    with pytest.raises(ValueError, match="y is required"):
        builder.chart("treemap", "Missing Values", "markets", "symbol")
    with pytest.raises(ValueError, match="do not support aggregate"):
        builder.chart(
            "treemap",
            "Aggregated",
            "markets",
            "symbol",
            "volume",
            aggregate="sum",
        )


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


def test_cleanup_keeps_recently_updated_report(reports_dir):
    entries = [
        {
            "id": "live",
            "filename": "live.html",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-04T00:00:00Z",
        },
        {
            "id": "middle",
            "filename": "middle.html",
            "created_at": "2026-01-02T00:00:00Z",
        },
        {
            "id": "new",
            "filename": "new.html",
            "created_at": "2026-01-03T00:00:00Z",
        },
    ]
    for entry in entries:
        (reports_dir / entry["filename"]).write_text(entry["id"], encoding="utf-8")
    store._write_index(entries)

    store._cleanup_locked(max_reports=2)

    assert {entry["id"] for entry in store._read_index()} == {"live", "new"}
    assert not (reports_dir / "middle.html").exists()


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
        {
            "timestamp": "1767225720000",
            "open": 103,
            "high": 102,
            "low": 100,
            "close": 101,
            "volume": 1000,
        },
        {
            "timestamp": "1767225780000",
            "open": 101,
            "high": 102,
            "low": 100,
            "close": 99,
            "volume": 1000,
        },
    ]
    prices, buys, sells = estimate_volume_profile(candles, buckets=4)
    figure = build_estimated_footprint_figure(candles, buckets=4)

    assert len(prices) == len(buys) == len(sells) == 4
    assert sum(buys) + sum(sells) == pytest.approx(30)
    assert figure is not None
    assert len(figure.data[0].open) == 2
    assert figure.layout.xaxis2.range[0] == 0
    assert figure.layout.yaxis.range[0] > 0
    total_labels = next(
        trace for trace in figure.data if trace.name == "Total volume values"
    )
    delta_labels = next(
        trace for trace in figure.data if trace.name == "Net delta values"
    )
    assert total_labels.textfont.size == delta_labels.textfont.size == 9
    assert total_labels.textfont.color is None
    assert delta_labels.textfont.color is None
    assert all(hasattr(value, "tzinfo") for value in candle_timestamps(candles[:2]))
    assert candle_timestamps(candles) == [0, 1, 2, 3, 4]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_report_runtime_core_with_node(tmp_path):
    runtime_path = Path(rendering.__file__).with_name("report_runtime.js")
    harness = tmp_path / "runtime_test.js"
    harness.write_text(
        "const fs = require('fs');\n"
        "global.window = {location: {protocol: 'file:'}, innerWidth: 1200};\n"
        "let lightTheme = false;\n"
        "global.document = {documentElement: {classList: {contains: () => lightTheme}}, getElementById: (id) => id === 'condor-report-spec' ? {textContent: '{\"datasets\":{},\"components\":[]}'} : null};\n"
        f"eval(fs.readFileSync({json.dumps(str(runtime_path))}, 'utf8'));\n"
        "const core = window.CondorReportRuntime.__test;\n"
        "const state = core.initialRangeState(null, 0.8, 52.1, 'number');\n"
        "if (state.min !== 0.8 || state.max !== 52.1 || state.touched !== false) process.exit(1);\n"
        "if (!core.rangeMatches(null, state)) process.exit(2);\n"
        "state.touched = true;\n"
        "if (core.rangeMatches(null, state) || !core.rangeMatches(1, state)) process.exit(3);\n"
        "state.min = null; state.max = null;\n"
        "if (!core.rangeMatches(null, state)) process.exit(4);\n"
        "if (core.advanceUiRevision() !== 1) process.exit(5);\n"
        "const rows = [{id: 'a', row: {name: 'E1', pnl: -10, label: '-$10', sign: 'Loss'}}];\n"
        "const trace = core.chartTraces({chart_type: 'horizontal_bar', x: 'name', y: 'pnl', color: 'sign', encodings: {text: 'label'}, title: 'PnL'}, rows)[0];\n"
        "if (trace.textposition !== 'inside') process.exit(6);\n"
        "const shape = core.referenceLineShape({axis: 'x', value: 80, label: 'Limit'});\n"
        "if (shape.x0 !== 80 || shape.name !== 'Limit' || !shape.showlegend) process.exit(7);\n"
        "const scannerRows = [{id: 'm1', row: {pair: 'BTC', natr: 0.1, volume: 1000000, classification: 'Mature', marker_size: 18}}];\n"
        "const scanner = core.chartTraces({chart_type: 'scatter', x: 'natr', y: 'volume', color: 'classification', color_map: {Mature: '#22c55e'}, encodings: {text: 'pair', text_position: 'top center', size: 'marker_size'}, title: 'Scanner'}, scannerRows)[0];\n"
        "if (scanner.mode !== 'markers+text' || scanner.textposition !== 'top center') process.exit(8);\n"
        "if (scanner.marker.size[0] !== 18 || scanner.marker.color !== '#22c55e') process.exit(9);\n"
        "const aggregateRows = [{id: 'a', row: {group: 'BTC', value: 2, label: 'BTC', size: 10}}, {id: 'b', row: {group: 'BTC', value: 4, label: 'BTC', size: 20}}];\n"
        "const aggregateScatter = core.chartTraces({chart_type: 'scatter', x: 'group', y: 'value', aggregate: 'mean', encodings: {text: 'label', size: 'size'}, title: 'Aggregated'}, aggregateRows)[0];\n"
        "if (aggregateScatter.text[0] !== 'BTC' || aggregateScatter.marker.size[0] !== 15) process.exit(10);\n"
        "const layout = {xaxis: {}, yaxis: {}};\n"
        "core.applyAxisEncodings(layout, {encodings: {x_range: [0, 2], y_scale: 'log'}});\n"
        "if (layout.xaxis.range[1] !== 2 || layout.yaxis.type !== 'log') process.exit(11);\n"
        "const histogramRows = [{id: 'null', row: {value: null}}, {id: 'blank', row: {value: ''}}, {id: 'a', row: {value: 7}}, {id: 'b', row: {value: '7'}}];\n"
        "const histogram = core.histogramTrace({x: 'value', bins: 12, title: 'Constant'}, histogramRows);\n"
        "if (histogram.x.length !== 1 || histogram.x[0] !== 7 || histogram.y[0] !== 2) process.exit(12);\n"
        "if (histogram.customdata[0].join(',') !== 'a,b') process.exit(13);\n"
        "const heatmapRows = [{id: 'btc', row: {symbol: 'BTC', volume: 100, change: 0.25}}, {id: 'btc-2', row: {symbol: 'BTC', volume: 50, change: null}}, {id: 'eth', row: {symbol: 'ETH', volume: 25, change: -0.4}}, {id: 'tiny', row: {symbol: 'TINY', volume: 1, change: 0.1}}, {id: 'bad', row: {symbol: 'BAD', volume: 0, change: 20}}];\n"
        "const heatmap = core.chartTraces({id: 'heatmap', chart_type: 'treemap', x: 'symbol', y: 'volume', color: 'change', encodings: {value_prefix: '$', color_suffix: '%'}, title: 'Trends'}, heatmapRows)[0];\n"
        "const rootId = '__condor_treemap_root__:heatmap';\n"
        "if (heatmap.type !== 'treemap' || heatmap.labels.slice(1).join(',') !== 'BTC,BTC,ETH,TINY') process.exit(14);\n"
        "if (heatmap.ids[0] !== rootId || heatmap.ids.slice(1).join(',') !== 'btc,btc-2,eth,tiny') process.exit(15);\n"
        "if (heatmap.customdata[0] !== null || heatmap.customdata.slice(1).join(',') !== 'btc,btc-2,eth,tiny') process.exit(16);\n"
        "if (heatmap.parents[0] !== '' || heatmap.parents.slice(1).some((parent) => parent !== rootId)) process.exit(17);\n"
        "if (heatmap.values[0] !== 176 || heatmap.branchvalues !== 'total') process.exit(18);\n"
        "if (heatmap.marker.cmin !== -0.4 || heatmap.marker.cmax !== 0.4 || heatmap.marker.cmid !== 0) process.exit(19);\n"
        "if (heatmap.marker.colors[0] !== 'rgba(0,0,0,0)' || heatmap.marker.line.width[0] !== 0) process.exit(20);\n"
        "if (heatmap.marker.line.color[1] !== 'rgba(15, 23, 42, 0.82)') process.exit(21);\n"
        "if (heatmap.text[1] !== '+0.25%' || heatmap.text[2] !== 'N/A') process.exit(22);\n"
        "if (heatmap.textfont.size !== 20 || heatmap.textposition !== 'middle center') process.exit(23);\n"
        "if (!heatmap.texttemplate[1].includes('%{text}') || heatmap.texttemplate[4].includes('%{text}')) process.exit(24);\n"
        "lightTheme = true;\n"
        "const lightHeatmap = core.chartTraces({id: 'heatmap', chart_type: 'treemap', x: 'symbol', y: 'volume', color: 'change', encodings: {}, title: 'Trends'}, heatmapRows)[0];\n"
        "if (lightHeatmap.marker.line.color[1] !== '#d8dce8') process.exit(25);\n"
        "const plainHeatmap = core.chartTraces({chart_type: 'treemap', x: 'symbol', y: 'volume', encodings: {}, title: 'Sizes'}, heatmapRows)[0];\n"
        "if (plainHeatmap.marker.colors.length !== 5 || plainHeatmap.marker.colors[0] !== 'rgba(0,0,0,0)' || plainHeatmap.marker.color !== undefined) process.exit(26);\n"
        "const line = core.chartTraces({chart_type: 'line', x: 'group', y: 'value', encodings: {text: 'label'}, title: 'Line'}, aggregateRows)[0];\n"
        "if (line.mode !== 'lines+text' || line.textposition !== 'top center') process.exit(27);\n"
        "const box = core.chartTraces({chart_type: 'box', x: 'group', y: 'value', y_label: 'Value (USD)', selection_field: 'label', encodings: {}, title: 'Box'}, aggregateRows)[0];\n"
        "if (!box.hovertemplate.includes('Value (USD)') || box.hovertemplate.includes('bps')) process.exit(28);\n"
        "if (box.jitter !== 0 || box.pointpos !== 0) process.exit(29);\n",
        encoding="utf-8",
    )
    subprocess.run(["node", str(harness)], check=True)


# ── PERF-267: reports reference one shared plotly bundle, hydrated on egress ──


def _plotly_report(title: str = "Chart Report"):
    import plotly.graph_objects as go

    builder = reports.ReportBuilder(title).source("routine", "perf267")
    builder.plotly(go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])]), optimize=False)
    return builder


def _saved_document(reports_dir, report_id: str) -> str:
    entry = store.get_report(report_id)
    return (reports_dir / entry["filename"]).read_text(encoding="utf-8")


def test_saved_chart_report_references_the_shared_bundle_instead_of_inlining_it(
    reports_dir,
):
    """A saved chart report carries no bundle and no off-origin host."""
    report_id = asyncio.run(_plotly_report().save())
    document = _saved_document(reports_dir, report_id)

    bundle = rendering.plotly_bundle()
    asset = reports_dir / rendering.ASSETS_DIRNAME / rendering.plotly_asset_name(bundle)
    assert asset.is_file()
    # Byte-for-byte what plotly ships: hydration re-inlines exactly this.
    assert asset.read_text(encoding="utf-8") == bundle

    assert bundle not in document
    assert f'<script src="/api/v1/reports/assets/{asset.name}"></script>' in document
    # Self-containment invariant: every script the document pulls in is served
    # by this app, from its own path. No CDN, no third-party host, no scheme.
    for src in re.findall(r'<script[^>]+src="([^"]*)"', document):
        assert src.startswith("/api/v1/reports/assets/"), src


def test_chartless_report_writes_no_asset_and_references_none(reports_dir):
    """No plotly section, no plotly slot — and no ``_assets`` directory at all."""
    builder = reports.ReportBuilder("Text Only").source("routine", "perf267")
    builder.markdown("Nothing but prose.")
    report_id = asyncio.run(builder.save())
    document = _saved_document(reports_dir, report_id)

    assert rendering.ASSET_URL_PREFIX not in document
    assert "<script src=" not in document
    assert "Charts unavailable" not in document
    assert not (reports_dir / rendering.ASSETS_DIRNAME).exists()


def test_hydrated_document_is_self_contained(reports_dir):
    """The egress form inlines the bundle and references nothing external."""
    report_id = asyncio.run(_plotly_report().save())
    hydrated = rendering.hydrate(_saved_document(reports_dir, report_id))

    assert rendering.plotly_bundle() in hydrated
    # The criterion that matters at ``file://`` and in Telegram: nothing left
    # for the document to fetch.
    assert "<script src=" not in hydrated


def test_hydrate_pins_the_version_the_report_was_saved_against(
    reports_dir, monkeypatch
):
    """A plotly upgrade must not retarget reports already on disk.

    Stored reports are immutable snapshots, so a v3 figure spec has to keep
    getting the v3 runtime after ``_assets`` gains a second, newer bundle.
    """
    report_id = asyncio.run(_plotly_report().save())
    old_bundle = rendering.plotly_bundle()
    old_name = rendering.plotly_asset_name(old_bundle)

    new_bundle = "/**\n* plotly.js v9.9.9\n*/\nwindow.Plotly={upgraded:true};"
    monkeypatch.setattr(rendering, "plotly_bundle", lambda: new_bundle)
    asyncio.run(_plotly_report("Newer Report").save())

    assets = reports_dir / rendering.ASSETS_DIRNAME
    assert (assets / "plotly-9.9.9.js").is_file()
    assert (assets / old_name).read_text(encoding="utf-8") == old_bundle

    document = _saved_document(reports_dir, report_id)
    assert old_name in document
    hydrated = rendering.hydrate(document)
    assert old_bundle in hydrated
    assert new_bundle not in hydrated


def test_hydrate_leaves_a_document_whose_asset_is_missing_alone(reports_dir):
    """A vanished bundle degrades to the in-report banner, not a crash."""
    report_id = asyncio.run(_plotly_report().save())
    document = _saved_document(reports_dir, report_id)
    for asset in (reports_dir / rendering.ASSETS_DIRNAME).iterdir():
        asset.unlink()

    assert rendering.hydrate(document) == document
    # The bootstrap that turns a missing bundle into something visible.
    assert "Charts unavailable" in document


def test_hydrate_is_a_no_op_for_a_pre_existing_inlined_report(reports_dir):
    """The 100 reports already on disk need no migration to keep working."""
    legacy = "<html><head><script>window.Plotly={};</script></head><body></body></html>"
    assert rendering.hydrate(legacy) == legacy
