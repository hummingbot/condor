"""PERF-267: every path off the origin ships a self-contained report.

A stored chart report references the shared plotly bundle at
``/api/v1/reports/assets/`` instead of inlining 4.85 MB of it. Nothing resolves
that URL once the document leaves the app — a Telegram document is opened by
Telegram's viewer, a downloaded file at ``file://`` — so both Telegram sinks
inline the bundle back in on the way out. "It renders in the dashboard" proves
nothing here: the sandboxed iframe is the one path that tolerates an external
reference, precisely because its base URL is the parent's.
"""

import json
from types import SimpleNamespace

import pytest

import condor.reports as rep
import condor.routine_hooks as routine_hooks
import handlers.routines as hr
from condor.reports import rendering

BUNDLE = "/**\n* plotly.js v0.0.0\n*/\nwindow.Plotly={};"
ASSET_NAME = "plotly-0.0.0.js"
REFERENCE = f'<script src="{rendering.ASSET_URL_PREFIX}{ASSET_NAME}"></script>'
BODY = f"<html><head>{REFERENCE}</head><body>chart</body></html>"


class FakeBot:
    def __init__(self):
        self.documents = []

    async def send_document(self, chat_id, document, filename=None, caption=None):
        self.documents.append(document.read())


class FakeQuery:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    """A reports directory holding one lean chart report and its bundle."""
    directory = tmp_path / "reports"
    assets = directory / rendering.ASSETS_DIRNAME
    assets.mkdir(parents=True)
    (assets / ASSET_NAME).write_text(BUNDLE, encoding="utf-8")
    (directory / "sample.html").write_text(BODY, encoding="utf-8")

    index = directory / "reports_index.json"
    index.write_text(
        json.dumps(
            [
                {
                    "id": "ok",
                    "title": "Sample",
                    "filename": "sample.html",
                    "created_at": "2026-08-28T00:00:00+00:00",
                    "source_type": "routine",
                    "source_name": "sample",
                    "tags": [],
                    "user_id": 111,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rep, "CHARTS_DIR", directory)
    monkeypatch.setattr(rep, "INDEX_FILE", index)
    return directory


@pytest.mark.asyncio
async def test_share_button_sends_a_hydrated_document(reports_dir):
    """The 📤 Share Report button mails a file that stands on its own."""
    bot = FakeBot()
    query = FakeQuery()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242), callback_query=query
    )

    await hr._share_report(update, SimpleNamespace(bot=bot), "ok")

    assert query.answers == []
    sent = bot.documents[0].decode("utf-8")
    assert BUNDLE in sent
    assert "<script src=" not in sent


def test_routine_hook_document_is_hydrated(reports_dir):
    """The report a routine run delivers to Telegram carries its own charts."""
    html, filename = routine_hooks._resolve_report_html("ok", None)

    assert filename == "sample.html"
    assert BUNDLE in html
    assert "<script src=" not in html


def test_routine_hook_fallback_is_untouched(reports_dir):
    """No report, no hydration — the minimal fallback document still works."""
    html, filename = routine_hooks._resolve_report_html(
        None, SimpleNamespace(text="done")
    )

    assert filename == "report.html"
    assert "done" in html
