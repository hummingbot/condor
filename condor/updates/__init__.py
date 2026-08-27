"""The update engine: what can be updated, whether it can, and what happened.

Headless on purpose. Nothing in here imports Telegram or FastAPI — the surfaces
are views over this API, which is why the dashboard costs no orchestration:

    check()                 what version each component is on, cached 60s
    preflight(keys)         blockers, warnings and the plan
    resolve(key, action)    act on a blocker's offered resolution
    start(keys, ...)        begin a run; returns immediately with the run
    current()               the run in flight, or the last one
    read_journal()          the durable record, across restarts
    register_observer(fn)   in-process, zero-latency step transitions
    finalize_pending_run()  judge the run the restart interrupted (boot only)
"""

from condor.updates.components import (
    CONDOR,
    HUMMINGBOT_API,
    Block,
    Component,
    ComponentStatus,
    Facet,
    Preflight,
    Warning,
    check,
    hb_api_base_url,
    invalidate,
    keys,
    preflight,
    repo_blocks,
    status,
)
from condor.updates.run import (
    Run,
    Step,
    current,
    finalize_pending_run,
    read_journal,
    register_observer,
    resolve,
    start,
    tail,
    unregister_observer,
)

__all__ = [
    "CONDOR",
    "HUMMINGBOT_API",
    "Block",
    "Component",
    "ComponentStatus",
    "Facet",
    "Preflight",
    "Run",
    "Step",
    "Warning",
    "check",
    "current",
    "finalize_pending_run",
    "hb_api_base_url",
    "invalidate",
    "keys",
    "preflight",
    "read_journal",
    "repo_blocks",
    "register_observer",
    "resolve",
    "start",
    "status",
    "tail",
    "unregister_observer",
]
