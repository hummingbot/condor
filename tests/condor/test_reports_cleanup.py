"""Tests for report index retention policy."""

from pathlib import Path

import condor.reports as reports_mod


def test_cleanup_skipped_when_max_reports_zero(tmp_path: Path):
    charts_dir = tmp_path / "reports"
    charts_dir.mkdir()
    reports_mod.CHARTS_DIR = charts_dir
    reports_mod.INDEX_FILE = charts_dir / "reports_index.json"

    entries = []
    for i in range(5):
        fname = f"r{i}.html"
        (charts_dir / fname).write_text("<html></html>")
        entries.append(
            {
                "id": f"r{i}",
                "filename": fname,
                "created_at": f"2026-01-{i + 1:02d}T00:00:00+00:00",
                "source_type": "routine",
                "source_name": "macd_bb_analysis",
            }
        )
    reports_mod._write_index(entries)
    reports_mod._cleanup_locked(max_reports=0)
    assert len(reports_mod._read_index()) == 5
    assert all((charts_dir / f"r{i}.html").exists() for i in range(5))


def test_cleanup_prunes_oldest_when_over_limit(tmp_path: Path):
    charts_dir = tmp_path / "reports"
    charts_dir.mkdir()
    reports_mod.CHARTS_DIR = charts_dir
    reports_mod.INDEX_FILE = charts_dir / "reports_index.json"

    entries = []
    for i in range(5):
        fname = f"r{i}.html"
        (charts_dir / fname).write_text("<html></html>")
        entries.append(
            {
                "id": f"r{i}",
                "filename": fname,
                "created_at": f"2026-01-{i + 1:02d}T00:00:00+00:00",
                "source_type": "routine",
                "source_name": "macd_bb_analysis",
            }
        )
    reports_mod._write_index(entries)
    reports_mod._cleanup_locked(max_reports=2)
    kept = reports_mod._read_index()
    assert len(kept) == 2
    assert {e["id"] for e in kept} == {"r3", "r4"}
    assert not (charts_dir / "r0.html").exists()
    assert (charts_dir / "r4.html").exists()
