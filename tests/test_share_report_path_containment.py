"""SEC-268: Telegram's share button reads reports through the guarded helper.

``_share_report`` used to join ``CHARTS_DIR / entry["filename"]`` by hand and
``open()`` the result, checking only ``.exists()``. A poisoned or hand-edited
``reports_index.json`` could therefore make the share button mail an arbitrary
file to a Telegram chat. It now reads through
``condor.reports.get_report_raw_html``, which keeps the resolved path inside
the reports directory and requires a real ``.html`` file — the same invariant
the authenticated web route relies on (SEC-112).
"""

import json
from types import SimpleNamespace

import pytest

import condor.reports as rep
import handlers.routines as hr

CHAT_ID = 4242
BODY = "<h1>Report</h1>"
OUTSIDE_BODY = "SECRET-FILE-BEYOND-REPORTS-DIR"
NON_REPORT_BODY = "SECRET-NON-REPORT-FILE"


class FakeBot:
    def __init__(self):
        self.documents = []

    async def send_document(self, chat_id, document, filename=None, caption=None):
        self.documents.append(
            {
                "chat_id": chat_id,
                "bytes": document.read(),
                "filename": filename,
                "caption": caption,
            }
        )


class FakeQuery:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


def _entry(report_id, title, filename):
    return {
        "id": report_id,
        "title": title,
        "filename": filename,
        "created_at": "2026-08-28T00:00:00+00:00",
        "source_type": "routine",
        "source_name": report_id,
        "tags": [],
        "user_id": 111,
    }


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    """A reports directory with one real report plus poisoned index entries."""
    directory = tmp_path / "reports"
    directory.mkdir()
    (directory / "sample.html").write_text(BODY, encoding="utf-8")
    (directory / "private.txt").write_text(NON_REPORT_BODY, encoding="utf-8")
    (directory / "adir.html").mkdir()
    (tmp_path / "outside.html").write_text(OUTSIDE_BODY, encoding="utf-8")

    index = directory / "reports_index.json"
    index.write_text(
        json.dumps(
            [
                _entry("ok", "Sample", "sample.html"),
                # The index lies: outside the reports directory, not .html,
                # and a directory rather than a file.
                _entry("escape", "Escape", "../outside.html"),
                _entry("nothtml", "Not HTML", "private.txt"),
                _entry("adir", "A Directory", "adir.html"),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rep, "CHARTS_DIR", directory)
    monkeypatch.setattr(rep, "INDEX_FILE", index)
    return directory


def _update_and_context():
    query = FakeQuery()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=CHAT_ID), callback_query=query
    )
    context = SimpleNamespace(bot=bot)
    return update, context, query, bot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "report_id",
    [
        "escape",  # index entry pointing outside the reports directory
        "nothtml",  # index entry pointing at a non-.html file
        "adir",  # index entry naming a directory
    ],
)
async def test_share_report_refuses_a_poisoned_index_entry(reports_dir, report_id):
    update, context, query, bot = _update_and_context()

    await hr._share_report(update, context, report_id)

    assert bot.documents == []
    assert query.answers == ["Report file missing"]


@pytest.mark.asyncio
async def test_share_report_answers_when_the_report_is_unknown(reports_dir):
    update, context, query, bot = _update_and_context()

    await hr._share_report(update, context, "nope")

    assert bot.documents == []
    assert query.answers == ["Report not found"]


@pytest.mark.asyncio
async def test_share_report_still_sends_a_real_report(reports_dir):
    update, context, query, bot = _update_and_context()

    await hr._share_report(update, context, "ok")

    assert query.answers == []
    assert len(bot.documents) == 1
    sent = bot.documents[0]
    assert sent["chat_id"] == CHAT_ID
    assert sent["bytes"] == BODY.encode("utf-8")
    assert sent["filename"] == "Sample.html"
    assert sent["caption"] == "📊 Sample"
