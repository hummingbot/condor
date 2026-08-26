"""The knowledge panel can write, not only read.

The panel behind a conversation showed an Agent's AGENT.md, playbooks and
memories and could change none of it — ``SkillStore`` and ``MemoryStore`` had
full CRUD and the web layer exposed only the reads, so editing meant leaving for
``/agents/{slug}`` and a separate modal that covered AGENT.md alone.

These cover the five write routes that closed that gap, and in particular the
two rules they must not have loosened on the way through: an inherited shared
playbook stays read-only, and a memory belongs to one ``(agent, user)`` pair.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents import agent as agent_module
from condor.agents.agent import AgentStore
from condor.memory import MemoryStore, SkillStore
from condor.memory.paths import shared_skills_root
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")
OTHER = WebUser(id=999, username="v", first_name="V", role="user")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    AgentStore().create(name="Brigado", description="BRL market making")
    return tmp_path


def _client(user: WebUser = USER) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── Playbooks ──


def test_a_playbook_can_be_created_read_back_and_deleted(env):
    client = _client()

    created = client.post(
        "/agents/brigado/skills",
        json={
            "name": "Rebalance the book",
            "description": "How to re-center quotes",
            "when_to_use": "When the mid has drifted past the band",
            "body": "1. Read the book\n2. Cancel\n3. Requote",
        },
    )
    assert created.status_code == 200, created.text
    slug = created.json()["name"]

    listed = client.get("/agents/brigado/brain").json()["skills"]
    assert [s["slug"] for s in listed] == [slug]

    body = client.get(f"/agents/brigado/skills/{slug}").json()
    assert "Requote" in body["body"]
    assert body["when_to_use"] == "When the mid has drifted past the band"

    assert client.delete(f"/agents/brigado/skills/{slug}").status_code == 200
    assert client.get("/agents/brigado/brain").json()["skills"] == []


def test_an_edit_patches_only_what_it_sends(env):
    """The panel saves one field at a time; the rest must survive it."""
    client = _client()
    client.post(
        "/agents/brigado/skills",
        json={
            "name": "Rebalance",
            "description": "original description",
            "when_to_use": "original trigger",
            "body": "original body",
        },
    )

    res = client.put("/agents/brigado/skills/rebalance", json={"body": "new body"})

    assert res.status_code == 200, res.text
    after = client.get("/agents/brigado/skills/rebalance").json()
    assert after["body"] == "new body"
    assert after["description"] == "original description"
    assert after["when_to_use"] == "original trigger"


def test_a_routine_link_is_cleared_by_an_empty_string_and_kept_by_omission(env):
    client = _client()
    client.post(
        "/agents/brigado/skills",
        json={
            "name": "Rebalance",
            "description": "d",
            "when_to_use": "w",
            "body": "b",
            "references_routine": "lp_rebalance",
        },
    )

    # Omitted: the link stands.
    client.put("/agents/brigado/skills/rebalance", json={"body": "b2"})
    assert (
        client.get("/agents/brigado/skills/rebalance").json()["references_routine"]
        == "lp_rebalance"
    )

    # Sent empty: the link goes.
    client.put("/agents/brigado/skills/rebalance", json={"references_routine": ""})
    assert (
        client.get("/agents/brigado/skills/rebalance").json()["references_routine"]
        == ""
    )


def test_creating_without_the_required_fields_is_a_400_not_a_half_written_playbook(env):
    res = _client().post("/agents/brigado/skills", json={"name": "Half"})

    assert res.status_code == 400
    assert "required" in res.json()["detail"]
    assert _client().get("/agents/brigado/brain").json()["skills"] == []


def test_an_inherited_shared_playbook_is_read_only_through_the_web_too(env):
    """The store refuses; the route must surface that rather than swallow it."""
    shared = shared_skills_root() / "house_rules"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text(
        "---\nname: house_rules\ndescription: d\nwhen_to_use: w\n---\n\nBody.\n"
    )

    client = _client()
    card = next(
        s
        for s in client.get("/agents/brigado/brain").json()["skills"]
        if s["slug"] == "house_rules"
    )
    assert card["inherited"] is True, "the panel needs this to hide its edit buttons"

    edited = client.put("/agents/brigado/skills/house_rules", json={"body": "mine"})
    deleted = client.delete("/agents/brigado/skills/house_rules")

    assert edited.status_code == 400
    assert deleted.status_code == 400
    assert (shared / "SKILL.md").read_text().endswith("Body.\n"), "untouched on disk"


def test_condor_may_edit_the_shared_library_it_owns(env):
    """The refusal above is about inheriting it, not about the library itself."""
    # `condor` is reserved, so `AgentStore.create` refuses it — the default
    # agent is a directory on disk like any other, just never created by name.
    (env / "condor").mkdir()
    (env / "condor" / "AGENT.md").write_text("---\nname: Condor\n---\n\nBody.\n")
    SkillStore(None).create(
        name="House rules", description="d", when_to_use="w", body="Body.", shared=True
    )

    res = _client().put("/agents/condor/skills/house_rules", json={"body": "Revised."})

    assert res.status_code == 200, res.text
    assert res.json()["body"] == "Revised."


def test_deleting_a_playbook_that_never_existed_is_a_404(env):
    assert _client().delete("/agents/brigado/skills/nope").status_code == 404


# ── Memories ──


def test_a_memory_round_trips_and_is_scoped_to_its_writer(env):
    res = _client().put(
        "/agents/brigado/memories/favourite_pair",
        json={
            "content": "SOL-USDC, always.",
            "description": "The pair to assume when none is named",
            "type": "preference",
        },
    )
    assert res.status_code == 200, res.text

    mine = _client().get("/agents/brigado/brain").json()["memories"]
    assert [(m["name"], m["type"]) for m in mine] == [("favourite_pair", "preference")]
    assert (
        _client().get("/agents/brigado/memories/favourite_pair").json()["body"]
        == "SOL-USDC, always."
    )

    # Same agent, different user: memory is keyed on the pair, so this is empty.
    assert _client(OTHER).get("/agents/brigado/brain").json()["memories"] == []
    assert MemoryStore(OTHER.id, "brigado").read("favourite_pair") is None


def test_writing_the_same_name_overwrites_rather_than_duplicating(env):
    client = _client()
    client.put(
        "/agents/brigado/memories/favourite_pair",
        json={"content": "SOL-USDC", "description": "d", "type": "preference"},
    )
    client.put(
        "/agents/brigado/memories/favourite_pair",
        json={"content": "BTC-USDT", "description": "d2", "type": "preference"},
    )

    memories = client.get("/agents/brigado/brain").json()["memories"]
    assert len(memories) == 1
    assert memories[0]["description"] == "d2"
    assert (
        client.get("/agents/brigado/memories/favourite_pair").json()["body"]
        == "BTC-USDT"
    )


def test_a_memory_can_be_forgotten(env):
    client = _client()
    client.put(
        "/agents/brigado/memories/favourite_pair",
        json={"content": "SOL-USDC", "description": "d"},
    )

    assert client.delete("/agents/brigado/memories/favourite_pair").status_code == 200
    assert client.get("/agents/brigado/brain").json()["memories"] == []
    assert client.delete("/agents/brigado/memories/favourite_pair").status_code == 404


def test_an_empty_memory_is_refused(env):
    res = _client().put(
        "/agents/brigado/memories/blank", json={"content": "", "description": "d"}
    )

    assert res.status_code == 400
    assert _client().get("/agents/brigado/brain").json()["memories"] == []


def test_the_writes_are_not_shadowed_by_the_slug_catch_alls(env):
    """``/{slug}`` and ``/{slug}/{name}`` sit in the same router (CORR-061)."""
    client = _client()

    assert client.post("/agents/brigado/skills", json={"name": "x"}).status_code == 400
    assert client.put("/agents/brigado/skills/x", json={}).status_code == 400
    # A bare `PUT /agents/{slug}` is still AGENT.md, not a skill write.
    assert (
        client.put("/agents/brigado", json={"content": "# Brigado"}).status_code == 200
    )
    assert AgentStore().get("brigado").instructions.strip() == "# Brigado"


def test_deleting_the_default_agent_is_a_refusal_not_a_crash(env):
    """Condor's page is a normal destination now, so its route must answer well."""
    (env / "condor").mkdir()
    (env / "condor" / "AGENT.md").write_text("---\nname: Condor\n---\n\nBody.\n")

    res = _client().delete("/agents/condor")

    assert res.status_code == 400
    assert "default agent" in res.json()["detail"]
    assert (env / "condor" / "AGENT.md").exists()
