"""Shared executor record shape + attribution helpers.

The record type used by the append-only per-slug JSONL executor log
(``log.py``) and everything that folds or serves it.
"""

from __future__ import annotations

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
