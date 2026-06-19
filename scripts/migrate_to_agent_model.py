"""Idempotent migration to the unified Agent model (FEAT-004).

Splits the two pre-existing "cobaya" agents into the new shape:

- **executor_manager** — already an ``AGENT.md`` consult agent. Drop the now-dead
  ``role: expert`` frontmatter key (consultability is derived from
  ``when_to_consult`` + a pydantic-ai ``agent_key``). No strategies.

- **brigado** — a loop-only agent stored as one ``agent.md``. Split it: the
  identity/objective becomes ``trading_agents/brigado/AGENT.md`` (not consultable),
  and the tactic + config becomes ``trading_agents/brigado/strategies/brl_mm/
  strategy.md``. ``routines/``, ``learnings.md`` and any ``sessions/`` move under
  the strategy; the shared brain (``store/``, ``skills/``) stays at the Agent level.

Re-running is safe: each agent is skipped once it is already in the new shape.

Usage::

    uv run python scripts/migrate_to_agent_model.py
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from condor.trading_agent.strategy import (  # noqa: E402
    _parse_frontmatter,
    _render_frontmatter,
)

log = logging.getLogger("migrate_to_agent_model")

_DATA_ROOT = Path(__file__).resolve().parent.parent / "trading_agents"


def _migrate_executor_manager() -> bool:
    """Drop ``role: expert`` from executor_manager's AGENT.md. Returns True if changed."""
    agent_md = _DATA_ROOT / "executor_manager" / "AGENT.md"
    if not agent_md.exists():
        log.info("executor_manager: no AGENT.md, nothing to migrate")
        return False
    meta, body = _parse_frontmatter(agent_md.read_text())
    if "role" not in meta:
        log.info("executor_manager: already migrated (no `role`)")
        return False
    meta.pop("role", None)
    agent_md.write_text(_render_frontmatter(meta, body))
    log.info("executor_manager: dropped `role: expert`")
    return True


def _split_body(body: str) -> tuple[str, str]:
    """Split brigado's body into (identity, tactic) at the first ``### Routines``.

    Everything before the operational sections is the Agent's domain identity;
    ``### Routines`` onward is the strategy tactic. If the marker is absent the
    whole body is treated as identity and the tactic is left empty.
    """
    marker = "### Routines"
    idx = body.find(marker)
    if idx == -1:
        return body.strip(), ""
    return body[:idx].rstrip(), body[idx:].strip()


def _migrate_brigado() -> bool:
    """Split brigado's agent.md into AGENT.md + strategies/brl_mm/strategy.md."""
    agent_dir = _DATA_ROOT / "brigado"
    strategy_dir = agent_dir / "strategies" / "brl_mm"
    if (strategy_dir / "strategy.md").exists():
        log.info("brigado: already migrated (strategies/brl_mm/strategy.md exists)")
        return False

    agent_md = agent_dir / "agent.md"
    if not agent_md.exists():
        log.info("brigado: no agent.md, nothing to migrate")
        return False

    # Read BEFORE writing — on case-insensitive filesystems AGENT.md and agent.md
    # are the same path, so writing AGENT.md would clobber the source otherwise.
    meta, body = _parse_frontmatter(agent_md.read_text())
    identity, tactic = _split_body(body)

    # 1. Strategy: tactic + config (model inherited from the Agent => agent_key null).
    strategy_meta = {
        "name": "BRL MM",
        "description": meta.get("description", ""),
        "agent_key": None,
        "skills": meta.get("skills", []) or [],
        "default_config": meta.get("default_config", {}) or {},
        "default_trading_context": meta.get("default_trading_context", ""),
        "created_by": meta.get("created_by", 0),
        "created_at": meta.get("created_at", ""),
    }
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.md").write_text(
        _render_frontmatter(strategy_meta, tactic or "(no tactic body)")
    )
    log.info("brigado: wrote strategies/brl_mm/strategy.md")

    # 2. Move operational history under the strategy.
    for name in ("routines", "sessions", "dry_runs"):
        src = agent_dir / name
        if src.exists() and src.is_dir():
            dst = strategy_dir / name
            if not dst.exists():
                shutil.move(str(src), str(dst))
                log.info("brigado: moved %s/ -> strategies/brl_mm/%s/", name, name)
    learnings = agent_dir / "learnings.md"
    if learnings.exists() and not (strategy_dir / "learnings.md").exists():
        shutil.move(str(learnings), str(strategy_dir / "learnings.md"))
        log.info("brigado: moved learnings.md -> strategies/brl_mm/learnings.md")

    # 3. Agent identity: write AGENT.md (replaces agent.md on case-insensitive FS).
    agent_meta = {
        "name": meta.get("name", "Brigado"),
        "description": meta.get("description", ""),
        "agent_key": meta.get("agent_key", "claude-code"),
        "tools": [],
        "when_to_consult": "",  # loop-only => not consultable
        "server_required": True,
        "created_by": meta.get("created_by", 0),
        "created_at": meta.get("created_at", ""),
    }
    (agent_dir / "AGENT.md").write_text(
        _render_frontmatter(agent_meta, identity or "## Brigado")
    )
    log.info("brigado: wrote AGENT.md")

    # 4. Remove a distinct lowercase agent.md (case-sensitive filesystems only).
    #    On case-INsensitive filesystems agent.md and AGENT.md are the SAME inode,
    #    so we must compare by inode (samefile) — Path.resolve() keeps the literal
    #    case and would wrongly report them as different, deleting the AGENT.md.
    import os

    new_agent_md = agent_dir / "AGENT.md"
    if (
        agent_md.exists()
        and new_agent_md.exists()
        and not os.path.samefile(agent_md, new_agent_md)
    ):
        agent_md.unlink()
        log.info("brigado: removed legacy agent.md")

    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not _DATA_ROOT.exists():
        log.info("No trading_agents/ directory — nothing to migrate.")
        return 0
    changed = False
    changed |= _migrate_executor_manager()
    changed |= _migrate_brigado()
    log.info("Migration %s.", "applied" if changed else "already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
