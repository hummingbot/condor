"""Every stretch of work an agent has done, whatever door it came through (FEAT-111).

:func:`condor.agents.sessions_index.list_runs` answers for the two kinds of run
that live under a strategy directory — a loop's ``sessions/session_N/`` and an
experiment's ``dry_runs/experiment_M.md``. That is the whole of what the Runs
rail used to show, and it means an agent with no strategy at all — which is most
of them, and is Condor — had **no runs**: its entire history of work was
invisible on the one screen built to show an agent's history of work.

Two more kinds were already on disk and simply never enumerated. A
**delegation** (or a consult) writes a ``status.json`` per run under
``.condor/users/{u}/delegations/{run_id}/`` (FEAT-058). A **conversation**
writes a ``meta.json`` beside its transcript. This module is the union of the
four, and nothing else.

**Metadata only, and that is the whole cost story.** A ``status.json``, a
``meta.json``, a directory listing — no Hummingbot request, no performance fetch
and no transcript read anywhere, the same discipline :mod:`condor.agents.fleet_map`
holds itself to. Four enumerations behind one call are affordable exactly
because none of them is expensive; anything a row needs beyond metadata is
fetched when the row is *opened*, not when it is listed.

**The id grammar is ``kind:id``.** ``s:3``, ``e:1``, ``d:abc123``, ``c:7f3a``.
A session's and an experiment's ids are numbers; a delegation's and a
conversation's are opaque strings, so the URL grammar had to grow a *shape*
rather than another letter. The old two-character form (``s3``, ``e1``) still
parses on the reader's side forever — it is in bookmarks and in notification
payloads — but it is no longer what gets written. Same one-way door
``views.ts`` opened for ``?tab=`` against ``?view=``.

**A chat belongs to nobody's strategy.** The two new kinds carry an empty
``strategy_slug``: a strategy is a loop concept, and filing a conversation under
a pseudo-strategy would put a row in the rail's strategy chips that
``pickStrategy`` cannot resolve. What they carry instead is a ``title`` — the
conversation's own, or the delegation's ask — because "S3 · brl mm" reads for a
loop run and nothing reads for a chat.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

KIND_SESSION = "session"
KIND_EXPERIMENT = "experiment"
KIND_DELEGATION = "delegation"
KIND_CONVERSATION = "conversation"

#: The kinds that are a sequence of ticks under a strategy. The other two are
#: one stretch of work each and have neither ticks nor a strategy.
LOOP_KINDS = (KIND_SESSION, KIND_EXPERIMENT)

#: The letter each kind takes in a ``?run=`` value. Mirrored by
#: ``frontend/src/components/agent/lab/runs.ts``; the two lists are four
#: characters long and are checked against each other by tests on both sides.
KIND_LETTERS = {
    KIND_SESSION: "s",
    KIND_EXPERIMENT: "e",
    KIND_DELEGATION: "d",
    KIND_CONVERSATION: "c",
}

#: What one page of the rail holds. A chatty install has hundreds of
#: conversations and the rail shows a window, not the archive.
DEFAULT_LIMIT = 100

#: How long a row's title may be before it is cut. Long enough for a real ask,
#: short enough that a 260px rail does not wrap three times.
TITLE_MAX_CHARS = 90


def run_id_for(kind: str, ident: Any) -> str:
    """``s:3`` | ``e:1`` | ``d:abc123`` | ``c:7f3a`` — the ``?run=`` value."""
    return f"{KIND_LETTERS.get(kind, kind[:1] or '?')}:{ident}"


def _title(text: str, limit: int = TITLE_MAX_CHARS) -> str:
    """One line, bounded. A task's ask is a paragraph; a rail row is not."""
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


# ── The two kinds that live under a strategy ──


def _loop_runs(agent_slug: str, limit: int) -> list[dict[str, Any]]:
    """Sessions and experiments, across every strategy this agent owns.

    Exactly what the rail listed before this module existed, re-keyed into the
    ``kind:id`` grammar. A strategy whose directory cannot be read contributes
    no rows rather than failing the whole listing: one unreadable playbook must
    not cost the reader every other run the agent has.
    """
    from condor.agents.sessions_index import list_runs
    from condor.agents.strategy import StrategyStore

    rows: list[dict[str, Any]] = []
    for strategy in StrategyStore().list(agent_slug):
        try:
            # ``{agent}.{strategy}`` is the run key everywhere in this codebase.
            runs = list_runs(strategy.dir, f"{agent_slug}.{strategy.slug}")
        except Exception:  # noqa: BLE001 - one bad playbook, not the whole rail
            log.debug(
                "Could not index runs for %s/%s",
                agent_slug,
                strategy.slug,
                exc_info=True,
            )
            continue
        for run in runs[:limit]:
            row = dict(run)
            number = int(row.get("number") or 0)
            row["id"] = str(number)
            row["run_id"] = run_id_for(str(row.get("kind") or ""), number)
            row["strategy_slug"] = strategy.slug
            row["strategy_name"] = strategy.name
            row["title"] = ""
            rows.append(row)
    return rows


# ── Delegations and consults (FEAT-058's records) ──


def _delegation_runs(
    agent_slug: str, user_id: int | str | None, limit: int
) -> list[dict[str, Any]]:
    """Background work asked of this agent, from its own status files.

    ``list_history`` orders before it hydrates, so this pays one ``read_status``
    per record it actually returns and a ``stat()`` for the rest — the cost the
    Activity feed already pays for the same rows.
    """
    from condor.agents.delegation_history import list_history

    try:
        records = list_history(user_id=user_id, agent_slug=agent_slug, limit=limit)
    except Exception:  # noqa: BLE001 - an unreadable store costs its own rows only
        log.debug("Could not list delegations for %s", agent_slug, exc_info=True)
        return []

    rows: list[dict[str, Any]] = []
    for record in records:
        task_id = str(record.get("task_id") or "")
        if not task_id:
            continue
        status = str(record.get("status") or "")
        started = float(record.get("started_at") or 0.0) or None
        ended = float(record.get("ended_at") or 0.0) or None
        rows.append(
            {
                "run_id": run_id_for(KIND_DELEGATION, task_id),
                "kind": KIND_DELEGATION,
                "id": task_id,
                # A delegation has no ordinal; the rail badges it by kind.
                "number": 0,
                "agent_id": "",
                "status": status,
                # ``delegate`` or ``consult`` — which channel asked, not a mode.
                "execution_mode": str(record.get("kind") or ""),
                "tick_count": 0,
                "snapshot_count": 0,
                "started_at": started,
                # A run still going has no end, the same rule a live session
                # gets: the last recorded stamp is not one.
                "ended_at": None if status == "running" else ended,
                "error": status == "error",
                "has_actions_log": False,
                "strategy_slug": "",
                "strategy_name": "",
                "title": _title(str(record.get("task") or "")),
            }
        )
    return rows


# ── Conversations ──


def _conversation_owners(user_id: int | str | None):
    """Whose conversations to read. ``None`` means every user (admin only)."""
    if user_id is not None:
        return [user_id]
    from condor import paths

    try:
        return list(paths.iter_user_ids())
    except OSError:
        return []


def _conversation_runs(
    agent_slug: str, user_id: int | str | None, limit: int
) -> list[dict[str, Any]]:
    """Chats had with this agent, from one ``meta.json`` each.

    An unbound conversation is a conversation with Condor — the settled rule in
    :mod:`condor.runtime.binding` and in :mod:`condor.agents.deeds` — so the
    empty slug on disk resolves to :data:`condor.memory.paths.CHAT_SLUG` before
    it is compared. Without that, Condor's own rail would be empty for exactly
    the conversations it is meant to show.
    """
    from condor.memory.paths import CHAT_SLUG
    from condor.runtime.conversations import list_conversations

    rows: list[dict[str, Any]] = []
    for owner in _conversation_owners(user_id):
        try:
            metas = list_conversations(owner, limit=limit)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - one owner's store, not the listing
            log.debug("Could not list conversations for %s", owner, exc_info=True)
            continue
        for meta in metas:
            if (meta.agent_slug or CHAT_SLUG) != agent_slug:
                continue
            rows.append(
                {
                    "run_id": run_id_for(KIND_CONVERSATION, meta.id),
                    "kind": KIND_CONVERSATION,
                    "id": meta.id,
                    "number": 0,
                    "agent_id": "",
                    # A conversation has no engine state to report. Blank is the
                    # honest answer, and it keeps the rail's live dot off.
                    "status": "",
                    "execution_mode": "",
                    # Ticks are a loop concept. A chat has turns, and the rail
                    # says so rather than reporting "0 ticks".
                    "tick_count": 0,
                    "snapshot_count": 0,
                    "started_at": meta.created_at.timestamp(),
                    "ended_at": meta.updated_at.timestamp(),
                    "error": False,
                    "has_actions_log": False,
                    "strategy_slug": "",
                    "strategy_name": "",
                    "title": _title(meta.title or meta.last_snippet),
                }
            )
    return rows


# ── The union ──


def list_all_runs(
    agent_slug: str,
    user_id: int | str | None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Every run of this agent — loops, experiments, delegations and chats.

    Newest first by when the run *started*, which is the axis the rail already
    prints beside every row ("4h ago"). Capped at ``limit``: each source is
    asked for at most that many, so the merge can never need more, and the union
    is truncated to it after the sort. That is the paging — the rail asks for a
    bigger window when the reader wants one.

    ``user_id`` scopes the two per-user kinds. A conversation is private, so two
    people looking at the same agent legitimately see different rails; the rail
    says so rather than letting it read as data loss. ``None`` means every user
    and is reachable only from an admin path.
    """
    cap = max(0, limit)
    if not cap:
        return []

    rows: list[dict[str, Any]] = []
    rows.extend(_loop_runs(agent_slug, cap))
    rows.extend(_delegation_runs(agent_slug, user_id, cap))
    rows.extend(_conversation_runs(agent_slug, user_id, cap))

    rows.sort(
        key=lambda r: (r.get("started_at") or 0.0, r.get("number") or 0),
        reverse=True,
    )
    return rows[:cap]
