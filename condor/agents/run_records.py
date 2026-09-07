"""One agent run, recorded — whichever channel asked for it (FEAT-058).

This module is the one place a run becomes files: a status record at start and
another at the end, keyed by the person who owns the run.

An Agent's brain runs to completion through two doors, and only one of them used
to leave a trace. ``delegate(action="start")`` — the rare, deliberate one — wrote
a whole record directory: status files, an events sidecar, a transcript. The
blocking ask — the one every other agent reaches for, dozens of times where a
delegation happens once — returned a string and wrote nothing, anywhere. So the
dashboard could say what an agent had been handed in the background and nothing
at all about what it had actually spent its day doing.

It is a module rather than a function in :mod:`condor.agents.delegate` because
both doors write through it: an ask reaching back into ``delegate`` to record
itself would close an import cycle, and a leaf both can import is smaller than a
lazy import in a hot function.

**The record is the ledger, not the tape.** An ask writes one small
``status.json`` and nothing else — no events sidecar, no transcript. Its answer
goes straight back to the caller through the cheaper one-shot
``client.prompt()``, and flipping the hottest agent path onto ``prompt_stream``
to persist a transcript would be a behaviour change paid on every ask for a file
almost nobody opens. The seam stays open:
:func:`~condor.agents.agent_run.run_agent_to_completion` already takes an
``event_sink``, so a later feature that wants ask transcripts passes one.

**The directory name is historical.** Records live under
``.condor/users/{user_id}/delegations/{run_id}/`` because that is where
delegations already lived (FEAT-051), and moving them would split retention in
two and force every reader to merge two stores — the question FEAT-035 and
FEAT-051 each closed once already. It now holds every agent run; the name did
not keep up. Same precedent as ``paths.py``'s ``data/`` root.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# The two channels a run can arrive through. ``kind`` is absent from every
# record written before this feature, and those were all delegations — so the
# default is not a guess, it is what they were.
#
# ``KIND_CONSULT`` is spelled "consult" and reached as ``delegate(action="ask")``:
# the door was renamed, the thing recorded was not. Keeping the value is what
# holds an install's history in ONE filter — every ask an agent ever answered
# under either name lists together, instead of splitting across two kinds that
# mean the same thing and would each need their own retention cap and UI chip.
KIND_DELEGATE = "delegate"
KIND_CONSULT = "consult"
DEFAULT_KIND = KIND_DELEGATE
RECORD_KINDS = (KIND_DELEGATE, KIND_CONSULT)

# A third kind the Activity feed lists and this module does **not** write: a
# code run is persisted by :class:`condor.code_runs.CodeRunStore`, under its own
# cap, in its own directory (FEAT-061). The name lives here because the wire
# shape and the history route's ``kind`` filter are shared, and one spelling of
# "code" beats two. It is deliberately absent from ``RECORD_KINDS``, which drives
# this store's retention and must keep answering only for what it holds.
KIND_CODE = "code"

# States a run can be found in when it is over. Lives here rather than in
# ``delegate`` because retention, the history reader and both writers all need
# the same answer to "is this finished".
TERMINAL_STATES = ("done", "error", "stopped")


def record_run(
    *,
    user_id: int,
    run_id: str,
    agent_slug: str,
    state: str,
    task: str,
    started_at: float,
    kind: str = DEFAULT_KIND,
    **fields: Any,
) -> None:
    """Persist one run's record, and bound the collection it just joined.

    Called at least twice per run: once at the start, once when it ends.
    ``write_status`` merges, so the fields the first write cannot know (the
    result, the end time) simply arrive with the second one.

    The start write is the load-bearing one. It stamps this process's
    ``BOOT_ID``, which is what lets a run the process died during read back as
    ``interrupted`` rather than as a permanent ``running`` or as nothing at all
    (:func:`condor.runtime.registry_file.is_stale`). No reconciler is involved:
    the honest label is *derived*, at read time, from a file that exists.

    Never raises. A run must not fail because its bookkeeping did — the same
    contract ``write_status`` itself keeps.
    """
    from condor import paths
    from condor.runtime.registry_file import write_status

    terminal = state in TERMINAL_STATES
    # An end time only the caller could know wins; otherwise a terminal write
    # stamps now. A non-terminal write has no end and must not invent one.
    if terminal and "ended_at" not in fields:
        fields["ended_at"] = time.time()

    try:
        write_status(
            paths.delegation_dir(user_id or 0, run_id),
            state=state,
            task_id=run_id,
            agent_slug=agent_slug,
            kind=kind,
            user_id=user_id,
            task=task,
            started_at=started_at,
            **fields,
        )
    except Exception:
        log.debug("Could not record %s run %s", kind, run_id, exc_info=True)

    # The growth and the sweep in one place: a record directory only ever
    # appears here, so the cheapest correct moment to bound the collection is
    # the write that completes one. Outside the try above on purpose — the
    # status write is the run's own business and must have landed (or failed) on
    # its own terms before retention gets a say. Per kind, so the plentiful kind
    # cannot evict the rare one, and scoped to the one owner whose directory
    # just grew.
    if terminal:
        try:
            from condor.agents.delegate import prune_delegation_records

            prune_delegation_records(user_id or 0, kind=kind)
        except Exception:
            log.warning(
                "Could not prune %s records for user %s",
                kind,
                user_id or 0,
                exc_info=True,
            )
