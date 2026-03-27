"""Trading Agents API routes."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["agents"])

_DATA_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "trading_agents"


# ── Request/Response Models ──


class RunningInstance(BaseModel):
    agent_id: str
    session_num: int
    status: str
    tick_count: int = 0
    daily_pnl: float = 0.0


class AgentSummary(BaseModel):
    slug: str
    name: str
    description: str
    status: str  # running, paused, stopped, idle
    agent_id: str = ""
    session_count: int = 0
    tick_count: int = 0
    daily_pnl: float = 0.0
    connector: str = ""
    trading_pair: str = ""
    instances: list[RunningInstance] = []


class SessionInfo(BaseModel):
    number: int
    snapshot_count: int = 0
    created_at: str = ""


class AgentDetail(BaseModel):
    slug: str
    name: str
    description: str
    agent_md: str
    config: dict[str, Any] = {}
    learnings: str = ""
    status: str = "idle"
    agent_id: str = ""
    sessions: list[SessionInfo] = []
    instances: list[RunningInstance] = []


class SnapshotSummary(BaseModel):
    tick: int
    timestamp: str = ""
    cost: float = 0.0
    file: str = ""


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    agent_key: str = "claude-code"
    config: dict[str, Any] = {}


class UpdateAgentMdRequest(BaseModel):
    content: str


class UpdateConfigRequest(BaseModel):
    config: dict[str, Any]


class UpdateLearningsRequest(BaseModel):
    content: str


class StartAgentRequest(BaseModel):
    config: dict[str, Any] = {}


# ── Helpers ──


def _get_store():
    from condor.trading_agent.strategy import StrategyStore
    return StrategyStore()


def _get_strategy_by_slug(slug: str):
    store = _get_store()
    strategy = store.get_by_slug(slug)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return strategy


def _get_running_engine(slug: str):
    from condor.trading_agent.engine import get_all_engines
    for engine in get_all_engines().values():
        if engine.strategy.slug == slug and engine.is_running:
            return engine
    return None


def _get_engine_for_slug(slug: str):
    """Get any engine (running or paused) for a slug."""
    from condor.trading_agent.engine import get_all_engines
    for engine in get_all_engines().values():
        if engine.strategy.slug == slug:
            return engine
    return None


def _get_engines_for_slug(slug: str) -> list:
    """Get all engines for a slug (multiple instances possible)."""
    from condor.trading_agent.engine import get_all_engines
    return [e for e in get_all_engines().values() if e.strategy.slug == slug]


def _count_sessions(agent_dir: Path) -> int:
    for dirname in ("sessions", "trading_sessions"):
        sessions_dir = agent_dir / dirname
        if sessions_dir.exists():
            return len([d for d in sessions_dir.iterdir() if d.is_dir() and d.name.startswith("session_")])
    return 0


def _list_sessions(agent_dir: Path) -> list[SessionInfo]:
    sessions = []
    for dirname in ("sessions", "trading_sessions"):
        sessions_dir = agent_dir / dirname
        if not sessions_dir.exists():
            continue
        for d in sorted(sessions_dir.iterdir(), reverse=True):
            if not d.is_dir() or not d.name.startswith("session_"):
                continue
            try:
                num = int(d.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            # Count snapshots
            snap_count = 0
            for snap_dir_name in ("snapshots", "runs"):
                snap_dir = d / snap_dir_name
                if snap_dir.exists():
                    snap_count = len(list(snap_dir.glob("*.md")))
                    break
            created = ""
            if (d / "journal.md").exists():
                import os
                created = str(os.path.getctime(d / "journal.md"))
            sessions.append(SessionInfo(number=num, snapshot_count=snap_count, created_at=created))
    return sessions


def _get_session_dir(agent_dir: Path, session_num: int) -> Path | None:
    for dirname in ("sessions", "trading_sessions"):
        path = agent_dir / dirname / f"session_{session_num}"
        if path.exists():
            return path
    return None


# ── Routes ──


@router.get("", response_model=list[AgentSummary])
async def list_agents(user: WebUser = Depends(get_current_user)):
    """List all trading agents with status."""
    store = _get_store()
    strategies = store.list_all()
    results = []

    for s in strategies:
        engines = _get_engines_for_slug(s.slug)
        status = "idle"
        agent_id = ""
        tick_count = 0
        daily_pnl = 0.0
        instances = []

        for engine in engines:
            info = engine.get_info()
            instances.append(RunningInstance(
                agent_id=info["agent_id"],
                session_num=info["session_num"],
                status=info["status"],
                tick_count=info["tick_count"],
                daily_pnl=info["daily_pnl"],
            ))
            # Use first running instance for top-level fields
            if not agent_id:
                status = info["status"]
                agent_id = info["agent_id"]
                tick_count = info["tick_count"]
                daily_pnl = info["daily_pnl"]

        results.append(AgentSummary(
            slug=s.slug,
            name=s.name,
            description=s.description,
            status=status,
            agent_id=agent_id,
            session_count=_count_sessions(s.agent_dir),
            tick_count=tick_count,
            daily_pnl=daily_pnl,
            connector=s.default_config.get("connector_name", ""),
            trading_pair=s.default_config.get("trading_pair", ""),
            instances=instances,
        ))

    return results


@router.get("/{slug}", response_model=AgentDetail)
async def get_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Get agent detail."""
    strategy = _get_strategy_by_slug(slug)
    agent_dir = strategy.agent_dir

    # Read agent.md raw content
    agent_md_path = agent_dir / "agent.md"
    agent_md = agent_md_path.read_text() if agent_md_path.exists() else ""

    # Read config
    from condor.trading_agent.config import load_agent_config
    config = load_agent_config(agent_dir, strategy.default_config)

    # Read learnings
    learnings_path = agent_dir / "learnings.md"
    learnings = learnings_path.read_text() if learnings_path.exists() else ""

    # Get engine status
    engines = _get_engines_for_slug(slug)
    status = "idle"
    agent_id = ""
    instances = []
    for engine in engines:
        info = engine.get_info()
        instances.append(RunningInstance(
            agent_id=info["agent_id"],
            session_num=info["session_num"],
            status=info["status"],
            tick_count=info["tick_count"],
            daily_pnl=info["daily_pnl"],
        ))
        if not agent_id:
            status = info["status"]
            agent_id = info["agent_id"]

    # List sessions
    sessions = _list_sessions(agent_dir)

    return AgentDetail(
        slug=slug,
        name=strategy.name,
        description=strategy.description,
        agent_md=agent_md,
        config=config.model_dump(),
        learnings=learnings,
        status=status,
        agent_id=agent_id,
        sessions=sessions,
        instances=instances,
    )


@router.post("", response_model=AgentSummary)
async def create_agent(req: CreateAgentRequest, user: WebUser = Depends(get_current_user)):
    """Create a new trading agent."""
    store = _get_store()
    strategy = store.create(
        name=req.name,
        description=req.description,
        agent_key=req.agent_key,
        instructions=req.instructions,
        default_config=req.config,
        created_by=user.id,
    )

    # Save config.yml
    if req.config:
        from condor.trading_agent.config import AgentConfig, save_agent_config
        config = AgentConfig.from_dict(req.config)
        save_agent_config(strategy.agent_dir, config)

    # Create empty learnings.md
    learnings_path = strategy.agent_dir / "learnings.md"
    if not learnings_path.exists():
        learnings_path.write_text("# Learnings\n\n## Active Insights\n\n## Retired Insights\n")

    return AgentSummary(
        slug=strategy.slug,
        name=strategy.name,
        description=strategy.description,
        status="idle",
    )


@router.put("/{slug}")
async def update_agent_md(slug: str, req: UpdateAgentMdRequest, user: WebUser = Depends(get_current_user)):
    """Update agent.md content."""
    strategy = _get_strategy_by_slug(slug)
    agent_md_path = strategy.agent_dir / "agent.md"
    agent_md_path.write_text(req.content)
    return {"updated": True}


@router.put("/{slug}/config")
async def update_agent_config(slug: str, req: UpdateConfigRequest, user: WebUser = Depends(get_current_user)):
    """Update agent config."""
    strategy = _get_strategy_by_slug(slug)
    from condor.trading_agent.config import AgentConfig, save_agent_config
    config = AgentConfig.from_dict(req.config)
    save_agent_config(strategy.agent_dir, config)
    return {"updated": True, "config": config.model_dump()}


@router.delete("/{slug}")
async def delete_agent(slug: str, user: WebUser = Depends(get_current_user)):
    """Delete an agent."""
    strategy = _get_strategy_by_slug(slug)
    # Don't delete if any instance is running
    running = [e for e in _get_engines_for_slug(slug) if e.is_running]
    if running:
        raise HTTPException(status_code=400, detail="Cannot delete a running agent. Stop all instances first.")

    store = _get_store()
    store.delete(strategy.id)
    return {"deleted": True}


# ── Lifecycle ──


@router.post("/{slug}/start")
async def start_agent(slug: str, req: StartAgentRequest, user: WebUser = Depends(get_current_user)):
    """Start an agent (creates new session)."""
    from condor.trading_agent.engine import TickEngine
    from condor.trading_agent.config import load_agent_config

    strategy = _get_strategy_by_slug(slug)

    # Load config (merge request overrides)
    config = load_agent_config(strategy.agent_dir, strategy.default_config)
    config_dict = config.model_dump()
    if req.config:
        config_dict.update(req.config)

    new_engine = TickEngine(
        strategy=strategy,
        config=config_dict,
        chat_id=0,  # Web-launched, no chat
        user_id=user.id,
    )
    await new_engine.start()

    return {"started": True, "agent_id": new_engine.agent_id, "session_num": new_engine.session_num}


@router.post("/{slug}/stop")
async def stop_agent(slug: str, agent_id: str | None = None, user: WebUser = Depends(get_current_user)):
    """Stop a running agent. If agent_id given, stop that specific instance; otherwise stop all."""
    if agent_id:
        from condor.trading_agent.engine import get_engine
        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        await engine.stop()
    else:
        engines = _get_engines_for_slug(slug)
        if not engines:
            raise HTTPException(status_code=404, detail="No running agent found")
        for engine in engines:
            await engine.stop()
    return {"stopped": True}


@router.post("/{slug}/pause")
async def pause_agent(slug: str, agent_id: str | None = None, user: WebUser = Depends(get_current_user)):
    """Pause a running agent."""
    if agent_id:
        from condor.trading_agent.engine import get_engine
        engine = get_engine(agent_id)
        if not engine or not engine.is_running:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found or not running")
        engine.pause()
    else:
        engine = _get_running_engine(slug)
        if not engine:
            raise HTTPException(status_code=404, detail="No running agent found")
        engine.pause()
    return {"paused": True}


@router.post("/{slug}/resume")
async def resume_agent(slug: str, agent_id: str | None = None, user: WebUser = Depends(get_current_user)):
    """Resume a paused agent."""
    if agent_id:
        from condor.trading_agent.engine import get_engine
        engine = get_engine(agent_id)
        if not engine:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        engine.resume()
    else:
        engine = _get_engine_for_slug(slug)
        if not engine:
            raise HTTPException(status_code=404, detail="No agent found")
        engine.resume()
    return {"resumed": True}


# ── Learnings ──


@router.get("/{slug}/learnings")
async def get_learnings(slug: str, user: WebUser = Depends(get_current_user)):
    """Read learnings.md."""
    strategy = _get_strategy_by_slug(slug)
    learnings_path = strategy.agent_dir / "learnings.md"
    content = learnings_path.read_text() if learnings_path.exists() else ""
    return {"content": content}


@router.put("/{slug}/learnings")
async def update_learnings(slug: str, req: UpdateLearningsRequest, user: WebUser = Depends(get_current_user)):
    """Update learnings.md."""
    strategy = _get_strategy_by_slug(slug)
    learnings_path = strategy.agent_dir / "learnings.md"
    learnings_path.write_text(req.content)
    return {"updated": True}


# ── Sessions ──


@router.get("/{slug}/sessions")
async def list_sessions(slug: str, user: WebUser = Depends(get_current_user)):
    """List sessions for an agent."""
    strategy = _get_strategy_by_slug(slug)
    sessions = _list_sessions(strategy.agent_dir)
    return {"sessions": [s.model_dump() for s in sessions]}


@router.get("/{slug}/sessions/{session_num}/journal")
async def get_journal(slug: str, session_num: int, user: WebUser = Depends(get_current_user)):
    """Read journal.md for a session."""
    strategy = _get_strategy_by_slug(slug)
    session_dir = _get_session_dir(strategy.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    journal_path = session_dir / "journal.md"
    content = journal_path.read_text() if journal_path.exists() else ""
    return {"content": content}


@router.get("/{slug}/sessions/{session_num}/snapshots")
async def list_snapshots(slug: str, session_num: int, user: WebUser = Depends(get_current_user)):
    """List snapshots for a session."""
    strategy = _get_strategy_by_slug(slug)
    session_dir = _get_session_dir(strategy.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    snapshots = []
    for snap_dir_name in ("snapshots", "runs"):
        snap_dir = session_dir / snap_dir_name
        if not snap_dir.exists():
            continue
        for f in sorted(snap_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            m = re.match(r"(?:snapshot|run)_(\d+)\.md", f.name)
            if m:
                tick = int(m.group(1))
                # Extract timestamp from file
                content = f.read_text()
                ts_match = re.search(r"^# (?:Snapshot|Tick) #\d+ — (.+)$", content, re.MULTILINE)
                timestamp = ts_match.group(1) if ts_match else ""
                # Extract cost
                cost_match = re.search(r"LLM: \$([0-9.]+)", content)
                cost = float(cost_match.group(1)) if cost_match else 0.0

                snapshots.append(SnapshotSummary(
                    tick=tick,
                    timestamp=timestamp,
                    cost=cost,
                    file=f.name,
                ))
        break  # Use whichever dir exists first

    return {"snapshots": [s.model_dump() for s in snapshots]}


@router.get("/{slug}/sessions/{session_num}/snapshots/{tick}")
async def get_snapshot(slug: str, session_num: int, tick: int, user: WebUser = Depends(get_current_user)):
    """Read a specific snapshot."""
    strategy = _get_strategy_by_slug(slug)
    session_dir = _get_session_dir(strategy.agent_dir, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")

    # Try new format first, then legacy
    for snap_dir_name, prefix in [("snapshots", "snapshot"), ("runs", "run")]:
        path = session_dir / snap_dir_name / f"{prefix}_{tick}.md"
        if path.exists():
            return {"content": path.read_text(), "tick": tick}

    raise HTTPException(status_code=404, detail=f"Snapshot {tick} not found")
