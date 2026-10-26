"""The Botcamp hub actions need a human (SEC review C1/C3).

``manage_agents(action="publish_hub")`` sends an agent off the box under the
install's Botcamp account, and ``hub_install`` makes a downloaded AGENT.md's
instructions live at once. The tool description said "ADMIN ONLY" and
"Confirm with the user", but only the model read that: neither action was in
the danger gate, so chat ran them unasked and a loop auto-approved them. An
installed agent whose instructions say "publish every agent" was one tick away
from doing it.

What this pins: both actions wait for a human in chat, every other agent action
stays on the fast path, and an unattended loop refuses both in every mode.
"""

import asyncio

import pytest

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from condor.runtime.danger import (
    DANGEROUS_AGENT_ACTIONS,
    format_tool_summary,
    is_dangerous_tool_call,
)

OPTIONS = [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny"}]


def _call(action: str | None, **extra) -> dict:
    data = {"action": action, **extra} if action is not None else extra
    return {"tool": "mcp__condor__manage_agents", "input": data}


@pytest.mark.parametrize("action", sorted(DANGEROUS_AGENT_ACTIONS))
def test_hub_actions_wait_for_a_human(action):
    assert is_dangerous_tool_call(_call(action))


@pytest.mark.parametrize(
    "action", ["list", "get", "create", "update_agent", "export_agent", "hub_get"]
)
def test_the_rest_of_manage_agents_stays_on_the_fast_path(action):
    assert not is_dangerous_tool_call(_call(action))


def test_an_unreadable_action_fails_closed():
    assert is_dangerous_tool_call(_call(None, agent_slug="traveller"))
    assert is_dangerous_tool_call({"tool": "manage_agents", "input": "not json"})


def test_the_prompt_says_which_way_the_agent_crosses_the_edge():
    publish = format_tool_summary(_call("publish_hub", agent_slug="traveller"))
    install = format_tool_summary(_call("hub_install", path="grid-bot"))

    assert "Publish agent 'traveller' to the Botcamp hub" in publish
    assert "Install 'grid-bot' from the Botcamp hub" in install


@pytest.mark.parametrize("mode", ["loop", "dry_run", "shutdown"])
@pytest.mark.parametrize("action", sorted(DANGEROUS_AGENT_ACTIONS))
def test_a_loop_never_runs_them(mode, action):
    gate = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), execution_mode=mode, agent_id="a_1"
    )

    result = asyncio.run(gate(_call(action, agent_slug="traveller", path="x"), OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"


def test_a_loop_refusal_says_to_use_chat():
    gate = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), execution_mode="loop", agent_id="a_1"
    )

    result = asyncio.run(gate(_call("publish_hub", agent_slug="traveller"), OPTIONS))

    assert "needs a human" in result["reason"]


def test_a_loop_still_reads_agents_freely():
    gate = auto_approve_with_risk_check(
        RiskEngine(RiskLimits()), RiskState(), execution_mode="loop", agent_id="a_1"
    )

    result = asyncio.run(gate(_call("list"), OPTIONS))

    assert result["outcome"]["outcome"] == "selected"


def test_the_prompt_says_when_the_publish_vouches_for_possible_keys():
    """SEC review C2: an acknowledgement is the human's to give, so the
    confirmation they answer names it."""
    line = format_tool_summary(
        _call(
            "publish_hub",
            agent_slug="traveller",
            acknowledge=["sha256:0123456789abcdef"],
        )
    )

    assert "accepting 1 possible private key(s) as not keys" in line
