import asyncio
"""Phase 2 acceptance: routine authoring is target-scoped by the session's
immutable agent capability (§7.2) — a consult/delegation session can never
write another agent's routine directory."""

import mcp_servers.condor.tools.routines as routines_tool
from mcp_servers.condor.settings import settings

ROUTINE_CODE = '''
from pydantic import BaseModel

class Config(BaseModel):
    pass

def run(config, context=None):
    return {"ok": True}
'''


def _patch_agents(monkeypatch, tmp_path):
    import condor.agents.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path)
    for slug in ("owner_agent", "victim_agent"):
        d = tmp_path / slug
        d.mkdir(parents=True)
        (d / "AGENT.md").write_text(
            f"---\nname: {slug}\nserver_required: false\n---\n\nBody.\n"
        )
    monkeypatch.setattr(
        "routines.base.assistant_routines_dir",
        lambda slug=None: tmp_path / (slug or "_chat") / "routines",
    )


def test_cross_agent_routine_authoring_denied(tmp_path, monkeypatch):
    _patch_agents(monkeypatch, tmp_path)
    # Session spawned FOR owner_agent (immutable capability set at spawn).
    monkeypatch.setattr(settings, "agent_slug", "owner_agent", raising=False)

    result = asyncio.run(routines_tool.create_routine(
        name="sneaky", code=ROUTINE_CODE, agent_slug="victim_agent"
    ))
    assert "error" in result and "scoped to your own agent" in result["error"]
    assert not (tmp_path / "victim_agent" / "routines" / "sneaky.py").exists()

    # edit/delete are equally denied
    assert "scoped to your own agent" in asyncio.run(routines_tool.edit_routine(
        "x", ROUTINE_CODE, agent_slug="victim_agent"
    ))["error"]
    assert "scoped to your own agent" in routines_tool.delete_routine(
        "x", agent_slug="victim_agent"
    )["error"]


def test_own_agent_authoring_allowed(tmp_path, monkeypatch):
    _patch_agents(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "agent_slug", "owner_agent", raising=False)

    # Explicitly naming YOURSELF is fine; so is naming nothing.
    result = asyncio.run(routines_tool.create_routine(
        name="mine", code=ROUTINE_CODE, agent_slug="owner_agent"
    ))
    assert result.get("created") is True
    assert (tmp_path / "owner_agent" / "routines" / "mine.py").exists()


def test_chat_coordinator_can_target_explicitly(tmp_path, monkeypatch):
    _patch_agents(monkeypatch, tmp_path)
    # Chat session: no session agent — acts for the human.
    monkeypatch.setattr(settings, "agent_slug", "", raising=False)

    result = asyncio.run(routines_tool.create_routine(
        name="foragent", code=ROUTINE_CODE, agent_slug="victim_agent"
    ))
    assert result.get("created") is True
    assert (tmp_path / "victim_agent" / "routines" / "foragent.py").exists()
