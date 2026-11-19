"""Report attribution and persistent report index storage."""

from __future__ import annotations

import asyncio
import contextvars
import html
import logging
import os
import re
import shutil
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from condor import paths
from condor.fsutil import atomic_write_json, atomic_write_text, read_json

logger = logging.getLogger(__name__)

# Retention (ST-07). One global FIFO of 100 let a single routine on a short
# schedule flush everyone's archive — every 30 s is 100 reports in 50 minutes,
# other users' included. So the first cut is per (owner, source): each keeps its
# newest MAX_REPORTS_PER_SOURCE. The global cap is the backstop over all of
# them, and correspondingly larger. Both are env-tunable; a per-source cap of 0
# disables that cut. A routine that only wants "the latest" should update one
# report in place with LiveReport instead of leaning on either.
MAX_REPORTS = int(os.environ.get("CONDOR_MAX_REPORTS", "500"))
MAX_REPORTS_PER_SOURCE = int(os.environ.get("CONDOR_MAX_REPORTS_PER_SOURCE", "50"))

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
    # reports/ is a repository-root output directory, not this source package;
    # condor.paths owns it, so $CONDOR_REPORTS_DIR moves it after import.
    return paths.reports_dir()


def _index_file() -> Path:
    return paths.reports_index_path()


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
    """The index for a read-only caller: an unreadable one lists as empty.

    Never feed this into a write — see :func:`_read_index_for_write`.
    """
    try:
        entries = read_json(_index_file(), [])
    except Exception:
        logger.warning("Failed to read %s; listing no reports", _index_file())
        return []
    return entries if isinstance(entries, list) else []


def _read_index_for_write() -> list[dict]:
    """The index for a read-modify-write. Never mistakes "unreadable" for "empty".

    Writing back what :func:`_read_index` degrades to made one failed read
    permanent: the next save persisted its own entry alone and every other
    report became an orphan file nothing lists or cleans (ST-08).

    A read that did not happen (``OSError``) raises: nothing is known about
    what is on disk, so nothing may be written over it. Content that is not a
    JSON list will not get better on a retry, so it is moved aside as
    ``reports_index.json.corrupt-<timestamp>`` and the index is rebuilt from
    the report files themselves.

    The recovery is durable before it is returned: the corrupt file is
    *copied* aside and the rebuilt index is then written over it atomically.
    Moving it aside first left a window where the index was simply missing —
    if the caller's own write then failed (disk full), the next save read
    "missing" as ``[]`` and persisted its new report alone, orphaning every
    recovered one again. Now a failure at any step leaves the corrupt index in
    place, so the next write retries the whole recovery.
    """
    index_file = _index_file()
    try:
        entries = read_json(index_file, [])
        if not isinstance(entries, list):
            raise ValueError(f"expected a list, found {type(entries).__name__}")
        return entries
    except ValueError as e:
        aside = index_file.with_name(
            f"{index_file.name}.corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
        )
        shutil.copy2(index_file, aside)
        entries = _rebuild_index_from_disk()
        _write_index(entries)
        logger.error(
            "Report index %s was unreadable (%s); kept as %s and rebuilt %d "
            "entries from the report files",
            index_file,
            e,
            aside.name,
            len(entries),
        )
        return entries


# ``<YYYYmmdd_HHMMSS>_<slug>_<id>.html`` — the name ``ReportBuilder.save`` mints.
_REPORT_FILENAME = re.compile(r"(\d{8}_\d{6})_(.*)_([0-9a-f]{6})\.html")
_TITLE_TAG = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _rebuild_index_from_disk() -> list[dict]:
    """Index entries recovered from the report files, oldest first.

    Only what a file can prove about itself: its id and creation time (from its
    name) and its title (from its head). Source, tags and subject lived in the
    lost index alone. So did the owner — a recovered report has none, which the
    web routes treat as admin-only, never as world-readable (SEC-196).
    """
    entries = []
    for path in sorted(_charts_dir().glob("*.html")):
        match = _REPORT_FILENAME.fullmatch(path.name)
        if not match:
            continue
        stamp, slug, report_id = match.groups()
        try:
            created = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(
                tzinfo=timezone.utc
            )
            with path.open(encoding="utf-8", errors="replace") as handle:
                title = _TITLE_TAG.search(handle.read(4096))
        except (OSError, ValueError):
            continue
        entries.append(
            {
                "id": report_id,
                "title": html.unescape(title.group(1)).strip() if title else slug,
                "filename": path.name,
                "created_at": created.isoformat(),
                "source_type": "",
                "source_name": "",
                "tags": [],
                "subject": None,
                "agent": "condor",
                "user_id": None,
                "recovered": True,
            }
        )
    return entries


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
    source_name: str | Iterable[str] | None = None,
    source_prefix: str | None = None,
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

    ``source_name`` matches the producer's name exactly — one name, or any of
    an iterable of accepted names — and ``source_prefix`` matches names that
    start with it (ARCH-692). Both apply before ``total`` and the page slice,
    so a match is never lost past ``limit`` the way post-filtering a ``search``
    page loses it: ``search`` is a substring over title, source and tags, and
    ranks ``pmm_king`` hits alongside ``pmm`` ones.
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
    if source_name is not None:
        names = {source_name} if isinstance(source_name, str) else set(source_name)
        entries = [entry for entry in entries if entry.get("source_name") in names]
    if source_prefix:
        entries = [
            entry
            for entry in entries
            if entry.get("source_name", "").startswith(source_prefix)
        ]
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
        entries = _read_index_for_write()
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


def _cleanup_locked(
    max_reports: int | None = None, max_per_source: int | None = None
) -> None:
    """Run cleanup while the caller already holds the index lock.

    Oldest-first by last update, in two passes: each (owner, source) down to
    ``max_per_source``, then whatever is left down to ``max_reports``. A report
    saved with no source belongs to no group and only meets the global cap.
    The caps default to the module's, read at call time.
    """
    if max_reports is None:
        max_reports = MAX_REPORTS
    if max_per_source is None:
        max_per_source = MAX_REPORTS_PER_SOURCE
    entries = _read_index_for_write()
    entries.sort(
        key=lambda entry: entry.get("updated_at") or entry.get("created_at", "")
    )

    doomed: set[str] = set()
    if max_per_source > 0:
        by_source: dict[tuple, list[dict]] = {}
        for entry in entries:
            if entry.get("source_name"):
                key = (entry.get("user_id"), entry["source_name"])
                by_source.setdefault(key, []).append(entry)
        for group in by_source.values():
            doomed.update(entry["id"] for entry in group[:-max_per_source])

    survivors = [entry for entry in entries if entry["id"] not in doomed]
    excess = len(survivors) - max_reports
    if excess > 0:
        doomed.update(entry["id"] for entry in survivors[:excess])
    if not doomed:
        return

    for entry in entries:
        if entry["id"] in doomed:
            path = _charts_dir() / entry["filename"]
            if path.exists():
                path.unlink()
    _write_index([entry for entry in entries if entry["id"] not in doomed])
