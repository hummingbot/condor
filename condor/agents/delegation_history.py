"""Delegations that outlived the process that ran them (FEAT-035).

The live registry in :mod:`condor.agents.delegate` is in-memory and per-process
by design: membership there means "this can still be running". A restart
therefore used to erase every trace of delegated work from the dashboard, even
though the work itself was on disk the whole time.

This module is the read side of those files, and nothing else: no state, no
writes, no knowledge of the web layer. It rebuilds a delegation record from what
was persisted, so the same routes and the same two React components that serve a
live task can serve one from two weeks ago.

**The owner is the first path segment** (FEAT-051). A record lives at
``.condor/users/{user_id}/delegations/{task_id}/``, beside that person's
conversations, which is why every reader here takes a ``user_id`` first:
answering "what did this user delegate" is opening one directory, and reading
someone else's is not a check a caller could forget to make -- it is a path they
cannot name. ``user_id=None`` means "every user" and is reachable only from an
admin path or the boot reconciler.

Four shapes of record exist on disk, and all four are readable here:

* **current** -- a ``{task_id}/`` directory under its owner, holding
  ``status.json`` (the whole record), ``events.json`` (the transcript, in the
  exact projection the wire uses) and ``transcript.md``.
* **pre-FEAT-051** -- the same three files, flat and keyed by agent, at
  ``agents/{slug}/delegations/{task_id}.{status.json,events.json,md}``. The boot
  migration moves every one of these that names a ``user_id``.
* **pre-FEAT-035** -- a ``.status.json`` that carries only state and provenance;
  the task text and result come from parsing the markdown header.
* **legacy** -- a lone ``.md``, from before status files existed at all.

The last two belong to nobody -- no ``user_id`` was ever written -- so there is
no user directory to file them under and the migration leaves them where they
are. They stay readable through the unscoped path, which is admin-only.

Only those last two ever cost a transcript read (PERF-204). A current
``status.json`` *is* the whole record, and ``transcript.md`` beside it embeds
every tool call's input and output -- so the listing route, which the dashboard
re-polls every five seconds and which drops the bodies anyway, opens nothing but
small JSON. What the older shapes still need is parsed at most once per version
of the file, and a listing only ever builds the page it returns.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from condor import paths

log = logging.getLogger(__name__)

# The current shape: one directory per delegation, the same idiom conversations
# already use. ``status.json`` is ``registry_file``'s own default name.
DELEGATION_STATUS_FILENAME = "status.json"
DELEGATION_TRANSCRIPT_FILENAME = "transcript.md"
DELEGATION_EVENTS_FILENAME = "events.json"

# The flat, agent-keyed shape the migration reads and never writes.
STATUS_SUFFIX = ".status.json"
EVENTS_SUFFIX = ".events.json"

# A record whose state was never recorded. Honest: the file exists, so the task
# does; what it ended as we do not know.
UNKNOWN = "unknown"


def _safe(value: int | str) -> str | None:
    """One path segment, or None. Task ids reach this module from a URL path."""
    try:
        return paths.safe_id(value)
    except paths.UnsafeIdError:
        return None


def _mtime(path: Path) -> float:
    """A file's mtime, or 0.0 when it cannot be stat'd. Never raises."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _owners(user_id: int | str | None) -> list[str]:
    """Whose delegations to look in. ``None`` is the cross-user seam."""
    if user_id is not None:
        safe = _safe(user_id)
        return [safe] if safe else []
    return list(paths.iter_user_ids())


def _legacy_dirs(agent_slug: str | None = None):
    """Every pre-FEAT-051 ``delegations/`` directory, newest agents last."""
    from condor.agents.agent import AgentStore

    store = AgentStore()
    agents = []
    if agent_slug:
        agent = store.get(agent_slug)
        if agent is not None:
            agents = [agent]
    else:
        agents = store.list_all()

    for agent in agents:
        d = agent.agent_dir / "delegations"
        if d.is_dir():
            yield agent.slug, d


# ── markdown fallback ──────────────────────────────────────────────────────
# The header emitted by ``delegate._persist_transcript`` is fixed-format, so the
# three lines below are the only prose ever parsed. A miss degrades the field to
# empty (or the status to "unknown"); it never raises.

_HEADER_FIELD = "- **{label}:** "


def _header_value(text: str, label: str) -> str:
    prefix = _HEADER_FIELD.format(label=label)
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
        if line.startswith("## "):  # past the header block
            break
    return ""


def _section(text: str, heading: str) -> str:
    """The body of a ``## heading`` section, up to the next ``## ``."""
    marker = f"\n## {heading}\n"
    start = text.find(marker)
    if start < 0:
        return ""
    body = text[start + len(marker) :]
    nxt = body.find("\n## ")
    return (body if nxt < 0 else body[:nxt]).strip()


def _read_markdown(md_path: Path) -> dict[str, Any]:
    """The fields one transcript carries, independent of who is asking.

    Everything here comes from the file itself, so two callers asking about the
    same path get the same answer -- which is what makes the parse cacheable.
    """
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        log.debug("Unreadable delegation transcript at %s", md_path, exc_info=True)
        text = ""

    status = (_header_value(text, "Status") or UNKNOWN).lower()
    is_error = status == "error"
    try:
        tool_count = int(_header_value(text, "Tool calls") or 0)
    except ValueError:
        tool_count = 0

    # The transcript is written when the task ends, so its mtime is the only
    # timestamp these records have. Reported as the start, since that is what
    # orders the list -- and it is closer to the truth than zero.
    mtime = _mtime(md_path)

    body = _section(text, "Error" if is_error else "Result")
    if body == "(none)":
        body = ""

    # "-" is the placeholder ``_persist_transcript`` renders for "no server",
    # not a server name: reading it back as one would put a dash on the wire.
    server = _header_value(text, "Server")

    return {
        "agent": _header_value(text, "Agent"),
        "user_id": 0,
        "chat_id": 0,
        "server_name": None if server in ("", "-") else server,
        "task": _section(text, "Task"),
        "status": status,
        "result": "" if is_error else body,
        "error": body if is_error else "",
        "conversation_id": "",
        "on_complete": "",
        "started_at": mtime,
        "ended_at": mtime,
        "tool_count": tool_count,
    }


# Memoized on ``(mtime, size)``, the idiom ``sessions_index`` already uses for
# snapshots. A transcript embeds every tool call's input and output, so one file
# runs to hundreds of KB, and the dashboard re-lists while a task is running --
# parsing an unchanged file once per process is the whole point. A file that is
# rewritten gets a new mtime or size and re-parses, so a delegation that changes
# is never served from a stale parse.
_MD_CACHE: dict[Path, tuple[float, int, dict[str, Any]]] = {}

# Only the two unowned legacy shapes still reach the parser, and nothing writes
# those any more, so this cannot grow with usage. The cap is for the
# pathological directory, not for a hot path; dropping it whole is fine because
# every entry is rebuildable from the file it names.
_MD_CACHE_MAX = 512


def _parse_markdown(md_path: Path) -> dict[str, Any]:
    """:func:`_read_markdown`, parsed at most once per version of the file."""
    try:
        stat = md_path.stat()
        key: tuple[float, int] | None = (stat.st_mtime, stat.st_size)
    except OSError:
        key = None

    cached = _MD_CACHE.get(md_path)
    if key is not None and cached is not None and cached[:2] == key:
        return cached[2]

    parsed = _read_markdown(md_path)
    if key is not None:
        if len(_MD_CACHE) >= _MD_CACHE_MAX:
            _MD_CACHE.clear()
        _MD_CACHE[md_path] = (key[0], key[1], parsed)
    return parsed


def _from_markdown(md_path: Path, agent_slug: str, task_id: str) -> dict[str, Any]:
    """A record rebuilt from the transcript alone."""
    parsed = _parse_markdown(md_path)
    return {
        "task_id": task_id,
        "agent": parsed["agent"] or agent_slug,
        **{k: v for k, v in parsed.items() if k != "agent"},
    }


# ── status.json ────────────────────────────────────────────────────────────


# What ``delegate._record_delegation_status`` writes. Their *presence* is what
# says "this status file is the whole record" and no backfill is owed. Presence,
# not truthiness: a running record's ``result`` is legitimately "" and an errored
# one's is too, so an emptiness test would re-read the transcript on exactly the
# rows the dashboard polls every five seconds.
_CONTENT_KEYS = frozenset({"state", "task", "result", "error", "tool_count"})


def _needs_markdown(data: dict[str, Any]) -> bool:
    """Whether this status file is missing content only the transcript has."""
    return not _CONTENT_KEYS <= data.keys()


def _status_sort_key(data: dict[str, Any], md_path: Path) -> float:
    """The ``started_at`` :func:`_from_status` will report, without parsing.

    Mirrors that function's fallback chain exactly -- transcript mtime included,
    since that is the only timestamp a status file missing ``started_at`` has. A
    ``stat()`` answers it; the file is not opened.
    """
    return data.get("started_at") or _mtime(md_path) or data.get("updated_at") or 0.0


def _from_status(
    status_path: Path,
    agent_slug: str,
    task_id: str,
    md_path: Path,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A record rebuilt from its status file, backfilled from the markdown.

    Backfill is what makes a *pre*-FEAT-035 status file useful: it recorded the
    state and the provenance but not the task or the result, and the markdown
    beside it has both. A current status file carries all of it, so the
    transcript -- the big file -- is not opened at all; see :data:`_CONTENT_KEYS`.

    ``data`` lets a caller that has already read the status file hand it over
    rather than paying for a second read.
    """
    from condor.runtime.registry_file import is_stale, read_status

    if data is None:
        data = read_status(status_path.parent, status_path.name) or {}
    md = (
        _from_markdown(md_path, agent_slug, task_id)
        if _needs_markdown(data) and md_path.is_file()
        else {}
    )

    # A `running` record stamped by a boot that is not ours belongs to a process
    # that died without recording an end -- FEAT-012's distinction, and the only
    # honest label for it.
    status = data.get("state") or md.get("status") or UNKNOWN
    if is_stale(data):
        status = "interrupted"

    def pick(key: str, fallback: Any = "") -> Any:
        value = data.get(key)
        if value in (None, ""):
            value = md.get(key, fallback)
        return fallback if value in (None, "") else value

    stamp: float | None = None

    def transcript_mtime() -> float:
        """The transcript's mtime, stat'd at most once and only if needed."""
        nonlocal stamp
        if stamp is None:
            stamp = _mtime(md_path)
        return stamp

    return {
        "task_id": task_id,
        "agent": data.get("agent_slug") or md.get("agent") or agent_slug,
        "user_id": data.get("user_id") or md.get("user_id") or 0,
        "chat_id": data.get("chat_id") or md.get("chat_id") or 0,
        "server_name": data.get("server_name") or md.get("server_name") or None,
        "task": pick("task"),
        "status": status,
        "result": pick("result"),
        "error": pick("error"),
        "conversation_id": data.get("conversation_id") or "",
        "on_complete": data.get("on_complete") or "",
        # The transcript's mtime stands in for a timestamp the status file does
        # not carry -- a status write that never landed, on either end. It is a
        # ``stat()``, not a parse, so it costs the same whether the transcript
        # was read or skipped, and the answer is the one the old backfill gave.
        "started_at": data.get("started_at")
        or transcript_mtime()
        or data.get("updated_at")
        or 0.0,
        "ended_at": data.get("ended_at") or transcript_mtime() or 0.0,
        "tool_count": data.get("tool_count") or md.get("tool_count") or 0,
    }


# ── the current shape: one directory per delegation ─────────────────────────


def _from_dir(record_dir: Path) -> dict[str, Any] | None:
    """The record in one ``{task_id}/`` directory, or None if there is none."""
    task_id = record_dir.name
    status_path = record_dir / DELEGATION_STATUS_FILENAME
    md_path = record_dir / DELEGATION_TRANSCRIPT_FILENAME
    if status_path.is_file():
        return _from_status(status_path, "", task_id, md_path)
    if md_path.is_file():
        return _from_markdown(md_path, "", task_id)
    return None


# ── listing: sort first, hydrate the survivors ──────────────────────────────
# A listing walks every delegation an owner ever ran -- nothing prunes those
# directories -- but returns at most ``limit`` of them. So the walk yields
# *handles*: the sort key, the id (for de-duplication), and a thunk that builds
# the record. Only the rows that survive the sort are ever built.

Entry = tuple[float, str, Callable[[], dict[str, Any] | None]]


def _entry_from_dir(record_dir: Path) -> Entry | None:
    """A sortable handle on one ``{task_id}/`` directory, hydrated on demand.

    The status file is read here rather than in the thunk: it is small, and the
    sort key is in it. It is handed to the thunk so the row that does get built
    does not read it twice.
    """
    from condor.runtime.registry_file import read_status

    task_id = record_dir.name
    status_path = record_dir / DELEGATION_STATUS_FILENAME
    md_path = record_dir / DELEGATION_TRANSCRIPT_FILENAME
    if status_path.is_file():
        data = read_status(record_dir, DELEGATION_STATUS_FILENAME) or {}
        return (
            _status_sort_key(data, md_path),
            task_id,
            lambda: _from_status(status_path, "", task_id, md_path, data),
        )
    if md_path.is_file():
        return _mtime(md_path), task_id, lambda: _from_markdown(md_path, "", task_id)
    return None


def _entries_of(user_id: int | str) -> Iterator[Entry]:
    """Every delegation this user has on disk, unhydrated."""
    try:
        children = sorted(paths.delegations_dir(user_id).iterdir())
    except (OSError, paths.UnsafeIdError):
        return
    for child in children:
        if not child.is_dir():
            continue
        entry = _entry_from_dir(child)
        if entry is not None:
            yield entry


def _legacy_entries_in(agent_slug: str, directory: Path) -> Iterator[Entry]:
    """Every delegation in one pre-FEAT-051 directory, status files first.

    The lone-``.md`` records are the ones this buys the most: their sort key is
    the file's mtime, so ordering them costs a ``stat()`` and the transcript is
    opened only for the handful that make the page.
    """
    from condor.runtime.registry_file import read_status

    seen: set[str] = set()
    for status_path in sorted(directory.glob(f"*{STATUS_SUFFIX}")):
        task_id = status_path.name[: -len(STATUS_SUFFIX)]
        seen.add(task_id)
        md_path = directory / f"{task_id}.md"
        data = read_status(directory, status_path.name) or {}
        yield (
            _status_sort_key(data, md_path),
            task_id,
            lambda sp=status_path, tid=task_id, mp=md_path, d=data: _from_status(
                sp, agent_slug, tid, mp, d
            ),
        )
    for md_path in sorted(directory.glob("*.md")):
        task_id = md_path.name[: -len(".md")]
        if task_id not in seen:
            yield (
                _mtime(md_path),
                task_id,
                lambda mp=md_path, tid=task_id: _from_markdown(mp, agent_slug, tid),
            )


# ── the read API ───────────────────────────────────────────────────────────


def list_history(
    *,
    user_id: int | str | None = None,
    agent_slug: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Recorded delegations, newest first.

    Scoped to one person unless ``user_id`` is None, which is admin-only and
    also picks up the unowned legacy records. Filtering by agent is a filter,
    not a lookup: the store is keyed by user now, and the agent-first view is
    the rarer one (it reads the same status files either way).

    Reads only what is on disk -- the caller merges in the live registry, which
    is the authority for anything still running in this process.

    Ordering happens before hydration: every delegation on disk is *ordered*,
    only ``limit`` of them are *built*. Nothing prunes these directories, so the
    walk grows for the life of the install while the page stays the same size.
    """
    entries: list[Entry] = []
    for owner in _owners(user_id):
        try:
            entries.extend(_entries_of(owner))
        except OSError:
            log.debug("Could not list delegations for user %s", owner, exc_info=True)

    if user_id is None:
        seen = {task_id for _key, task_id, _load in entries}
        for slug, directory in _legacy_dirs(agent_slug):
            try:
                entries.extend(
                    e for e in _legacy_entries_in(slug, directory) if e[1] not in seen
                )
            except OSError:
                log.debug("Could not list delegations in %s", directory, exc_info=True)

    entries.sort(key=lambda e: e[0], reverse=True)

    records: list[dict[str, Any]] = []
    cap = max(0, limit)
    for _key, _task_id, load in entries:
        if len(records) >= cap:
            break
        record = load()
        if record is None:
            continue
        if agent_slug and record.get("agent") != agent_slug:
            continue
        records.append(record)
    return records


def read_history(user_id: int | str | None, task_id: str) -> dict[str, Any] | None:
    """One recorded delegation, or None if nothing on disk describes it.

    With an owner this is a single ``is_dir()``: the caller's id is a path
    segment, so a stranger's task is not merely refused, it is unnameable.
    """
    safe = _safe(task_id)
    if safe is None:
        return None

    for owner in _owners(user_id):
        record_dir = paths.delegation_dir(owner, safe)
        if record_dir.is_dir():
            record = _from_dir(record_dir)
            if record is not None:
                return record

    if user_id is not None:
        return None

    for slug, directory in _legacy_dirs():
        status_path = directory / f"{safe}{STATUS_SUFFIX}"
        md_path = directory / f"{safe}.md"
        if status_path.is_file():
            return _from_status(status_path, slug, safe, md_path)
        if md_path.is_file():
            return _from_markdown(md_path, slug, safe)
    return None


def read_history_events(
    user_id: int | str | None, task_id: str
) -> tuple[list[dict[str, Any]], str]:
    """``(events, markdown)`` for a recorded delegation.

    The sidecar is preferred: it is the same projection
    :func:`condor.agents.delegate.events_for_wire` serves for a live task, so a
    finished delegation renders identically to a running one. Records written
    before the sidecar existed return no events and their transcript instead --
    the client renders that markdown rather than showing an empty transcript.
    """
    safe = _safe(task_id)
    if safe is None:
        return [], ""

    for owner in _owners(user_id):
        record_dir = paths.delegation_dir(owner, safe)
        if not record_dir.is_dir():
            continue
        found = _events_or_markdown(
            record_dir / DELEGATION_EVENTS_FILENAME,
            record_dir / DELEGATION_TRANSCRIPT_FILENAME,
        )
        if found is not None:
            return found

    if user_id is not None:
        return [], ""

    for _slug, directory in _legacy_dirs():
        found = _events_or_markdown(
            directory / f"{safe}{EVENTS_SUFFIX}", directory / f"{safe}.md"
        )
        if found is not None:
            return found
    return [], ""


def _events_or_markdown(
    sidecar: Path, md_path: Path
) -> tuple[list[dict[str, Any]], str] | None:
    """The transcript from one record, or None when neither file is there."""
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            events = data.get("events") if isinstance(data, dict) else None
            if isinstance(events, list):
                return events, ""
        except (OSError, json.JSONDecodeError):
            log.debug("Unreadable events sidecar at %s", sidecar, exc_info=True)

    if md_path.is_file():
        try:
            return [], md_path.read_text(encoding="utf-8")
        except OSError:
            log.debug("Unreadable transcript at %s", md_path, exc_info=True)
            return [], ""
    return None
