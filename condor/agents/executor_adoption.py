"""Session lineage of executors, and which of them have lost their owner (CORR-726).

An executor's ``controller_id`` is the *session* tag
``"{agent_slug}.{strategy_slug}_{N}"`` (``TickEngine.__post_init__``), and ``N``
is minted afresh on every start or restart. So the tag identifies a run, not a
strategy: the executors a previous session opened carry a tag no later session
ever matches, and the stop gate — which allows a stop only for an executor in
this tick's own snapshot or tagged with this session's own id
(``condor.agents.risk._stop_refusal``) — refuses to stop them. One LP executor
left that way kept polling every second for 13.5h and ate the host's whole RPC
budget.

This module answers the one question both the fix's halves need: *which running
executors belong to this strategy's lineage but to no session that is still
alive?* The conjunction is what keeps it safe. Lineage alone would let a session
stop a live sibling's position, which is exactly the harm the gate exists to
prevent; a dead owner alone would let it stop another strategy's.

The authority on which session numbers are alive is the supervisor's registry
(``LoopSupervisor.for_strategy``), never a status file: a run is registered for
exactly as long as its engine is in memory.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

_RUNNING = "RUNNING"


def session_lineage(agent_slug: str, strategy_slug: str) -> str:
    """The prefix every session tag of this ``(agent, strategy)`` carries."""
    if not agent_slug or not strategy_slug:
        return ""
    return f"{agent_slug}.{strategy_slug}_"


def lineage_session_num(
    controller_id: Any, agent_slug: str, strategy_slug: str
) -> int | None:
    """``N`` when ``controller_id`` is a real session of this strategy, else ``None``.

    ``None`` for another agent, another strategy, an experiment (``_e3``), the
    backend's default ``"main"`` tag, and anything else that is not this
    lineage followed by a plain number. Slugs never contain a dot
    (``condor.frontmatter.slugify``), so the prefix cannot straddle two names;
    and a sibling strategy whose slug merely *starts* with ours leaves a
    non-numeric remainder, so it is rejected too.
    """
    prefix = session_lineage(agent_slug, strategy_slug)
    tag = str(controller_id or "")
    if not prefix or not tag.startswith(prefix):
        return None
    suffix = tag[len(prefix) :]
    if not suffix.isdecimal():
        return None
    return int(suffix)


def orphaned_running_rows(
    rows: Iterable[Mapping[str, Any]] | None,
    agent_slug: str,
    strategy_slug: str,
    live_sessions: Iterable[int],
) -> list[dict]:
    """The RUNNING rows of this lineage whose owning session is not alive.

    ``rows`` are raw executor records as ``search_executors`` returns them;
    ``live_sessions`` is the set of session numbers the supervisor currently
    holds engines for — including the caller's own, so a session never reports
    its own executors as orphaned.

    Status is re-checked here rather than trusted from the caller's filter: a
    terminated executor has nothing to adopt, and the terminated-with-position
    case has its own, different recovery path (``list_orphaned_positions``).
    """
    live = set()
    for num in live_sessions or ():
        try:
            live.add(int(num))
        except (TypeError, ValueError):
            continue

    out: list[dict] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "").strip().upper() != _RUNNING:
            continue
        cfg = row.get("config")
        cfg = cfg if isinstance(cfg, Mapping) else {}
        # Same precedence as ``build_executor_row``: the creator's tag is on the
        # record, and what is left inside ``config`` is the backend default.
        controller = str(row.get("controller_id") or cfg.get("controller_id") or "")
        num = lineage_session_num(controller, agent_slug, strategy_slug)
        if num is None or num in live:
            continue
        if not str(row.get("id") or row.get("executor_id") or ""):
            continue
        out.append(dict(row))
    return out
