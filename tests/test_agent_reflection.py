"""The reflection pass (FEAT-061).

The pass spends the agent's own model on a chat the user may never return to,
and it writes into their memory without anyone reading the answer first. So the
tests that matter are the ones that pin the guards rather than the happy path:
that a conversation is attempted **once** whatever happened, that a garbage
answer writes nothing at all, that the caps are enforced in Python rather than
requested in the prompt, and that the run builds **no MCP servers** — which is
what makes "this pass cannot use tools" a property instead of an intention.

Every run here is against a stub client. Nothing in this file starts a
subprocess or talks to a model.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from condor.agents import agent as agent_module
from condor.agents import reflection, starters
from condor.agents.agent import AgentStore
from condor.memory import MemoryStore
from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry

USER = 4242
SLUG = "brigado"

GOOD = """Here is what I found:

```json
{
  "intents": [
    {"label": "Rebalance my SOL-USDC range",
     "hint": "Check the position and re-centre it",
     "icon": "lp", "skill": "clmm-rebalance"}
  ],
  "memories": [
    {"name": "prefers-tight-ranges",
     "description": "Wants CLMM ranges centred tight",
     "type": "preference",
     "content": "Asks for narrow ranges and re-centres early."}
  ]
}
```
"""


class StubClient:
    """A model that answers with whatever the test handed it.

    Records the kwargs it was built with, so the "no MCP servers" claim is
    asserted against the call rather than trusted.
    """

    instances: list = []
    built: dict = {}
    answer: str = GOOD
    raises: bool = False

    def __init__(self, **kwargs):
        type(self).built = kwargs
        type(self).instances.append(self)
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def prompt(self, text):
        type(self).prompt_text = text
        if self.raises:
            raise RuntimeError("the model fell over")
        return self.answer

    async def stop(self):
        self.stopped = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    """One agent on disk, with the shipped reflect.md beside it."""
    root = tmp_path / "agents"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_module, "_DATA_ROOT", root)
    AgentStore().create(name="Brigado", description="BRL MM", agent_key="claude-code")

    defaults = root / "_defaults"
    defaults.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        Path(__file__).parent.parent / "agents" / "_defaults" / "reflect.md",
        defaults / "reflect.md",
    )
    return root


@pytest.fixture
def stub(monkeypatch):
    StubClient.answer = GOOD
    StubClient.raises = False
    StubClient.built = {}
    StubClient.instances = []

    def _build(agent_key, **kwargs):
        return StubClient(agent_key=agent_key, **kwargs)

    monkeypatch.setattr("condor.runtime.llm_client.build_llm_client", _build)
    return StubClient


def _conversation(*, turns: int = 2, agent_slug: str = SLUG):
    meta = conversations.new_conversation(USER, surface="web", agent_slug=agent_slug)
    for i in range(turns):
        conversations.append_turn(
            USER,
            meta.id,
            TurnEntry(
                role="user" if i % 2 == 0 else "assistant",
                text=f"rebalance the SOL-USDC range please ({i})",
            ),
        )
    return conversations.get_conversation(USER, meta.id)


def _meta(conv_id: str):
    return conversations.get_conversation(USER, conv_id)


# ── The prompt ──


def test_the_shipped_default_is_the_policy_when_the_agent_has_none(env):
    body = reflection.load_policy(SLUG)

    assert "Intents" in body and "Memories" in body
    # Frontmatter, if an override ever carries any, is not part of the prompt.
    assert not body.startswith("---")


def test_an_agent_can_override_how_it_reflects(env):
    (env / SLUG / "reflect.md").write_text("Only ever learn about XRP.", "utf-8")

    assert reflection.load_policy(SLUG) == "Only ever learn about XRP."


def test_the_prompt_carries_the_transcript_the_memories_and_the_known_slugs(env):
    prompt = reflection.build_prompt(
        "POLICY", "TRANSCRIPT", "MEMORY INDEX", ["rebalance_my_range"]
    )

    assert prompt.startswith("POLICY")
    for expected in ("TRANSCRIPT", "MEMORY INDEX", "rebalance_my_range", "lp"):
        assert expected in prompt


# ── Parsing ──


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"intents": []}\n```',
        '```\n{"intents": []}\n```',
        'Sure! {"intents": []} — hope that helps',
        '{"intents": []}',
    ],
)
def test_the_parser_finds_the_object_however_it_was_wrapped(text):
    assert reflection.parse_answer(text) == {"intents": []}


def test_a_brace_inside_a_label_does_not_end_the_object_early():
    data = reflection.parse_answer('{"intents": [{"label": "a } b"}]}')

    assert data["intents"][0]["label"] == "a } b"


@pytest.mark.parametrize("text", ["", "no json here at all", "{not json", "[1, 2]"])
def test_an_unparseable_answer_is_none(text):
    assert reflection.parse_answer(text) is None


# ── The pass ──


@pytest.mark.asyncio
async def test_a_good_answer_writes_a_memory_and_merges_an_intent(env, stub):
    meta = _conversation()

    assert await reflection.reflect(USER, meta) is True

    [entry] = starters.read(USER, SLUG)
    assert entry.label == "Rebalance my SOL-USDC range"
    assert entry.count == 1
    assert entry.icon == "lp"

    [memory] = MemoryStore(USER, SLUG).catalog()
    assert memory["name"] == "prefers_tight_ranges"
    assert memory["source"] == reflection.SOURCE


@pytest.mark.asyncio
async def test_the_run_builds_no_mcp_servers(env, stub):
    await reflection.reflect(USER, _conversation())

    assert stub.built["mcp_servers"] is None
    assert "permission_callback" not in stub.built
    assert stub.built["agent_key"] == "claude-code"


@pytest.mark.asyncio
async def test_the_conversations_own_model_answers_for_it(env, stub):
    meta = _conversation()
    conversations.update_meta(USER, meta.id, agent_key="gpt-4o")

    await reflection.reflect(USER, _meta(meta.id))

    assert stub.built["agent_key"] == "gpt-4o"


@pytest.mark.asyncio
async def test_a_reflected_conversation_is_stamped_and_flagged(env, stub):
    meta = _conversation()

    await reflection.reflect(USER, meta)

    after = _meta(meta.id)
    assert after.reflected_at is not None
    assert after.reflected_ok is True


@pytest.mark.asyncio
async def test_an_unparseable_answer_writes_nothing_but_still_stamps(env, stub):
    stub.answer = "I would rather not."
    meta = _conversation()

    assert await reflection.reflect(USER, meta) is False

    assert starters.read(USER, SLUG) == []
    assert MemoryStore(USER, SLUG).catalog() == []
    after = _meta(meta.id)
    assert after.reflected_at is not None
    assert after.reflected_ok is False


@pytest.mark.asyncio
async def test_a_model_that_raises_still_stamps_the_conversation(env, stub):
    stub.raises = True
    meta = _conversation()

    assert await reflection.reflect(USER, meta) is False
    assert _meta(meta.id).reflected_at is not None


@pytest.mark.asyncio
async def test_an_unknown_agent_still_stamps_the_conversation(env, stub):
    meta = _conversation(agent_slug="nobody_here")

    assert await reflection.reflect(USER, meta) is False
    assert _meta(meta.id).reflected_at is not None
    assert stub.built == {}  # no client was ever built


@pytest.mark.asyncio
async def test_the_client_is_stopped_even_when_the_answer_is_garbage(env, stub):
    stub.answer = "nope"

    await reflection.reflect(USER, _conversation())

    assert [c.stopped for c in stub.instances] == [True]


@pytest.mark.asyncio
async def test_the_caps_hold_against_a_greedy_answer(env, stub):
    stub.answer = json.dumps(
        {
            "intents": [{"label": f"Intent {i}"} for i in range(20)],
            "memories": [
                {
                    "name": f"fact-{i}",
                    "description": f"d{i}",
                    "content": f"c{i}",
                    "type": "fact",
                }
                for i in range(20)
            ],
        }
    )

    await reflection.reflect(USER, _conversation())

    assert len(starters.read(USER, SLUG)) == reflection.MAX_INTENTS
    assert len(MemoryStore(USER, SLUG).catalog()) == reflection.MAX_MEMORIES


@pytest.mark.asyncio
async def test_a_half_filled_memory_is_refused_without_taking_the_pass_down(env, stub):
    stub.answer = json.dumps(
        {"intents": [{"label": "Rebalance"}], "memories": [{"name": "no-body"}]}
    )

    assert await reflection.reflect(USER, _conversation()) is True

    assert MemoryStore(USER, SLUG).catalog() == []
    assert [e.label for e in starters.read(USER, SLUG)] == ["Rebalance"]


@pytest.mark.asyncio
async def test_the_same_intent_twice_is_counted_not_duplicated(env, stub):
    await reflection.reflect(USER, _conversation())
    await reflection.reflect(USER, _conversation())

    [entry] = starters.read(USER, SLUG)
    assert entry.count == 2
    assert entry.score > 1.0
