"""Tests for the MCP-server settings (§4.3: no user identity dimension)."""

import asyncio
from dataclasses import fields

from mcp_servers.condor import settings as settings_module


def test_settings_carry_no_user_identity():
    """§4.3: the MCP settings expose no user_id/chat_id — identity is gone;
    the only keys are agent attribution + the run capability."""
    names = {f.name for f in fields(settings_module.Settings)}
    assert names == {"agent_slug", "agent_id", "capability"}
    assert not hasattr(settings_module, "ensure_identity")


def test_manage_memory_needs_no_identity(monkeypatch, tmp_path):
    """Memory resolves by agent_slug alone — no identity bootstrap required."""
    from condor.memory import paths as paths_module
    from mcp_servers.condor.tools.memory import manage_memory

    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings_module.settings, "agent_slug", "")

    result = asyncio.run(manage_memory(action="list"))
    assert result == {"index": ""}

    result = asyncio.run(
        manage_memory(
            action="write", name="Fact", content="body", description="desc"
        )
    )
    assert result["saved"] is True
    # The write landed in the global tier: repo-root store/memory.
    assert (tmp_path / "store" / "memory" / "memories" / "fact.md").exists()
