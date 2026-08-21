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
"""

from __future__ import annotations

import json
import logging
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


def _from_markdown(md_path: Path, agent_slug: str, task_id: str) -> dict[str, Any]:
    """A record rebuilt from the transcript alone."""
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
    try:
        mtime = md_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    body = _section(text, "Error" if is_error else "Result")
    if body == "(none)":
        body = ""

    return {
        "task_id": task_id,
        "agent": _header_value(text, "Agent") or agent_slug,
        "user_id": 0,
        "chat_id": 0,
        "server_name": _header_value(text, "Server") or None,
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


# ── status.json ────────────────────────────────────────────────────────────


def _from_status(
    status_path: Path, agent_slug: str, task_id: str, md_path: Path
) -> dict[str, Any]:
    """A record rebuilt from its status file, backfilled from the markdown.

    Backfill is what makes a *pre*-FEAT-035 status file useful: it recorded the
    state and the provenance but not the task or the result, and the markdown
    beside it has both.
    """
    from condor.runtime.registry_file import is_stale, read_status

    data = read_status(status_path.parent, status_path.name) or {}
    md = _from_markdown(md_path, agent_slug, task_id) if md_path.is_file() else {}

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
        "started_at": data.get("started_at")
        or md.get("started_at")
        or data.get("updated_at")
        or 0.0,
        "ended_at": data.get("ended_at") or md.get("ended_at") or 0.0,
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


def _records_of(user_id: int | str):
    """Every delegation this user has on disk."""
    try:
        children = sorted(paths.delegations_dir(user_id).iterdir())
    except (OSError, paths.UnsafeIdError):
        return
    for child in children:
        if not child.is_dir():
            continue
        record = _from_dir(child)
        if record is not None:
            yield record


def _legacy_records_in(agent_slug: str, directory: Path):
    """Every delegation in one pre-FEAT-051 directory, status files first."""
    seen: set[str] = set()
    for status_path in sorted(directory.glob(f"*{STATUS_SUFFIX}")):
        task_id = status_path.name[: -len(STATUS_SUFFIX)]
        seen.add(task_id)
        yield _from_status(
            status_path, agent_slug, task_id, directory / f"{task_id}.md"
        )
    for md_path in sorted(directory.glob("*.md")):
        task_id = md_path.name[: -len(".md")]
        if task_id not in seen:
            yield _from_markdown(md_path, agent_slug, task_id)


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
    """
    records: list[dict[str, Any]] = []
    for owner in _owners(user_id):
        try:
            records.extend(_records_of(owner))
        except OSError:
            log.debug("Could not list delegations for user %s", owner, exc_info=True)

    if user_id is None:
        seen = {r["task_id"] for r in records}
        for slug, directory in _legacy_dirs(agent_slug):
            try:
                records.extend(
                    r
                    for r in _legacy_records_in(slug, directory)
                    if r["task_id"] not in seen
                )
            except OSError:
                log.debug("Could not list delegations in %s", directory, exc_info=True)

    if agent_slug:
        records = [r for r in records if r.get("agent") == agent_slug]

    records.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)
    return records[: max(0, limit)]


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
