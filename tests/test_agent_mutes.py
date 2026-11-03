"""FEAT-090: a muted playbook or routine never reaches the agent.

Four things are asserted here, in the order the feature builds them:

1. the mute file itself — absent means nothing muted, pruning removes it again,
   and a falsy slug resolves the chat's own ``agents/condor/mutes.yml``;
2. the skill filter — a muted playbook is out of the index, out of a search, out
   of ``read()``, still editable, and muted for **one** agent only;
3. the routine filter — out of ``assistant_routines`` (and therefore out of the
   prompt section and out of ``_resolve_routine``) while ``discover_routines``,
   which the human ``/routines`` page reads, is untouched;
4. the panel's route — the operator's view lists what it muted, still opens it,
   and the agent's view does not.
"""

import pytest
import yaml

import condor.memory.paths as paths_mod
import routines.base as base
from condor.memory import skills as skills_module
from condor.memory.mutes import is_muted, load_mutes, mutes_path, set_muted
from condor.memory.skills import SkillStore
from routines.base import assistant_routines, assistant_routines_dir, discover_routines

ROUTINE_TEMPLATE = '''
from pydantic import BaseModel


class Config(BaseModel):
    """{desc}"""
    value: int = 1


async def run(config, context):
    return "ok"
'''


@pytest.fixture
def project_root(tmp_path):
    """The tmp root the suite-wide fixture already points ``agents/`` under."""
    return tmp_path


@pytest.fixture
def no_routines(monkeypatch):
    """No routine reference resolves, so ``routine_ok`` never touches disk."""
    monkeypatch.setattr(
        skills_module, "_routine_exists", lambda name, agent_slug=None: False
    )


def _write_skill(root, agent_slug, slug, *, shared=False, when_to_use="whenever"):
    base_dir = (
        root / "agents" / "_shared" / "skills"
        if shared
        else root / "agents" / (agent_slug or "condor") / "skills"
    )
    d = base_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: d\nwhen_to_use: {when_to_use}\n"
        "source: builtin\n---\n\nSteps.\n"
    )
    return d


def _write_routine(dir_path, name, desc="a routine"):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{name}.py").write_text(ROUTINE_TEMPLATE.format(desc=desc))


# ── 1. the file ──


def test_absent_file_means_nothing_muted(project_root):
    assert not mutes_path("perps").exists()
    assert load_mutes("perps") == {"skills": set(), "routines": set(), "tools": set()}
    assert is_muted("perps", "skill", "anything") is False


def test_round_trip_and_prune_back_to_no_file(project_root):
    set_muted("perps", "skill", "lp_rebalance", True)
    assert mutes_path("perps").exists()
    assert load_mutes("perps")["skills"] == {"lp_rebalance"}
    assert is_muted("perps", "skill", "lp_rebalance") is True

    set_muted("perps", "routine", "lp_scanner", True)
    on_disk = yaml.safe_load(mutes_path("perps").read_text())
    assert on_disk == {"skills": ["lp_rebalance"], "routines": ["lp_scanner"]}
    # Empty kinds are pruned rather than written as `tools: []`.
    assert "tools" not in on_disk

    set_muted("perps", "skill", "lp_rebalance", False)
    set_muted("perps", "routine", "lp_scanner", False)
    assert not mutes_path("perps").exists()


def test_falsy_slug_is_the_chat_not_no_mutes(project_root):
    """Condor is an ordinary agent (FEAT-033), so ``None`` must land on it."""
    set_muted(None, "skill", "lp_rebalance", True)
    assert mutes_path(None) == project_root / "agents" / "condor" / "mutes.yml"
    assert is_muted("condor", "skill", "lp_rebalance") is True


def test_unknown_kind_is_refused(project_root):
    with pytest.raises(ValueError):
        set_muted("perps", "memories", "x", True)


def test_unreadable_file_reads_as_nothing_muted(project_root):
    """A broken file costs the operator a curation, never the agent a library."""
    path = mutes_path("perps")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("skills: [oops\n")
    assert load_mutes("perps")["skills"] == set()


# ── 2. skills ──


def test_muted_skill_is_gone_from_every_read(project_root, no_routines):
    _write_skill(project_root, "perps", "lp_rebalance")
    _write_skill(project_root, "perps", "funding_carry")
    set_muted("perps", "skill", "lp_rebalance", True)

    store = SkillStore("perps")
    assert "lp_rebalance" not in store.list_index()
    assert "funding_carry" in store.list_index()
    assert [h["name"] for h in store.search("")] == ["funding_carry"]
    assert [r["slug"] for r in store.catalog()] == ["funding_carry"]
    assert store.read("lp_rebalance") is None
    assert store.read("funding_carry") is not None


def test_muted_skills_companion_file_is_unreachable_too(project_root, no_routines):
    d = _write_skill(project_root, "perps", "lp_rebalance")
    (d / "template.md").write_text("body")
    assert SkillStore("perps").read_file("lp_rebalance", "template.md")["content"]

    set_muted("perps", "skill", "lp_rebalance", True)
    assert "error" in SkillStore("perps").read_file("lp_rebalance", "template.md")


def test_operator_view_sees_the_muted_skill_and_says_so(project_root, no_routines):
    _write_skill(project_root, "perps", "lp_rebalance")
    _write_skill(project_root, "perps", "funding_carry")
    set_muted("perps", "skill", "lp_rebalance", True)

    rows = {r["slug"]: r for r in SkillStore("perps", include_muted=True).catalog()}
    assert rows["lp_rebalance"]["muted"] is True
    assert rows["funding_carry"]["muted"] is False
    assert SkillStore("perps", include_muted=True).read("lp_rebalance") is not None


def test_muting_is_reversible_curation_not_a_soft_delete(project_root, no_routines):
    _write_skill(project_root, "perps", "lp_rebalance")
    set_muted("perps", "skill", "lp_rebalance", True)

    # Still editable while muted — that is what makes the switch reversible.
    result = SkillStore("perps").edit("lp_rebalance", description="edited")
    assert "error" not in result

    set_muted("perps", "skill", "lp_rebalance", False)
    read = SkillStore("perps").read("lp_rebalance")
    assert read is not None and read["description"] == "edited"


def test_muting_a_shared_skill_touches_one_agent_only(project_root, no_routines):
    _write_skill(project_root, None, "lp_rebalance", shared=True)
    set_muted("perps", "skill", "lp_rebalance", True)

    assert SkillStore("perps").read("lp_rebalance") is None
    assert SkillStore("spot").read("lp_rebalance") is not None
    assert "lp_rebalance" in SkillStore("spot").list_index()


def test_an_agent_with_no_mutes_behaves_exactly_as_before(project_root, no_routines):
    _write_skill(project_root, "perps", "lp_rebalance")
    assert [r["slug"] for r in SkillStore("perps").catalog()] == ["lp_rebalance"]
    assert not mutes_path("perps").exists()  # nothing written by merely reading


# ── 3. routines ──


@pytest.fixture
def isolated_routines(tmp_path, monkeypatch):
    """Shared root + agent routine dirs under ``tmp_path``, caches emptied."""
    monkeypatch.setattr(base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(base, "_routines_cache", None)
    monkeypatch.setattr(base, "_routines_mtimes", {})
    monkeypatch.setattr(base, "_path_caches", {})
    return tmp_path


def test_muted_routine_is_gone_from_an_agents_scope(isolated_routines, monkeypatch):
    _write_routine(paths_mod.shared_routines_root(), "lp_scanner")
    _write_routine(assistant_routines_dir("perps"), "funding_watch")

    assert set(assistant_routines("perps")) == {"lp_scanner", "funding_watch"}

    set_muted("perps", "routine", "lp_scanner", True)
    assert set(assistant_routines("perps")) == {"funding_watch"}
    # The operator's view still lists it, so the panel can render its switch.
    assert set(assistant_routines("perps", include_muted=True)) == {
        "lp_scanner",
        "funding_watch",
    }


def test_a_muted_routine_no_longer_resolves_for_the_agent(isolated_routines):
    """What ``manage_routines(action="run")`` and the prompt section both ask."""
    _write_routine(assistant_routines_dir("perps"), "lp_scanner")
    assert assistant_routines("perps").get("lp_scanner") is not None

    set_muted("perps", "routine", "lp_scanner", True)
    assert assistant_routines("perps").get("lp_scanner") is None
    # A playbook pointing at it now reports the broken reference the panel warns
    # about — the same resolver answers both questions.
    assert skills_module._routine_exists("lp_scanner", "perps") is False


def test_muting_for_the_chat_leaves_the_human_routines_page_alone(isolated_routines):
    """A mute is about what an assistant is told, not about what a person may do."""
    before = set(discover_routines(force_reload=True))
    a_routine = sorted(before)[0]

    set_muted(None, "routine", a_routine, True)
    assert a_routine not in assistant_routines(None)
    assert a_routine not in assistant_routines("condor")
    # ``RoutineStore.list_routines`` reads this, and it is untouched.
    assert a_routine in discover_routines(force_reload=True)


# ── 4. through the route ──


@pytest.fixture
def web_env(tmp_path, monkeypatch):
    """One real Agent on disk, reachable through the agents router."""
    from condor.agents.agent import AgentStore

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    AgentStore().create(name="Brigado", description="BRL market making")
    return tmp_path


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=555, username="u", first_name="U", role="user"
    )
    return TestClient(app)


def test_the_panel_mutes_a_playbook_and_still_shows_it(web_env, no_routines):
    _write_skill(web_env, "brigado", "lp_rebalance")
    client = _client()

    assert client.get("/agents/brigado/brain").json()["skills"][0]["muted"] is False

    put = client.put(
        "/agents/brigado/mutes",
        json={"kind": "skill", "name": "lp_rebalance", "muted": True},
    )
    assert put.status_code == 200, put.text

    card = client.get("/agents/brigado/brain").json()["skills"][0]
    assert card["slug"] == "lp_rebalance" and card["muted"] is True
    # Still openable and still editable from the panel — muting is not deleting.
    body = client.get("/agents/brigado/skills/lp_rebalance")
    assert body.status_code == 200 and body.json()["muted"] is True
    # …while the Agent's own view no longer has it at all.
    assert SkillStore("brigado").read("lp_rebalance") is None

    client.put(
        "/agents/brigado/mutes",
        json={"kind": "skill", "name": "lp_rebalance", "muted": False},
    )
    assert client.get("/agents/brigado/brain").json()["skills"][0]["muted"] is False
    assert SkillStore("brigado").read("lp_rebalance") is not None


def test_the_panel_mutes_a_routine(web_env, isolated_routines, monkeypatch):
    monkeypatch.setattr(base, "_PROJECT_ROOT", web_env)
    _write_routine(assistant_routines_dir("brigado"), "lp_scanner")
    client = _client()

    client.put(
        "/agents/brigado/mutes",
        json={"kind": "routine", "name": "lp_scanner", "muted": True},
    )
    cards = {
        r["name"]: r for r in client.get("/agents/brigado/brain").json()["routines"]
    }
    assert cards["lp_scanner"]["muted"] is True
    assert "lp_scanner" not in assistant_routines("brigado")


def test_the_route_refuses_a_kind_it_does_not_know(web_env):
    bad = _client().put(
        "/agents/brigado/mutes",
        json={"kind": "memory", "name": "x", "muted": True},
    )
    assert bad.status_code == 400
