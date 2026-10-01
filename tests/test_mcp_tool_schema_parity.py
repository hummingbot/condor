"""The tool surface both MCP servers advertise, pinned across the SDK major (FEAT-130).

``tests/fixtures/mcp_tool_surface_v1.json`` was dumped on ``mcp`` 1.26, the last
lock before the move to 2.x. A model picks and fills a tool from exactly three
things — its name, its description and its input schema — so a migration that
"changes nothing" must leave those byte-identical, and this is where a drift
nobody asserted on would otherwise go unnoticed.

Server ``instructions`` are not in the fixture: the condor server assembles them
from the skills and agents on disk, so a frozen copy would pin the machine it was
dumped on. What the SDK could break is the hand-off, and that is asserted here.
"""

import asyncio
import json
from pathlib import Path

import pytest

from mcp_servers.condor import server as condor_server
from mcp_servers.hummingbot_api import server as hb_server

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_tool_surface_v1.json"
SERVERS = {"condor": condor_server, "hummingbot_api": hb_server}
FIELDS = ("name", "description", "inputSchema", "outputSchema")


def live_surface(module) -> dict[str, dict]:
    """``tool name → the fields a client reads``, under the ``full`` profile.

    A fresh server rather than the module's singleton: that one mounts whatever
    profile argv resolved to, which under pytest is the default ring.
    """
    server = type(module.mcp)("surface-probe")
    module.register_tools(server, "full")
    tools = asyncio.run(server.list_tools())
    dumped = (tool.model_dump(by_alias=True) for tool in tools)
    return {d["name"]: {f: d.get(f) for f in FIELDS} for d in dumped}


def dump_fixture() -> None:
    """Rewrite the fixture from the live surface (run by hand, never by a test)."""
    surface = {key: live_surface(module) for key, module in SERVERS.items()}
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n")


@pytest.fixture(scope="module")
def frozen() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("key", sorted(SERVERS))
def test_the_same_tools_are_advertised(key, frozen):
    assert set(live_surface(SERVERS[key])) == set(frozen[key])


@pytest.mark.parametrize("key", sorted(SERVERS))
@pytest.mark.parametrize("field", ["description", "inputSchema"])
def test_what_a_model_reads_is_unchanged(key, field, frozen):
    live = live_surface(SERVERS[key])
    drifted = {
        name: (frozen[key][name][field], tool[field])
        for name, tool in live.items()
        if tool[field] != frozen[key][name][field]
    }
    assert not drifted


@pytest.mark.parametrize("key", sorted(SERVERS))
def test_output_schemas_are_unchanged(key, frozen):
    live = live_surface(SERVERS[key])
    drifted = {
        name: (frozen[key][name]["outputSchema"], tool["outputSchema"])
        for name, tool in live.items()
        if tool["outputSchema"] != frozen[key][name]["outputSchema"]
    }
    assert not drifted


def test_instructions_are_handed_to_the_sdk_unchanged():
    """The server object carries the text built at import, not a rebuilt one.

    Compared against the seat's framing rather than a second
    ``_build_instructions()`` call: the indexes below it are read from disk, and
    a test session's disk is not the one the module was imported on.
    """
    assert condor_server.mcp.instructions.startswith(condor_server._chat_base())
    assert hb_server.mcp.instructions is None
