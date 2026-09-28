"""Controller code has one careful path, and every gate holds it (SEC-713).

FEAT-126 made ``manage_agent_controllers`` the way to put controller code on a
server: a drift check against the agent's folder, a backup of the copy it
replaces, and a human before an overwrite. The raw
``manage_controllers(action="upsert", target="controller")`` sat one door over
with none of it, stopped only by a line of prose in a skill.

These drive every gate that sees an agent's tool calls:

- the loop/tick risk callback (``auto_approve_with_risk_check``), in live,
  dry-run and shutdown modes;
- the attended confirmation callback (``build_permission_callback``), reached
  through the ACP client's real entry point and through the pydantic-ai
  client's ``_authorize``;
- the mcp-hummingbot server itself on agent profiles, the backstop for a
  delegated or consulted run, which builds no permission callback at all.

Saved configs are ordinary deploy work and must pass exactly as before.
"""

from __future__ import annotations

import asyncio
import inspect
import typing
from types import SimpleNamespace

import pytest

from condor.acp.client import ACPClient, normalize_tool_call
from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from condor.runtime import confirmations as confirmations_module
from condor.runtime.confirmations import ConfirmationRegistry, build_permission_callback
from condor.runtime.danger import (
    RAW_CONTROLLER_CODE_REFUSAL,
    dry_run_refusal,
    is_controller_template_delete,
    is_dangerous_tool_call,
    is_mutating_tool_call,
    is_raw_controller_code_write,
    shutdown_refusal,
)

USER_ID = 42
SESSION_KEY = "tg:42:main"
OPTIONS = [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny"}]
MODES = ("loop", "dry_run", "shutdown")

# The tool arrives bare on a pydantic-ai seat and namespaced on an ACP one.
NAMES = ("manage_controllers", "mcp__mcp-hummingbot__manage_controllers")

CODE_UPSERT = {
    "action": "upsert",
    "target": "controller",
    "controller_type": "market_making",
    "controller_name": "pmm_king",
    "controller_code": "class PmmKing: ...",
}


def _call(tool: str = "manage_controllers", input_data=None, **fields) -> dict:
    return {
        "tool": tool,
        "title": tool,
        "input": input_data if input_data is not None else fields,
    }


def _risk(tool_call: dict, mode: str = "loop") -> dict:
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), execution_mode=mode
    )
    return asyncio.run(callback(tool_call, OPTIONS))


def _approved(result: dict) -> bool:
    return result["outcome"]["outcome"] == "selected"


class _Channel:
    """Stands in for Telegram/the dashboard; answers with ``answer``."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.delivered = []

    async def deliver(self, pending):
        self.delivered.append(pending)
        await confirmations_module._registry.resolve(
            pending.id, approved=self.answer, by_user_id=USER_ID
        )


@pytest.fixture
def registry(monkeypatch):
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


def _attended(tool_call: dict, channel: _Channel) -> dict:
    callback = build_permission_callback(
        SESSION_KEY, USER_ID, channels=[channel], timeout_seconds=5
    )
    return asyncio.run(callback(tool_call, OPTIONS))


# ── The loop/tick gate ──


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", NAMES)
def test_the_loop_gate_refuses_a_raw_code_upsert_in_every_mode(mode, name):
    result = _risk(_call(name, dict(CODE_UPSERT)), mode)

    assert not _approved(result)
    assert "manage_agent_controllers" in result["reason"]


@pytest.mark.parametrize(
    "input_data",
    [
        {k: v for k, v in CODE_UPSERT.items() if k != "target"},  # missing
        {**CODE_UPSERT, "target": None},
        {**CODE_UPSERT, "target": 7},
        "{not json",  # unreadable arguments
        {"target": "controller"},  # no action to read
    ],
)
@pytest.mark.parametrize("mode", MODES)
def test_an_unreadable_target_fails_closed(input_data, mode):
    call = {"tool": "manage_controllers", "input": input_data}

    assert is_raw_controller_code_write(call)
    result = _risk(call, mode)
    assert not _approved(result)
    assert "manage_agent_controllers" in result["reason"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "fields",
    [
        {"action": "upsert", "target": "config", "config_name": "c", "config_data": {}},
        {"action": "list"},
        {"action": "describe", "controller_name": "pmm_king", "include_code": True},
    ],
)
def test_configs_and_reads_pass_untouched(fields, mode):
    call = _call(**fields)

    assert not is_raw_controller_code_write(call)
    assert not is_dangerous_tool_call(call)
    assert _approved(_risk(call, mode))


def test_the_loop_gate_treats_a_template_delete_like_an_overwriting_sync():
    delete = _call(action="delete", target="controller", controller_name="pmm_king")
    sync = _call(
        "manage_agent_controllers", action="sync", name="pmm_king", overwrite=True
    )

    for call in (delete, sync):
        # No human in a live loop, so both go through (risk-checked, unasked) ...
        assert _approved(_risk(call, "loop"))
        # ... and neither survives a rehearsal or a winddown.
        assert not _approved(_risk(call, "dry_run"))
        assert not _approved(_risk(call, "shutdown"))


# ── The policy in danger.py ──


def test_only_a_template_delete_is_gated_and_it_is_recorded():
    delete = _call(action="delete", target="controller", controller_name="x")
    config_delete = _call(action="delete", target="config", config_name="x")
    no_target = _call(action="delete", controller_name="x")

    assert is_controller_template_delete(delete)
    assert is_dangerous_tool_call(delete)
    assert is_mutating_tool_call(delete)  # everything gated is logged
    assert is_dangerous_tool_call(no_target)  # fails closed
    assert not is_dangerous_tool_call(config_delete)


def test_dry_run_and_shutdown_refuse_a_template_delete_by_policy():
    delete = _call(action="delete", target="controller", controller_name="x")
    config_delete = _call(action="delete", target="config", config_name="x")

    assert dry_run_refusal(delete)
    assert shutdown_refusal(delete)
    assert dry_run_refusal(config_delete) is None
    assert shutdown_refusal(config_delete) is None


def test_agent_controller_sync_is_unchanged():
    plain = _call("manage_agent_controllers", action="sync", name="x")
    overwrite = _call(
        "manage_agent_controllers", action="sync", name="x", overwrite=True
    )

    assert not is_raw_controller_code_write(plain)
    assert not is_raw_controller_code_write(overwrite)
    assert not is_dangerous_tool_call(plain)
    assert is_dangerous_tool_call(overwrite)
    assert _approved(_risk(plain, "loop"))
    assert _approved(_risk(overwrite, "loop"))
    assert dry_run_refusal(plain) and dry_run_refusal(overwrite)
    assert not _approved(_risk(overwrite, "shutdown"))


def test_the_gate_literals_match_the_registered_tool():
    from mcp_servers.hummingbot_api import server

    params = inspect.signature(server.manage_controllers).parameters

    def literals(name: str) -> set[str]:
        found: set[str] = set()
        for arg in (params[name].annotation, *typing.get_args(params[name].annotation)):
            if typing.get_origin(arg) is typing.Literal:
                found.update(typing.get_args(arg))
        return found

    assert {"upsert", "delete"} <= literals("action")
    assert literals("target") == {"controller", "config"}


# ── The attended gate (chat, a specialist with a human watching) ──


@pytest.mark.parametrize("name", NAMES)
def test_the_attended_gate_refuses_without_asking(registry, name):
    channel = _Channel(answer=True)

    result = _attended(_call(name, dict(CODE_UPSERT)), channel)

    assert not _approved(result)
    assert result["reason"] == RAW_CONTROLLER_CODE_REFUSAL
    assert channel.delivered == []  # a human approving it would still skip the backup


def test_the_attended_gate_passes_a_config_upsert_without_asking(registry):
    channel = _Channel()
    call = _call(action="upsert", target="config", config_name="c", config_data={})

    assert _approved(_attended(call, channel))
    assert channel.delivered == []


@pytest.mark.parametrize("answer", [True, False])
def test_the_attended_gate_asks_before_a_template_delete(registry, answer):
    channel = _Channel(answer=answer)
    call = _call(action="delete", target="controller", controller_name="pmm_king")

    result = _attended(call, channel)

    assert len(channel.delivered) == 1
    assert "pmm_king" in channel.delivered[0].summary
    assert _approved(result) is answer


def test_an_acp_seat_is_refused_on_the_wire(registry):
    """The ACP client's real entry point, with the wire's namespaced name."""
    channel = _Channel()
    client = ACPClient(
        command="true",
        permission_callback=build_permission_callback(
            SESSION_KEY, USER_ID, channels=[channel], timeout_seconds=5
        ),
    )
    wire = {
        "toolCallId": "1",
        "title": "mcp__mcp-hummingbot__manage_controllers",
        "status": "pending",
        "rawInput": dict(CODE_UPSERT),
    }

    result = asyncio.run(
        client._on_request_permission(sessionId="s", options=OPTIONS, toolCall=wire)
    )

    assert result == {"outcome": {"outcome": "cancelled"}}
    assert channel.delivered == []


def test_an_acp_loop_seat_is_refused_on_the_wire():
    call = normalize_tool_call(
        {
            "toolCallId": "1",
            "title": "mcp__mcp-hummingbot__manage_controllers",
            "rawInput": dict(CODE_UPSERT),
        }
    )
    assert not _approved(_risk(call, "loop"))


@pytest.mark.parametrize("gate", ["attended", "loop"])
def test_a_pydantic_ai_seat_hands_the_model_the_reason(registry, gate):
    from condor.acp.pydantic_ai_client import PydanticAIClient

    if gate == "attended":
        callback = build_permission_callback(
            SESSION_KEY, USER_ID, channels=[_Channel()], timeout_seconds=5
        )
    else:
        callback = auto_approve_with_risk_check(
            RiskEngine(RiskLimits()), RiskState(), execution_mode="loop"
        )
    client = PydanticAIClient(model="ollama:llama3.1", permission_callback=callback)
    part = SimpleNamespace(
        tool_name="manage_controllers", args=dict(CODE_UPSERT), tool_call_id="t1"
    )

    approved, reason = asyncio.run(client._authorize(part))

    assert not approved
    assert "manage_agent_controllers" in reason


# ── The backstop: mcp-hummingbot on an agent profile ──


@pytest.fixture
def hb_server(monkeypatch):
    from mcp_servers.hummingbot_api import server

    calls = []

    class _Controllers:
        async def create_or_update_controller(self, *args, **kwargs):
            calls.append(("controller", args, kwargs))
            return {"message": "ok"}

    async def fake_manage(**kwargs):
        calls.append(("tool", kwargs))
        return {"message": "ok"}

    async def get_client():
        return SimpleNamespace(controllers=_Controllers())

    monkeypatch.setattr(server.hummingbot_client, "get_client", get_client)
    monkeypatch.setattr(server.controllers_tools, "manage_controllers", fake_manage)
    return server, calls


@pytest.mark.parametrize("profile", ["agent", "tick"])
@pytest.mark.parametrize("target", ["controller", None])
def test_an_agent_profile_refuses_a_code_upsert(
    hb_server, monkeypatch, profile, target
):
    from mcp_servers.hummingbot_api.exceptions import ToolError

    server, calls = hb_server
    monkeypatch.setattr(server.settings, "tool_profile", profile)
    fields = {**CODE_UPSERT, "target": target}

    with pytest.raises(ToolError, match="manage_agent_controllers"):
        asyncio.run(server.manage_controllers(**fields))
    assert calls == []


@pytest.mark.parametrize("profile", ["agent", "tick"])
def test_an_agent_profile_still_writes_configs(hb_server, monkeypatch, profile):
    server, calls = hb_server
    monkeypatch.setattr(server.settings, "tool_profile", profile)

    asyncio.run(
        server.manage_controllers(
            action="upsert", target="config", config_name="c", config_data={}
        )
    )
    assert calls and calls[0][1]["target"] == "config"


def test_the_full_profile_keeps_the_raw_tool(hb_server, monkeypatch):
    """The operator's own surface (standalone, `.mcp.json`) is out of scope."""
    server, calls = hb_server
    monkeypatch.setattr(server.settings, "tool_profile", "full")

    asyncio.run(server.manage_controllers(**CODE_UPSERT))
    assert calls and calls[0][1]["target"] == "controller"
