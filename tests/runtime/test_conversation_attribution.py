"""What a conversation's positions are called, and who checks (CORR-325).

Two facts that were each unwritten, and which cost the same thing together.

**A chat was never told to tag anything.** The instruction to pass
``controller_id`` lived in exactly one place — the loop's ``[TICK INFO]`` block
— nested inside ``if agent_id:``, and ``agent_id`` is a *session* concept. A
conversation has none, so it never reached the instruction and had no string to
pass. Every executor a chat opened was born untagged and attributable to
nothing, which is why the conversation deployment panel (FEAT-110) could show
bots and controllers and never positions.

**And nothing caught it.** The risk gate refuses an untagged create outright,
which would have made this loud. It is not on this path: the chat wires
``build_permission_callback`` — a human — and the gate is the *loop's* seat
alone. That ambiguity is what stalled the item, so it is pinned here from both
ends: where the gate is installed, and what each callback actually does with the
untagged create that started all this.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from condor.agents import deeds
from condor.agents.risk import RiskEngine, RiskLimits, RiskState
from condor.runtime import SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import confirmations as confirmations_module
from condor.runtime import sessions as session_module
from condor.runtime.confirmations import ConfirmationRegistry, build_permission_callback
from condor.runtime.context import conversation_attribution

USER = 4242


class _FakeClient:
    """Captures the opening context without spawning a subprocess."""

    last: "_FakeClient | None" = None

    def __init__(self, **kwargs):
        self.alive = True
        self.kwargs = kwargs
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        self.prompts.append(text)
        return ""


@pytest.fixture
def registry(monkeypatch, isolated_conversation_root):
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _FakeClient)
    monkeypatch.setattr("condor.acp.pydantic_ai_client.PydanticAIClient", _FakeClient)
    # The coordinator's own context is not what this file is about; blanking it
    # leaves the attribution block as the whole of the opening turn.
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    _FakeClient.last = None
    return session_module


def _open_chat(**kwargs) -> tuple[str, str]:
    """Start a session and return its conversation id and opening context."""
    spec = dict(
        key=str(SessionKey.telegram(USER)),
        agent_key="claude-code",
        chat_id=USER,
        user_id=USER,
    )
    spec.update(kwargs)

    async def scenario():
        info = await runtime.create_session(SessionSpec(**spec))
        return info.conversation_id

    conversation_id = asyncio.run(scenario())
    return conversation_id, "\n\n".join(_FakeClient.last.prompts)


# ── The tag reaches the chat ──


def test_a_conversation_is_told_the_tag_its_positions_carry(registry):
    """The instruction a chat never used to get, and the exact string it needs."""
    conversation_id, opening = _open_chat()

    tag = f"condor.chat_{conversation_id}"
    assert tag == deeds.attribution_tag(deeds.for_conversation(USER, conversation_id))
    assert f'controller_id="{tag}"' in opening
    assert "create_*_executor" in opening


def test_the_instruction_says_conversation_and_never_session(registry):
    """The loop's line names a session; for a chat that would name nothing real.

    A model told it belongs to a "session" has no session id to reach for, and
    the failure mode of a plausible-sounding instruction is an invented tag —
    which is worse than the empty one, because it looks attributed.
    """
    conversation_id, opening = _open_chat()

    block = conversation_attribution(f"condor.chat_{conversation_id}")
    assert block in opening, "the block under test is not the one that shipped"
    assert "this conversation" in block
    assert "session" not in block.lower()


def test_a_bound_specialist_is_told_its_own_tag(registry, tmp_path, monkeypatch):
    """The branch that skips ``build_initial_context`` entirely (CORR-272).

    A specialist opens with its own identity instead of the chat's, so an
    instruction added to the coordinator's builder would reach one brain and not
    the other — and the two would disagree about what this conversation's
    positions are called. Appending past the fork is what makes that
    impossible.
    """
    from condor.agents import agent as agent_module
    from condor.agents.agent import AgentStore

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    agent = AgentStore().create(
        name="Brigado",
        description="Domain agent",
        instructions="You are Brigado.",
        agent_key="claude-code",
        server_required=False,
    )

    conversation_id, opening = _open_chat(agent_key="", agent_slug=agent.slug)

    assert f'controller_id="brigado.chat_{conversation_id}"' in opening


def test_a_run_with_no_conversation_is_told_nothing(registry):
    """Silence beats an instruction to pass a tag that does not exist.

    A model told to tag will tag with *something*, so a half-built instruction
    is how a fabricated ``controller_id`` gets into the fleet.
    """
    assert conversation_attribution("") == ""


# ── Which gate is actually on the chat path ──

OPTIONS = [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny"}]
UNTAGGED_CREATE = {
    "tool": "create_position_executor",
    "input": {"connector_name": "binance", "trading_pair": "SOL-USDC", "amount": 10},
}


def test_the_risk_gate_is_installed_on_the_loop_seat_and_nowhere_else():
    """The finding this item stopped on, pinned so it cannot go unread again.

    ``auto_approve_with_risk_check`` refuses a create with no ``controller_id``.
    If it ran for chat, an untagged chat create would be *cancelled* — a louder
    and quite different bug from the reporting gap that was reported. It has one
    production install site, and it is the unattended tick seat.

    Asserted over the source rather than by driving a session, because the claim
    is about where the callback is *wired*: a second install site added later is
    exactly what this must catch, and it would catch no behavioural test aimed
    at the seat we already know about.
    """
    root = Path(__file__).resolve().parents[2] / "condor"
    installs = set()
    for path in root.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            call = "auto_approve_with_risk_check(" in line
            if call and not line.lstrip().startswith("def "):
                installs.add(path.relative_to(root).as_posix())

    assert installs == {"agents/engine.py"}


def _drive(callback, tool_call: dict, answer: bool | None = True) -> dict:
    """Run one tool call through a permission callback, answering as a human."""

    class _Channel:
        def __init__(self):
            self.delivered = []

        async def deliver(self, pending):
            self.delivered.append(pending)
            if answer is not None:
                await confirmations_module._registry.resolve(
                    pending.id, approved=answer, by_user_id=USER
                )

    channel = _Channel()

    async def run():
        confirmations_module._registry = ConfirmationRegistry()
        try:
            return await callback(channel), channel
        finally:
            confirmations_module._registry = ConfirmationRegistry()

    return asyncio.run(run())


def test_the_chat_asks_a_human_about_an_untagged_create_it_does_not_refuse_it():
    """What the chat path really does with the call at the heart of this item.

    Not "blocked for want of a tag" — a person is shown it and it runs if they
    say yes. That is why untagged chat executors exist in the fleet at all
    rather than never having been created, and it is the branch of the open
    question this item resolved.
    """

    async def callback(channel):
        cb = build_permission_callback(
            "tg:4242", USER, channels=[channel], timeout_seconds=5
        )
        return await cb(UNTAGGED_CREATE, OPTIONS)

    result, channel = _drive(callback, UNTAGGED_CREATE, answer=True)

    assert len(channel.delivered) == 1, "nobody was asked"
    assert result["outcome"]["outcome"] != "cancelled"


def test_the_loop_would_have_refused_the_very_same_call():
    """The contrast that makes the test above a statement and not a shrug.

    Same untagged create, the other seat: the risk gate cancels it before any
    human sees it. The two callbacks genuinely differ here, so which one a
    surface installs is a real decision and not a detail.
    """
    from condor.agents.risk import auto_approve_with_risk_check

    async def run():
        cb = auto_approve_with_risk_check(
            RiskEngine(RiskLimits()), RiskState(), execution_mode="loop"
        )
        return await cb(UNTAGGED_CREATE, OPTIONS)

    assert asyncio.run(run())["outcome"]["outcome"] == "cancelled"
