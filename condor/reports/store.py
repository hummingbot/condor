"""Report attribution and persistent report index storage."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
from contextlib import contextmanager
from pathlib import Path

from condor.fsutil import atomic_write_json, atomic_write_text

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
_report_source: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "report_source", default=None
)
_report_owner: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "report_owner", default=None
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


def current_agent() -> str | None:
    """The assistant reports are currently attributed to, if any.

    Read by the nested ``call_routine`` runner: a nested run executes in its own
    copy of the caller's context, so the caller's ``attribute_to`` is still in
    scope here, and the inner routine can inherit the assistant that asked
    instead of falling back to its own library owner (ARCH-217).
    """
    return _report_agent.get()


@contextmanager
def attribute_owner(user_id: int | None):
    """Attribute reports saved within this block to an authenticated user id.

    Every runner (routine store, code runner, Telegram routine handler, agent
    session report) wraps execution with this so ``ReportBuilder.save`` records
    who the report belongs to — the id the web routes then authorize reads and
    deletes against (SEC-196). A falsy id (0, None) records no owner, which the
    routes treat as admin-only, never world-readable.
    """
    token = _report_owner.set(int(user_id) if user_id else None)
    try:
        yield
    finally:
        _report_owner.reset(token)


@contextmanager
def default_source(source_type: str, source_name: str):
    """Stamp reports saved in this block that never called ``ReportBuilder.source()``.

    Without a source a report is invisible on the Routines page: the per-routine
    list matches on ``source_name`` and ``list_reports_grouped`` skips entries
    without one, so a routine that forgot the call showed "No reports yet" while
    the report existed. The routine runner wraps every run with this so the
    call is a nicety, not a requirement. An explicit ``source()`` always wins.
    """
    value = (source_type, source_name) if source_name else None
    token = _report_source.set(value)
    try:
        yield
    finally:
        _report_source.reset(token)


@contextmanager
def run_scope(
    *,
    owner: int | None = None,
    agent: str | None = None,
    source_type: str = "",
    source_name: str = "",
):
    """The report-attribution scope every runner of user code enters.

    One block, four effects: the last-report id is reset so the run reports only
    what *it* saved, and reports saved inside are stamped with the owner allowed
    to read them (SEC-196), the assistant that asked, and a fallback source that
    keeps them visible on the Routines page. The routine store, the code runner,
    the nested ``call_routine`` runner and both Telegram runners enter it here
    instead of hand-copying the four calls, which had already drifted apart once
    (ARCH-217).

    ``bind_context`` stays at the call site: each runner publishes a different
    context object, and ``condor.reports`` must not import ``condor.primitives``.
    """
    reset_last_report_id()
    with (
        attribute_owner(owner),
        attribute_to(agent),
        default_source(source_type, source_name),
    ):
        yield


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


def resolve_report_asset(filename: str) -> Path | None:
    """Resolve a persisted report asset (the plotly bundle) by name, or None.

    The name is matched against a fixed allowlist pattern before it is joined,
    and the resolved path must still land inside ``reports/_assets``. This is
    deliberately *not* a ``/{name}`` handler that reads a caller-supplied path
    — that shape is what SEC-044 and SEC-112 removed. Assets are vendored
    library bytes, identical for every user and carrying no account data, so
    unlike report bodies they need no authentication.
    """
    from . import rendering

    if not rendering.PLOTLY_ASSET_PATTERN.fullmatch(filename):
        return None
    directory = rendering.assets_dir(_charts_dir()).resolve()
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        return None
    return path


def _read_index() -> list[dict]:
    index_file = _index_file()
    if not index_file.exists():
        return []
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_index(entries: list[dict]) -> None:
    atomic_write_json(_index_file(), entries, indent=2, ensure_ascii=False)


def _write_report_html(path: Path, content: str) -> None:
    """Atomically create or replace a report HTML file."""
    atomic_write_text(path, content)


def list_reports(
    source_type: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    agent: str | None = None,
    subject: str | None = None,
    limit: int = 50,
    offset: int = 0,
    owner_id: int | None = None,
) -> tuple[list[dict], int]:
    """List index entries, newest first.

    ``owner_id`` scopes the listing to one user's reports: entries whose
    ``user_id`` differs — including legacy entries with no owner at all —
    are dropped (fail closed, SEC-196). ``None`` means no owner filter,
    which the web routes reserve for admins; internal callers that match
    on ``source_name`` (routine run lists) also pass ``None``.

    ``subject`` matches what a report is about exactly (FEAT-078): pass a key
    built by :mod:`condor.reports.subjects`. Entries saved without a subject —
    every entry written before the field existed — never match one, and a key
    whose report has since been pruned simply matches nothing.
    """
    entries = _read_index()
    entries.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)

    if owner_id is not None:
        entries = [entry for entry in entries if entry.get("user_id") == owner_id]
    if source_type:
        entries = [
            entry for entry in entries if entry.get("source_type") == source_type
        ]
    if tag:
        entries = [entry for entry in entries if tag in entry.get("tags", [])]
    if agent:
        entries = [entry for entry in entries if entry.get("agent") == agent]
    if subject:
        entries = [entry for entry in entries if entry.get("subject") == subject]
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


def list_reports_grouped(owner_id: int | None = None) -> list[dict]:
    """Return the latest report per source name, with count.

    ``owner_id`` scopes the grouping exactly as in ``list_reports``: ``None``
    (admin) sees every entry, anyone else only entries stamped with their id.
    """
    entries = _read_index()
    entries.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)
    if owner_id is not None:
        entries = [entry for entry in entries if entry.get("user_id") == owner_id]
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
    """Return one report's index entry, or None when no report has that id."""
    for entry in _read_index():
        if entry["id"] == report_id:
            return entry
    return None


async def delete_report(report_id: str) -> bool:
    """Delete a report's HTML and its index entry. True when one was removed."""
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
