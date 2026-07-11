"""Tool profiles: call-time restriction of the condor MCP subprocess — the
line that holds for ACP models, which cannot be tool-allowlisted client-side."""

import asyncio

from mcp_servers.condor import middleware
from mcp_servers.condor.middleware import handle_errors
from mcp_servers.condor.settings import settings


@handle_errors("manage skill")
async def manage_skill(**kw):
    return {"ok": "skill"}


@handle_errors("manage trading agent")
async def manage_trading_agent(**kw):
    return {"ok": "lifecycle"}


@handle_errors("delegate task")
async def delegate(**kw):
    return {"ok": "delegate"}


def _with_profile(monkeypatch, profile):
    monkeypatch.setattr(settings, "tool_profile", profile, raising=False)


def test_no_profile_allows_everything(monkeypatch):
    _with_profile(monkeypatch, "")
    assert asyncio.run(manage_trading_agent())["ok"] == "lifecycle"


def test_curation_profile_blocks_lifecycle_and_delegation(monkeypatch):
    _with_profile(monkeypatch, "curation")
    assert asyncio.run(manage_skill())["ok"] == "skill"
    blocked = asyncio.run(manage_trading_agent())
    assert "does not permit manage_trading_agent" in blocked["error"]
    assert "error" in asyncio.run(delegate())


def test_unknown_profile_fails_closed(monkeypatch):
    _with_profile(monkeypatch, "bogus")
    res = asyncio.run(manage_skill())
    assert "unknown tool profile" in res["error"]


def test_curation_profile_lists_exact_allowed_set():
    allowed = middleware.TOOL_PROFILES["curation"]
    # Deliberately minimal: skills + journal + memory reads. No lifecycle,
    # no delegation, no routines, no notifications.
    assert "manage_skill" in allowed
    assert "trading_agent_journal_write" in allowed
    for forbidden in ("manage_trading_agent", "delegate", "consult",
                      "manage_routines", "send_notification"):
        assert forbidden not in allowed
