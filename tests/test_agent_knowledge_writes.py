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
from condor.memory import MemoryStore, SkillStore, proposals
from condor.memory.paths import shared_skills_root
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")
OTHER = WebUser(id=999, username="v", first_name="V", role="user")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
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


# ── Proposed playbooks (FEAT-074) ──


def _propose(name: str = "CLMM rebalance") -> None:
    proposals.put(
        "brigado",
        name=name,
        description="Re-centre a CLMM position when price leaves the range",
        when_to_use="The user asks to check or rebalance an LP range",
        body="1. Pull the pool state\n2. Compare to the position bounds",
        conversation_id="8f2c1a4b90de",
    )


def test_a_pending_proposal_rides_along_with_the_brain(env):
    client = _client()
    assert client.get("/agents/brigado/brain").json()["skill_proposal"] is None

    _propose()

    brain = client.get("/agents/brigado/brain").json()
    assert brain["skill_proposal"]["name"] == "clmm_rebalance"
    assert brain["skill_proposal"]["from_conversation"] == "8f2c1a4b90de"
    assert "Pull the pool state" in brain["skill_proposal"]["body"]
    # Offered, not owned: it is not in the library and not in the count.
    assert brain["skills"] == []


def test_accepting_a_proposal_puts_it_in_the_library_and_takes_the_card_away(env):
    client = _client()
    _propose()

    res = client.post("/agents/brigado/skill-proposals/accept")

    assert res.status_code == 200, res.text
    assert res.json() == {"accepted": True, "name": "clmm_rebalance"}
    brain = client.get("/agents/brigado/brain").json()
    assert [s["slug"] for s in brain["skills"]] == ["clmm_rebalance"]
    assert brain["skill_proposal"] is None
    # An ordinary playbook from here on — readable, editable, deletable.
    assert (
        "Pull the pool state"
        in client.get("/agents/brigado/skills/clmm_rebalance").json()["body"]
    )
    assert client.delete("/agents/brigado/skills/clmm_rebalance").status_code == 200


def test_discarding_a_proposal_leaves_the_library_untouched(env):
    client = _client()
    client.post(
        "/agents/brigado/skills",
        json={"name": "Kept", "description": "d", "when_to_use": "w", "body": "b"},
    )
    _propose()

    assert client.delete("/agents/brigado/skill-proposals").status_code == 200

    brain = client.get("/agents/brigado/brain").json()
    assert brain["skill_proposal"] is None
    assert [s["slug"] for s in brain["skills"]] == ["kept"]


def test_ruling_on_a_proposal_that_is_not_there_says_so(env):
    client = _client()

    assert client.delete("/agents/brigado/skill-proposals").status_code == 404
    assert client.post("/agents/brigado/skill-proposals/accept").status_code == 400


# ── Controllers (FEAT-127) ──

_MM_SOURCE = '''"""Pure maker around mid."""
from hummingbot.strategy_v2.controllers.market_making_controller_base import (
    MarketMakingControllerBase,
)


class PmmKing(MarketMakingControllerBase):
    pass
'''


def _controller(base, name, source=_MM_SOURCE, samples=None):
    d = base / "controllers" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(source)
    for stem, text in (samples or {}).items():
        (d / "sample_configs").mkdir(exist_ok=True)
        (d / "sample_configs" / f"{stem}.yml").write_text(text)
    return d


def test_the_brain_lists_own_and_shared_controllers(env):
    _controller(env / "brigado", "pmm_king", samples={"tight": "spread: 0.1\n"})
    _controller(env / "_shared", "grid_basic")
    _controller(env / "brigado", "mystery", source="class Mystery:\n    pass\n")

    cards = {
        c["name"]: c
        for c in _client().get("/agents/brigado/brain").json()["controllers"]
    }

    assert set(cards) == {"pmm_king", "grid_basic", "mystery"}
    assert cards["pmm_king"]["controller_type"] == "market_making"
    assert cards["pmm_king"]["styles"] == ["tight"]
    assert cards["pmm_king"]["description"] == "Pure maker around mid."
    assert cards["pmm_king"]["shared"] is False
    assert cards["grid_basic"]["shared"] is True
    assert cards["mystery"]["controller_type"] is None
    assert "cannot be inferred" in cards["mystery"]["type_error"]


def test_an_agent_without_controllers_has_none(env):
    assert _client().get("/agents/brigado/brain").json()["controllers"] == []


def test_a_broken_controller_library_does_not_break_the_brain(env, monkeypatch):
    import condor.agent_controllers as ac

    def boom(slug):
        raise OSError("disk gone")

    monkeypatch.setattr(ac, "agent_controllers", boom)
    brain = _client().get("/agents/brigado/brain")
    assert brain.status_code == 200
    assert brain.json()["controllers"] == []
    assert brain.json()["name"] == "Brigado"


def test_the_brain_never_asks_a_server_about_controllers(env, monkeypatch):
    import condor.agent_controllers_sync as sync

    _controller(env / "brigado", "pmm_king")

    async def no_network(*a, **k):
        raise AssertionError("the brain must not reach a server")

    monkeypatch.setattr(sync, "controller_statuses", no_network)
    monkeypatch.setattr(sync, "controller_status", no_network)
    assert len(_client().get("/agents/brigado/brain").json()["controllers"]) == 1


def test_a_controllers_source_and_styles_read_back(env):
    _controller(
        env / "brigado",
        "pmm_king",
        source=_MM_SOURCE.replace("\n", "\r\n"),
        samples={"tight": "spread: 0.1\n", "wide": "spread: 0.5\n"},
    )
    client = _client()

    body = client.get("/agents/brigado/controllers/pmm_king/source").json()
    assert body["controller_type"] == "market_making"
    assert body["styles"] == ["tight", "wide"]
    # Normalised — exactly what a sync would upload.
    assert "\r" not in body["source"] and "class PmmKing" in body["source"]

    sample = client.get("/agents/brigado/controllers/pmm_king/configs/wide").json()
    assert sample == {"name": "pmm_king", "sample": "wide", "yaml": "spread: 0.5\n"}


def test_controller_reads_are_confined_to_the_resolved_folder(env):
    _controller(env / "brigado", "pmm_king", samples={"tight": "spread: 0.1\n"})
    (env / "brigado" / "AGENT.md").write_text("secret")
    client = _client()

    assert client.get("/agents/brigado/controllers/nope/source").status_code == 404
    assert (
        client.get("/agents/brigado/controllers/pmm_king/configs/nope").status_code
        == 404
    )
    for bad in ("..%2F..%2FAGENT", "..", "%2E%2E"):
        r = client.get(f"/agents/brigado/controllers/pmm_king/configs/{bad}")
        assert r.status_code == 404
        assert "secret" not in r.text
    assert client.get("/agents/nobody/controllers/pmm_king/source").status_code == 404
