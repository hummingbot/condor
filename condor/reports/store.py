"""Report attribution and persistent report index storage."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

# reports/ is a repository-root output directory, not this source package.
CHARTS_DIR = Path(__file__).resolve().parents[2] / "reports"
INDEX_FILE = CHARTS_DIR / "reports_index.json"
MAX_REPORTS = int(os.environ.get("CONDOR_MAX_REPORTS", "100"))

_index_lock = asyncio.Lock()
_last_report_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "last_report_id", default=None
)
_report_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "report_agent", default=None
)


def _charts_dir() -> Path:
    # Resolve through the public package so existing runtime overrides of
    # condor.reports.CHARTS_DIR keep working after the module-to-package split.
    from . import CHARTS_DIR as configured_dir

    return configured_dir


def _index_file() -> Path:
    from . import INDEX_FILE as configured_file

    return configured_file


def reset_last_report_id() -> None:
    """Clear the last-saved report ID for the current task (call before a run)."""
    _last_report_id.set(None)


def get_last_report_id() -> str | None:
    """Return the ID of the last report saved by the current task, if any."""
    return _last_report_id.get()


@contextmanager
def attribute_to(agent: str | None):
    """Attribute reports saved within this block to an assistant slug."""
    token = _report_agent.set(agent or None)
    try:
        yield
    finally:
        _report_agent.reset(token)


def get_report_raw_html(report_id: str) -> tuple[str, str] | None:
    """Return the report's raw HTML and filename exactly as saved on disk.

    The filename is never taken from the caller — it is read from the index
    entry for ``report_id`` — but the entry is still treated as untrusted:
    the resolved path must stay inside the reports directory and must be an
    ``.html`` file. This is what keeps the authenticated HTML route
    (``GET /api/v1/reports/{id}/html``) from being turned into an arbitrary
    file reader by a poisoned or hand-edited index.
    """
    entry = get_report(report_id)
    if not entry:
        return None
    charts_dir = _charts_dir().resolve()
    path = (charts_dir / entry["filename"]).resolve()
    if not path.is_relative_to(charts_dir):
        return None
    if path.suffix.lower() != ".html" or not path.is_file():
        return None
    return path.read_text(encoding="utf-8"), entry["filename"]


def _read_index() -> list[dict]:
    index_file = _index_file()
    if not index_file.exists():
        return []
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_index(entries: list[dict]) -> None:
    charts_dir = _charts_dir()
    charts_dir.mkdir(exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(charts_dir), suffix=".tmp", delete=False
    )
    try:
        json.dump(entries, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, str(_index_file()))
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _write_report_html(path: Path, content: str) -> None:
    """Atomically create or replace a report HTML file."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(content)
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def list_reports(
    source_type: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    agent: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    entries = _read_index()
    entries.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)

    if source_type:
        entries = [
            entry for entry in entries if entry.get("source_type") == source_type
        ]
    if tag:
        entries = [entry for entry in entries if tag in entry.get("tags", [])]
    if agent:
        entries = [entry for entry in entries if entry.get("agent") == agent]
    if search:
        query = search.lower()
        entries = [
            entry
            for entry in entries
            if query in entry.get("title", "").lower()
            or query in entry.get("source_name", "").lower()
            or any(query in item.lower() for item in entry.get("tags", []))
        ]

    total = len(entries)
    return entries[offset : offset + limit], total


def list_reports_grouped() -> list[dict]:
    """Return the latest report per source name, with count."""
    entries = _read_index()
    entries.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)
    groups: dict[str, dict] = {}
    for entry in entries:
        source_name = entry.get("source_name", "")
        if not source_name:
            continue
        if source_name not in groups:
            groups[source_name] = {
                "source_name": source_name,
                "source_type": entry.get("source_type", ""),
                "latest_report": entry,
                "total_count": 1,
                "all_tags": set(entry.get("tags", [])),
            }
        else:
            groups[source_name]["total_count"] += 1
            groups[source_name]["all_tags"].update(entry.get("tags", []))
    for group in groups.values():
        group["all_tags"] = sorted(group["all_tags"])
    return sorted(
        groups.values(),
        key=lambda group: group["latest_report"]["created_at"],
        reverse=True,
    )


def get_report(report_id: str) -> dict | None:
    for entry in _read_index():
        if entry["id"] == report_id:
            return entry
    return None


async def delete_report(report_id: str) -> bool:
    async with _index_lock:
        entries = _read_index()
        new_entries = []
        deleted = False
        for entry in entries:
            if entry["id"] == report_id:
                path = _charts_dir() / entry["filename"]
                if path.exists():
                    path.unlink()
                deleted = True
            else:
                new_entries.append(entry)
        if deleted:
            _write_index(new_entries)
    return deleted


def _cleanup_locked(max_reports: int = MAX_REPORTS) -> None:
    """Run cleanup while the caller already holds the index lock."""
    entries = _read_index()
    if len(entries) <= max_reports:
        return
    entries.sort(
        key=lambda entry: entry.get("updated_at") or entry.get("created_at", "")
    )
    to_remove = entries[: len(entries) - max_reports]
    keep_ids = {entry["id"] for entry in entries[len(entries) - max_reports :]}
    for entry in to_remove:
        path = _charts_dir() / entry["filename"]
        if path.exists():
            path.unlink()
    _write_index([entry for entry in entries if entry["id"] in keep_ids])
