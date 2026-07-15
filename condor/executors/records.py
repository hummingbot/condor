"""Shared executor record shape + attribution helpers.

The record type used by the append-only per-slug JSONL executor log
(``log.py``) and everything that folds or serves it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutorRecord:
    id: str
    type: str
    status: str
    agent_slug: str
    agent_id: str
    strategy: str
    config: dict
    state: dict
    close_reason: Optional[str]
    created_at: float
    updated_at: float
    heartbeat_at: float


def slug_from_run_id(run_id: str) -> str:
    """Derive the agent slug from a run id: strips a trailing session
    ``_N``/``_eN``, delegation ``-dN``, or consult ``-cTS`` suffix. Slugs may
    contain underscores, so only the rightmost suffix is stripped."""
    m = re.match(r"^(.+?)(?:_e?\d+|-[dc]\d+)$", run_id)
    return m.group(1) if m else run_id
