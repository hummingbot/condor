"""Delegations that outlived the process that ran them (FEAT-035).

The live registry in :mod:`condor.agents.delegate` is in-memory and per-process
by design: membership there means "this can still be running". A restart
therefore used to erase every trace of delegated work from the dashboard, even
though the work itself was on disk the whole time --
``agents/{slug}/delegations/{task_id}.md`` plus a ``{task_id}.status.json``
sidecar, both written by the runner.

This module is the read side of those files, and nothing else: no state, no
writes, no knowledge of the web layer. It rebuilds a delegation record from what
was persisted, so the same routes and the same two React components that serve a
live task can serve one from two weeks ago.

Three shapes of record exist on disk, and all three are readable here:

* **current** -- ``.status.json`` (the whole record) + ``.events.json`` (the
  transcript, in the exact projection the wire uses) + ``.md``.
* **pre-FEAT-035** -- a ``.status.json`` that carries only state and provenance;
  the task text and result come from parsing the markdown header.
* **legacy** -- a lone ``.md``, from before status files existed at all.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATUS_SUFFIX = ".status.json"
EVENTS_SUFFIX = ".events.json"

# A task_id reaches this module straight from a URL path, and every lookup here
# builds a filename out of it. Anything that is not a plain identifier is
# refused rather than sanitized -- ``..`` and separators must never survive into
# a path join.
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# A record whose state was never recorded. Honest: the file exists, so the task
# does; what it ended as we do not know.
UNKNOWN = "unknown"


def _is_safe_task_id(task_id: str) -> bool:
    return bool(task_id) and ".." not in task_id and bool(_SAFE_TASK_ID.match(task_id))


def _delegation_dirs(agent_slug: str | None = None):
    """Every ``delegations/`` directory to look in, newest agents last."""
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


def _from_markdown(md_path: Path, agent_slug: str) -> dict[str, Any]:
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
        "task_id": md_path.name[: -len(".md")],
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


def _from_status(status_path: Path, agent_slug: str) -> dict[str, Any]:
    """A record rebuilt from its status file, backfilled from the markdown.

    Backfill is what makes a *pre*-FEAT-035 status file useful: it recorded the
    state and the provenance but not the task or the result, and the markdown
    beside it has both.
    """
    from condor.runtime.registry_file import is_stale, read_status

    task_id = status_path.name[: -len(STATUS_SUFFIX)]
    data = read_status(status_path.parent, status_path.name) or {}

    md_path = status_path.parent / f"{task_id}.md"
    md = _from_markdown(md_path, agent_slug) if md_path.is_file() else {}

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


def _records_in(agent_slug: str, directory: Path):
    """Every delegation recorded in one directory, status files taking priority."""
    seen: set[str] = set()
    for status_path in sorted(directory.glob(f"*{STATUS_SUFFIX}")):
        record = _from_status(status_path, agent_slug)
        seen.add(record["task_id"])
        yield record
    for md_path in sorted(directory.glob("*.md")):
        task_id = md_path.name[: -len(".md")]
        if task_id not in seen:
            yield _from_markdown(md_path, agent_slug)


def list_history(
    *, agent_slug: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Recorded delegations, newest first.

    Reads only what is on disk -- the caller merges in the live registry, which
    is the authority for anything still running in this process.
    """
    records: list[dict[str, Any]] = []
    for slug, directory in _delegation_dirs(agent_slug):
        try:
            records.extend(_records_in(slug, directory))
        except OSError:
            log.debug("Could not list delegations in %s", directory, exc_info=True)

    records.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)
    return records[: max(0, limit)]


def read_history(task_id: str) -> dict[str, Any] | None:
    """One recorded delegation, or None if nothing on disk describes it."""
    if not _is_safe_task_id(task_id):
        return None
    for slug, directory in _delegation_dirs():
        status_path = directory / f"{task_id}{STATUS_SUFFIX}"
        if status_path.is_file():
            return _from_status(status_path, slug)
        md_path = directory / f"{task_id}.md"
        if md_path.is_file():
            return _from_markdown(md_path, slug)
    return None


def read_history_events(task_id: str) -> tuple[list[dict[str, Any]], str]:
    """``(events, markdown)`` for a recorded delegation.

    The sidecar is preferred: it is the same projection
    :func:`condor.agents.delegate.events_for_wire` serves for a live task, so a
    finished delegation renders identically to a running one. Records written
    before the sidecar existed return no events and their transcript instead --
    the client renders that markdown rather than showing an empty transcript.
    """
    if not _is_safe_task_id(task_id):
        return [], ""

    for _slug, directory in _delegation_dirs():
        sidecar = directory / f"{task_id}{EVENTS_SUFFIX}"
        if sidecar.is_file():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                events = data.get("events") if isinstance(data, dict) else None
                if isinstance(events, list):
                    return events, ""
            except (OSError, json.JSONDecodeError):
                log.debug("Unreadable events sidecar at %s", sidecar, exc_info=True)

        md_path = directory / f"{task_id}.md"
        if md_path.is_file():
            try:
                return [], md_path.read_text(encoding="utf-8")
            except OSError:
                log.debug("Unreadable transcript at %s", md_path, exc_info=True)
                return [], ""
    return [], ""
