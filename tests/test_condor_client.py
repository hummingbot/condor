"""Tests for the MCP-server → main-process HTTP client."""

import asyncio

import pytest

from mcp_servers.condor import condor_client
from mcp_servers.condor.exceptions import APIError


def test_call_main_api_fails_fast_without_identity(monkeypatch):
    """An MCP server started with no --user-id/CONDOR_USER_ID must raise a
    clear, actionable error instead of minting a JWT for user 0 and letting
    the main process 403 with an opaque 'Access denied' (the exact failure a
    stock `claude` session hits with the repo's identity-less .mcp.json)."""
    monkeypatch.setattr(condor_client.settings, "user_id", 0)

    with pytest.raises(APIError, match="CONDOR_USER_ID"):
        asyncio.run(condor_client.call_main_api("GET", "/agents"))
