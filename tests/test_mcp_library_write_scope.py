"""A launched agent writes only its own library (MC-07).

``manage_skill``, ``manage_routines`` and ``manage_agent_controllers`` all take
``agent=``. Only the controllers tool checked who was asking, so a specialist
seat could create, edit or delete a peer's playbooks and routine code — code
that later runs in the main process under the peer's name — and Condor's own
library with ``agent="condor"``. The rule now lives once, in ``tools/_scope.py``.

Pinned: the three write refusals, that nothing on disk moved, that the documented
cross-agent *reads* still work, and that the chat can still target any agent.
"""

import asyncio

import pytest

from condor.agents.agent import AgentStore
from condor.memory import SkillStore
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import _scope, routines, skills

ROUTINE = '''"""A routine."""
from pydantic import BaseModel


class Config(BaseModel):
    """Does a thing"""


async def run(config, context):
    return "ok"
'''


@pytest.fixture
def two_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    AgentStore().create(name="Alpha", description="d", instructions="i")
    AgentStore().create(name="Beta", description="d", instructions="i")
    # Beta's library, written from the chat seat, which may target anyone.
    monkeypatch.setattr(settings, "agent_slug", "condor")
    made = asyncio.run(
        skills.manage_skill(
            "create",
            name="betas_playbook",
            description="d",
            when_to_use="w",
            body="beta's own steps",
            agent="beta",
        )
    )
    assert "error" not in made, made
    made = routines.create_routine("betas_scan", ROUTINE, "beta")
    assert made.get("created"), made
    return tmp_path


def _seat(monkeypatch, slug):
    monkeypatch.setattr(settings, "agent_slug", slug)


def _skill(**kw):
    return asyncio.run(skills.manage_skill(**kw))


REFUSAL = "an agent manages only its own"


# ── skills ──


@pytest.mark.parametrize(
    "call",
    [
        dict(action="edit", name="betas_playbook", body="rewritten by alpha"),
        dict(action="delete", name="betas_playbook"),
        dict(action="write_file", name="betas_playbook", file="x.yml", content="a"),
        dict(
            action="create", name="planted", description="d", when_to_use="w", body="b"
        ),
    ],
)
def test_a_specialist_cannot_write_a_peers_skills(two_agents, monkeypatch, call):
    _seat(monkeypatch, "alpha")

    result = _skill(agent="beta", **call)

    assert REFUSAL in result["error"]
    store = SkillStore("beta")
    assert store.read("betas_playbook")["body"].strip() == "beta's own steps"
    assert store.read("planted") is None


def test_a_specialist_cannot_write_condors_own_skills(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")

    result = _skill(
        action="create",
        name="planted",
        description="d",
        when_to_use="w",
        body="b",
        agent="condor",
    )

    assert REFUSAL in result["error"]


def test_a_specialist_can_still_read_a_peers_skills(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")

    assert _skill(action="read", name="betas_playbook", agent="beta")[
        "body"
    ].strip() == ("beta's own steps")
    assert "error" not in _skill(action="list", agent="beta")


def test_a_specialist_still_writes_its_own_skills(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")

    for target in (None, "alpha"):
        made = _skill(
            action="create",
            name=f"mine_{'named' if target else 'bare'}",
            description="d",
            when_to_use="w",
            body="b",
            agent=target,
        )
        assert "error" not in made, made


def test_the_chat_still_targets_any_agent(two_agents, monkeypatch):
    _seat(monkeypatch, "condor")

    edited = _skill(
        action="edit", name="betas_playbook", body="chat edit", agent="beta"
    )

    assert "error" not in edited, edited
    assert SkillStore("beta").read("betas_playbook")["body"].strip() == "chat edit"


def test_an_unknown_target_keeps_its_own_error(two_agents, monkeypatch):
    _seat(monkeypatch, "condor")

    assert _skill(action="list", agent="nobody") == {
        "error": "No agent or loop found for 'nobody'"
    }


# ── routines ──


def _betas_file():
    from routines.base import assistant_routines_dir

    return assistant_routines_dir("beta") / "betas_scan.py"


def test_a_specialist_cannot_write_a_peers_routines(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")
    before = _betas_file().read_text()

    edited = routines.edit_routine("betas_scan", ROUTINE + "# alpha was here\n", "beta")
    deleted = routines.delete_routine("betas_scan", "beta")
    created = routines.create_routine("planted", ROUTINE, "beta")

    for result in (edited, deleted, created):
        assert REFUSAL in result["error"]
    assert _betas_file().read_text() == before
    assert not (_betas_file().parent / "planted.py").exists()


def test_a_specialist_can_still_read_a_peers_routine(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")

    assert routines.read_routine("betas_scan", "beta")["code"] == ROUTINE


def test_a_specialist_still_writes_its_own_routines(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")

    assert routines.create_routine("mine", ROUTINE, None).get("created")
    assert routines.edit_routine("mine", ROUTINE + "# v2\n", "alpha").get("updated")
    assert routines.delete_routine("mine", None) == {"deleted": True, "name": "mine"}


def test_the_chat_still_edits_an_agents_routine(two_agents, monkeypatch):
    _seat(monkeypatch, "condor")

    assert routines.edit_routine("betas_scan", ROUTINE + "# chat\n", "beta").get(
        "updated"
    )


# ── the rule itself ──


def test_the_rule_in_one_place(two_agents, monkeypatch):
    _seat(monkeypatch, "alpha")

    assert _scope.resolve_target(None, write=True, what="x") == "alpha"
    assert _scope.resolve_target("alpha", write=True, what="x") == "alpha"
    assert _scope.resolve_target("beta", write=False, what="x") == "beta"
    with pytest.raises(_scope.ScopeError, match="only its own x"):
        _scope.resolve_target("beta", write=True, what="x")
    # A write aimed at nothing that exists is refused the same way, so the
    # answer does not say which libraries exist.
    with pytest.raises(_scope.ScopeError, match="only its own x"):
        _scope.resolve_target("nobody", write=True, what="x")
    with pytest.raises(_scope.UnknownAgent):
        _scope.resolve_target("nobody", write=False, what="x")


def test_the_controllers_tool_uses_the_same_rule(two_agents, monkeypatch):
    from mcp_servers.condor.tools import agent_controllers

    _seat(monkeypatch, "alpha")
    result = asyncio.run(
        agent_controllers.manage_agent_controllers("list", agent="beta")
    )
    assert result == {
        "error": "an agent manages only its own controllers — omit `agent`"
    }

    _seat(monkeypatch, "condor")
    assert (
        asyncio.run(agent_controllers.manage_agent_controllers("list", agent="beta"))[
            "agent"
        ]
        == "beta"
    )
    assert asyncio.run(
        agent_controllers.manage_agent_controllers("list", agent="nobody")
    ) == {"error": "no agent named 'nobody'"}
