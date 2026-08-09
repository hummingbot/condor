from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.lp_agent_lite.routines import _reporting


@pytest.fixture(autouse=True)
def stub_diagnostic_report_save(monkeypatch):
    """Keep routine tests deterministic and avoid persistent test reports."""

    async def save(_builder, report_id=None):
        assert report_id is None
        return "rpt001"

    monkeypatch.setattr(_reporting.ReportBuilder, "save", save)
