"""Two agent roots: the library ships, the install writes (FEAT-115).

``agents/`` used to be a directory git and the product both wrote to, which made
every ``/update`` an offer to discard the operator's own agent. It is now two
roots — the shipped library the repo tracks, and the one this install writes,
which git has never heard of — with reads layered **per item**, local shadowing
stock.

The three properties this file pins, in the order they matter:

* **Nothing changes for an install that has written nothing.** With an empty
  local root every resolver answers exactly what the shipped tree alone says.
* **Every write lands local.** The shipped tree is byte-identical after an edit,
  a promoted skill, a new strategy and a raw web write.
* **The fork is per file, not per agent.** An operator who tweaks the shipped
  ``condor`` agent keeps the tweak *and* still receives upstream's new skills
  for that same agent.
"""

from __future__ import annotations

import pytest

from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from condor.layering import FORKED_FROM_KEY
from condor.memory.paths import (
    agent_home,
    iter_agent_slugs,
    resolve_agent_file,
    shared_skills_root,
    shared_skills_roots,
    stock_agent_home,
)
from condor.memory.skills import SkillStore

AGENT_MD = "---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"
SKILL_MD = (
    "---\nname: {slug}\ndescription: d\nwhen_to_use: whenever\nsource: builtin\n"
    "---\n\n{body}\n"
)


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


@pytest.fixture
def stock():
    """A small shipped library: one agent, one skill, one shared playbook."""
    root = stock_agent_home("scout")
    _write(
        root / "AGENT.md", AGENT_MD.format(name="Scout", desc="shipped", body="Ship.")
    )
    _write(
        root / "skills" / "recon" / "SKILL.md",
        SKILL_MD.format(slug="recon", body="Look."),
    )
    _write(
        shared_skills_roots()[1] / "house_rules" / "SKILL.md",
        SKILL_MD.format(slug="house_rules", body="Behave."),
    )
    return root


# ── 1. An install that has written nothing sees exactly the shipped tree ──


def test_an_empty_local_root_resolves_the_shipped_library_unchanged(stock):
    assert iter_agent_slugs() == ["scout"]

    agent = AgentStore().get("scout")
    assert agent is not None and agent.name == "Scout"
    assert agent.source == stock / "AGENT.md"
    # The *writable* home is local and does not exist yet — nothing has written.
    assert agent.home == agent_home("scout")
    assert not agent.home.exists()

    catalog = {row["slug"]: row for row in SkillStore("scout").catalog()}
    assert set(catalog) == {"recon", "house_rules"}
    assert all(row["stock"] for row in catalog.values())


def test_a_stock_agent_is_listed_beside_a_locally_created_one(stock):
    AgentStore().create(name="Perps", description="funding")

    assert iter_agent_slugs() == ["perps", "scout"]
    assert {a.slug for a in AgentStore().list_all()} == {"perps", "scout"}
    # The new one is this install's, and nowhere near the shipped tree.
    assert (agent_home("perps") / "AGENT.md").exists()
    assert not (stock.parent / "perps").exists()


# ── 2. Every write lands in the local root ──


def test_editing_a_stock_agent_forks_it_down_and_leaves_the_shipped_copy(stock):
    before = (stock / "AGENT.md").read_text()

    agent = AgentStore().get("scout")
    agent.instructions = "Ship differently."
    AgentStore().update(agent)

    assert (stock / "AGENT.md").read_text() == before, "the shipped copy moved"
    local = agent_home("scout") / "AGENT.md"
    assert local.exists() and "Ship differently." in local.read_text()
    # And the fork is recorded, naming the revision it diverged from.
    assert FORKED_FROM_KEY in local.read_text()
    assert AgentStore().get("scout").source == local


def test_editing_a_stock_skill_forks_the_folder_not_the_agent(stock):
    store = SkillStore("scout")
    assert store.edit("recon", body="Look harder.").get("error") is None

    assert (stock / "skills" / "recon" / "SKILL.md").read_text().endswith("Look.\n")
    local = agent_home("scout") / "skills" / "recon" / "SKILL.md"
    assert "Look harder." in local.read_text()
    assert FORKED_FROM_KEY in local.read_text()


def test_a_new_strategy_lands_local_and_the_shipped_tree_is_untouched(stock):
    StrategyStore().create(agent_slug="scout", name="Grid", instructions="tick")

    s = StrategyStore().get("scout", "grid")
    assert s is not None
    assert s.home == agent_home("scout") / "strategies" / "grid"
    assert s.source == s.home / "strategy.md"
    assert not (stock / "strategies").exists()


# ── 3. The fork is per item ──


def test_a_forked_agent_md_still_receives_the_librarys_other_files(stock):
    agent = AgentStore().get("scout")
    agent.instructions = "Mine."
    AgentStore().update(agent)

    # Upstream ships a *new* skill for the same agent...
    _write(
        stock / "skills" / "triage" / "SKILL.md",
        SKILL_MD.format(slug="triage", body="Sort."),
    )

    catalog = {row["slug"]: row for row in SkillStore("scout").catalog()}
    # ...and the install that forked AGENT.md gets it anyway.
    assert "triage" in catalog and catalog["triage"]["stock"] is True
    assert catalog["recon"]["stock"] is True
    assert "Mine." in AgentStore().get("scout").instructions


def test_a_local_skill_shadows_the_shipped_one_of_the_same_slug(stock):
    _write(
        agent_home("scout") / "skills" / "recon" / "SKILL.md",
        SKILL_MD.format(slug="recon", body="My recon."),
    )

    read = SkillStore("scout").read("recon")
    assert read is not None and "My recon." in read["body"]
    rows = {row["slug"]: row for row in SkillStore("scout").catalog()}
    # Listed once, and as local.
    assert rows["recon"]["stock"] is False


# ── 4. Deleting a shipped item is refused, and names the answer ──


def test_deleting_a_stock_skill_is_refused_and_names_the_mute(stock):
    result = SkillStore("scout").delete("recon")

    assert isinstance(result, dict) and "mute" in result["error"]
    assert (stock / "skills" / "recon" / "SKILL.md").exists()


def test_muting_a_stock_skill_works_and_writes_locally(stock):
    from condor.memory.mutes import set_muted

    set_muted("scout", "skill", "recon", True)

    assert (agent_home("scout") / "mutes.yml").exists()
    assert not (stock / "mutes.yml").exists()
    assert "recon" not in {row["slug"] for row in SkillStore("scout").catalog()}


def test_deleting_a_stock_agent_is_refused(stock):
    with pytest.raises(ValueError, match="ships with Condor"):
        AgentStore().delete("scout")
    assert (stock / "AGENT.md").exists()


def test_deleting_a_stock_strategy_is_refused(stock):
    _write(
        stock / "strategies" / "shipped" / "strategy.md",
        "---\nname: Shipped\n---\n\ntick\n",
    )

    with pytest.raises(ValueError, match="ships with Condor"):
        StrategyStore().delete("scout", "shipped")
    assert (stock / "strategies" / "shipped" / "strategy.md").exists()


def test_deleting_a_local_strategy_still_works(stock):
    StrategyStore().create(agent_slug="scout", name="Grid", instructions="tick")
    (StrategyStore().get("scout", "grid").home / "sessions").mkdir()

    assert StrategyStore().delete("scout", "grid") is True
    assert StrategyStore().get("scout", "grid") is None


# ── 5. Publishing a still-stock skill forks it down, then moves the fork ──


def test_publishing_a_stock_skill_moves_the_fork_and_not_the_shipped_copy(stock):
    _write(
        agent_home("condor") / "skills" / "recon" / "SKILL.md",
        SKILL_MD.format(slug="recon", body="Condor's own."),
    )

    result = SkillStore(None).create(
        name="recon",
        description="d",
        when_to_use="whenever",
        body="Published.",
        shared=True,
    )

    assert result.get("shared") is True
    assert (shared_skills_root() / "recon" / "SKILL.md").exists()
    assert not (agent_home("condor") / "skills" / "recon").exists()
    # The shipped library is untouched by any of it.
    assert (stock / "skills" / "recon" / "SKILL.md").read_text().endswith("Look.\n")


# ── 6. The raw web writes bypass the stores, so they carry the guard ──


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


def test_the_raw_agent_md_route_writes_into_the_local_root(stock):
    before = (stock / "AGENT.md").read_text()

    resp = _client().put(
        "/agents/scout", json={"content": "---\nname: Scout\n---\n\nX"}
    )

    assert resp.status_code == 200, resp.text
    assert (stock / "AGENT.md").read_text() == before
    assert "X" in (agent_home("scout") / "AGENT.md").read_text()


def test_the_raw_strategy_md_route_writes_into_the_local_root(stock):
    _write(
        stock / "strategies" / "shipped" / "strategy.md",
        "---\nname: Shipped\n---\n\ntick\n",
    )
    before = (stock / "strategies" / "shipped" / "strategy.md").read_text()

    resp = _client().put(
        "/agents/scout/strategies/shipped",
        json={"content": "---\nname: Shipped\n---\n\nmine\n"},
    )

    assert resp.status_code == 200, resp.text
    assert (stock / "strategies" / "shipped" / "strategy.md").read_text() == before
    local = agent_home("scout") / "strategies" / "shipped" / "strategy.md"
    assert "mine" in local.read_text()


def test_the_raw_learnings_route_writes_into_the_local_root(stock):
    StrategyStore().create(agent_slug="scout", name="Grid", instructions="tick")

    resp = _client().put(
        "/agents/scout/strategies/grid/learnings", json={"content": "learned"}
    )

    assert resp.status_code == 200, resp.text
    learnings = agent_home("scout") / "strategies" / "grid" / "learnings.md"
    assert learnings.read_text() == "learned"


def test_the_create_strategy_route_seeds_the_journals_learnings_template(stock):
    from condor.agents.journal import LEARNINGS_TEMPLATE

    resp = _client().post("/agents/scout/strategies", json={"name": "Grid"})

    assert resp.status_code == 200, resp.text
    strategy = StrategyStore().get("scout", "grid")
    assert (strategy.home / "learnings.md").read_text() == LEARNINGS_TEMPLATE


def test_a_learning_appended_to_a_route_created_strategy_adds_no_section(
    stock, tmp_path
):
    import re

    from condor.agents.journal import JournalManager

    resp = _client().post("/agents/scout/strategies", json={"name": "Grid"})
    assert resp.status_code == 200, resp.text
    home = StrategyStore().get("scout", "grid").home

    JournalManager(
        "scout_grid.1", session_dir=tmp_path / "session_1", agent_dir=home
    ).append_learning("Spreads widen at the open", "market")

    text = (home / "learnings.md").read_text()
    assert re.findall(r"^## (.+)$", text, re.MULTILINE) == [
        "Market Observations",
        "Execution Notes",
        "Retired Insights",
    ]
    assert "Spreads widen at the open" in text


# ── 7. The defaults and the back-walk sites ──


def test_core_rules_and_the_reflect_policy_resolve_across_both_roots():
    from condor.agents.prompts import load_core_rules
    from condor.agents.reflection import load_policy
    from condor.memory.paths import defaults_layers

    local_defaults, stock_defaults = defaults_layers()
    _write(stock_defaults / "core_rules.md", "shipped rules")
    _write(stock_defaults / "reflect.md", "shipped reflection")

    assert load_core_rules("scout") == "shipped rules"
    assert load_policy("scout") == "shipped reflection"

    _write(local_defaults / "core_rules.md", "our rules")
    assert load_core_rules("scout") == "our rules"
    # The sibling the install never overrode still comes from upstream.
    assert load_policy("scout") == "shipped reflection"


def test_the_shutdown_policy_walks_strategy_then_agent_then_defaults(stock):
    from condor.agents.shutdown import load_shutdown_policy
    from condor.memory.paths import defaults_layers

    _write(defaults_layers()[1] / "shutdown.md", "---\non_kill_switch: hold\n---\n\nD")
    strategy = StrategyStore().create(
        agent_slug="scout", name="Grid", instructions="tick"
    )

    assert load_shutdown_policy(strategy)[1] == "D"

    _write(stock / "shutdown.md", "---\non_kill_switch: hold\n---\n\nA")
    assert load_shutdown_policy(strategy)[1] == "A"

    _write(strategy.home / "shutdown.md", "---\non_kill_switch: hold\n---\n\nS")
    assert load_shutdown_policy(strategy)[1] == "S"


@pytest.fixture
def forget_shadow_warnings():
    """The shadow warning is once per process; each test starts from none."""
    from condor.memory import paths

    paths._SHADOWED_RULEBOOKS_WARNED.clear()
    yield
    paths._SHADOWED_RULEBOOKS_WARNED.clear()


def _shadow_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "shadows" in r.getMessage()
    ]


def test_the_three_policy_loaders_share_one_resolver():
    """ARCH-656: no loader builds its own candidate walk or parses frontmatter."""
    import inspect

    from condor.agents import prompts, reflection, shutdown

    for fn in (
        reflection.load_policy,
        prompts.load_core_rules,
        shutdown.load_shutdown_policy,
    ):
        src = inspect.getsource(fn)
        assert "read_layered_file" in src
        for hand_rolled in (
            "agent_home_layers",
            "defaults_layers",
            "parse_frontmatter",
        ):
            assert hand_rolled not in src, (fn.__name__, hand_rolled)

    from condor.memory import paths

    # Tests and callers clear the set by its old name: it must be the same object.
    assert prompts._SHADOWED_RULEBOOKS_WARNED is paths._SHADOWED_RULEBOOKS_WARNED


def test_a_differing_local_reflect_policy_is_warned_about_once(
    forget_shadow_warnings, caplog
):
    from condor.agents.reflection import load_policy
    from condor.memory.paths import defaults_layers

    local_defaults, stock_defaults = defaults_layers()
    _write(stock_defaults / "reflect.md", "shipped reflection")
    _write(local_defaults / "reflect.md", "our reflection")

    with caplog.at_level("WARNING"):
        for _ in range(3):
            assert load_policy("scout") == "our reflection"

    warnings = _shadow_warnings(caplog)
    assert len(warnings) == 1
    assert str(local_defaults / "reflect.md") in warnings[0]
    assert str(stock_defaults / "reflect.md") in warnings[0]


def test_an_identical_local_reflect_policy_says_nothing(forget_shadow_warnings, caplog):
    from condor.agents.reflection import load_policy
    from condor.memory.paths import defaults_layers

    local_defaults, stock_defaults = defaults_layers()
    _write(stock_defaults / "reflect.md", "same reflection")
    _write(local_defaults / "reflect.md", "same reflection")

    with caplog.at_level("WARNING"):
        assert load_policy("scout") == "same reflection"

    assert not _shadow_warnings(caplog)


def test_a_differing_local_strategy_shutdown_policy_is_warned_about_once(
    stock, forget_shadow_warnings, caplog
):
    from condor.agents.shutdown import load_shutdown_policy
    from condor.agents.strategy import STRATEGIES_DIRNAME

    strategy = StrategyStore().create(
        agent_slug="scout", name="Grid", instructions="tick"
    )
    shipped = stock / STRATEGIES_DIRNAME / strategy.slug / "shutdown.md"
    _write(shipped, "---\non_kill_switch: hold\n---\n\nshipped")
    _write(strategy.home / "shutdown.md", "---\non_kill_switch: hold\n---\n\nours")

    with caplog.at_level("WARNING"):
        for _ in range(3):
            assert load_shutdown_policy(strategy)[1] == "ours"

    warnings = _shadow_warnings(caplog)
    assert len(warnings) == 1
    assert str(strategy.home / "shutdown.md") in warnings[0]
    assert str(shipped) in warnings[0]


def test_an_identical_local_strategy_shutdown_policy_says_nothing(
    stock, forget_shadow_warnings, caplog
):
    from condor.agents.shutdown import load_shutdown_policy
    from condor.agents.strategy import STRATEGIES_DIRNAME

    strategy = StrategyStore().create(
        agent_slug="scout", name="Grid", instructions="tick"
    )
    text = "---\non_kill_switch: hold\n---\n\nsame"
    _write(stock / STRATEGIES_DIRNAME / strategy.slug / "shutdown.md", text)
    _write(strategy.home / "shutdown.md", text)

    with caplog.at_level("WARNING"):
        assert load_shutdown_policy(strategy)[1] == "same"

    assert not _shadow_warnings(caplog)


def test_a_frontmatter_only_strategy_shutdown_policy_still_wins(stock):
    """An empty body is a legitimate shutdown.md: its policy must not fall through."""
    from condor.agents.shutdown import (
        DEFAULT_POLICY,
        VALID_POLICIES,
        load_shutdown_policy,
    )
    from condor.memory.paths import defaults_layers

    other = next(p for p in sorted(VALID_POLICIES) if p != DEFAULT_POLICY)
    _write(
        defaults_layers()[1] / "shutdown.md",
        f"---\non_kill_switch: {DEFAULT_POLICY}\n---\n\nD",
    )
    _write(stock / "shutdown.md", f"---\non_kill_switch: {DEFAULT_POLICY}\n---\n\nA")
    strategy = StrategyStore().create(
        agent_slug="scout", name="Grid", instructions="tick"
    )
    _write(strategy.home / "shutdown.md", f"---\non_kill_switch: {other}\n---\n")

    policy, body = load_shutdown_policy(strategy)
    assert policy.on_kill_switch == other
    assert body == ""


def test_a_directory_named_like_the_policy_is_skipped_not_crashed_on(stock):
    from condor.agents.shutdown import load_shutdown_policy
    from condor.memory.paths import defaults_layers

    _write(defaults_layers()[1] / "shutdown.md", "---\non_kill_switch: hold\n---\n\nD")
    strategy = StrategyStore().create(
        agent_slug="scout", name="Grid", instructions="tick"
    )
    (strategy.home / "shutdown.md").mkdir(parents=True)

    assert load_shutdown_policy(strategy)[1] == "D"


# ── 8. Routines layer by name, the way they always have ──


def test_an_agents_routines_layer_local_over_stock(stock):
    from routines.base import assistant_routines

    def _routine(path, desc):
        _write(
            path,
            "from pydantic import BaseModel\n\n\n"
            "class Config(BaseModel):\n"
            f'    """{desc}"""\n\n\n'
            "async def run(config, context):\n"
            "    return 'ok'\n",
        )

    _routine(stock / "routines" / "recon_sweep.py", "SHIPPED")
    _routine(stock / "routines" / "only_shipped.py", "SHIPPED ONLY")

    found = assistant_routines("scout", force_reload=True)
    assert set(found) >= {"recon_sweep", "only_shipped"}
    assert found["recon_sweep"].description == "SHIPPED"

    _routine(agent_home("scout") / "routines" / "recon_sweep.py", "MINE")

    found = assistant_routines("scout", force_reload=True)
    assert found["recon_sweep"].description == "MINE"
    # Shadowed by name, one entry, and the sibling still comes from upstream.
    assert found["only_shipped"].description == "SHIPPED ONLY"


# ── 9. The property the whole feature exists for ──


def test_resolve_agent_file_prefers_local_and_falls_back_per_file(stock):
    assert resolve_agent_file("scout", "AGENT.md") == stock / "AGENT.md"

    _write(agent_home("scout") / "AGENT.md", "mine")

    assert resolve_agent_file("scout", "AGENT.md") == agent_home("scout") / "AGENT.md"
    assert (
        resolve_agent_file("scout", "skills", "recon", "SKILL.md")
        == stock / "skills" / "recon" / "SKILL.md"
    )
    assert resolve_agent_file("scout", "nothing.md") is None


# ── 10. What travels to a subprocess, and the one way back into the library ──


def test_an_mcp_child_is_handed_both_roots_resolved(monkeypatch):
    """A stdio MCP child gets ``env`` from its own config, not the parent's.

    So an unset variable does not mean "inherit" — it means the child recomputes
    a default, and would land on a different local root than its parent whenever
    the parent derived one from ``$CONDOR_RUNTIME_ROOT``. Both roots therefore
    travel resolved.
    """
    from condor.paths import local_agents_root, stock_agents_root
    from condor.runtime import toolsets

    monkeypatch.setattr(toolsets, "_bot_token", lambda: "t")
    entries = toolsets._env_entries(
        TELEGRAM_BOT_TOKEN="t",
        CONDOR_AGENTS_ROOT=str(local_agents_root()),
        CONDOR_STOCK_AGENTS_ROOT=str(stock_agents_root()),
    )
    env = {e["name"]: e["value"] for e in entries}

    assert env["CONDOR_AGENTS_ROOT"] == str(local_agents_root())
    assert env["CONDOR_STOCK_AGENTS_ROOT"] == str(stock_agents_root())
    assert env["CONDOR_AGENTS_ROOT"] != env["CONDOR_STOCK_AGENTS_ROOT"]


def test_publishing_copies_the_library_half_and_leaves_the_runtime_behind(stock):
    """The maintainer's only path into the shipped tree — and it stays a library.

    A whole-directory copy would drag the store, the journals and the mute set
    into the tracked tree, which is the wound the feature closed.
    """
    from condor.layering import publish_to_stock

    home = agent_home("perps")
    _write(
        home / "AGENT.md", AGENT_MD.format(name="Perps", desc="mine", body="Funding.")
    )
    _write(
        home / "skills" / "basis" / "SKILL.md", SKILL_MD.format(slug="basis", body="B.")
    )
    _write(
        home / "strategies" / "carry" / "strategy.md", "---\nname: Carry\n---\n\ntick\n"
    )
    _write(home / "strategies" / "carry" / "learnings.md", "learned")
    _write(home / "store" / "user_7" / "audit.log", "ran")
    _write(home / "mutes.yml", "skills: []\n")

    result = publish_to_stock("perps")

    assert result["published"] is True
    # Repo-relative, so the maintainer can read them straight into ``git add``.
    prefix = stock.parent.name
    assert set(result["paths"]) == {
        f"{prefix}/perps/AGENT.md",
        f"{prefix}/perps/skills/basis/SKILL.md",
        f"{prefix}/perps/strategies/carry/strategy.md",
    }
    shipped = stock_agent_home("perps")
    assert (shipped / "AGENT.md").exists()
    assert not (shipped / "store").exists()
    assert not (shipped / "mutes.yml").exists()
    assert not (shipped / "strategies" / "carry" / "learnings.md").exists()


def test_publishing_clears_the_fork_stamp(stock):
    from condor.layering import publish_to_stock

    agent = AgentStore().get("scout")
    agent.instructions = "Mine."
    AgentStore().update(agent)
    assert FORKED_FROM_KEY in (agent_home("scout") / "AGENT.md").read_text()

    publish_to_stock("scout", "AGENT.md")

    # A shipped file claiming to be forked from a shipped file would make every
    # install that pulls it look forked from itself.
    assert FORKED_FROM_KEY not in (stock / "AGENT.md").read_text()
    assert "Mine." in (stock / "AGENT.md").read_text()


def test_publishing_is_admin_only(stock, monkeypatch):
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(
        "config_manager.get_config_manager",
        lambda: type("CM", (), {"is_admin": staticmethod(lambda _uid: False)})(),
    )

    result = trading_agent.manage_agents(action="publish", agent_slug="scout")

    assert "admin-only" in result["error"]


# ── 11. The routine tools an agent actually calls ──


def _routine_src(desc: str) -> str:
    return (
        "from pydantic import BaseModel\n\n\n"
        "class Config(BaseModel):\n"
        f'    """{desc}"""\n\n\n'
        "async def run(config, context):\n"
        "    return 'ok'\n"
    )


def test_an_agent_can_read_and_edit_a_shipped_routine_but_not_delete_it(stock):
    from mcp_servers.condor.tools import routines as routine_tools

    _write(stock / "routines" / "recon_sweep.py", _routine_src("SHIPPED"))

    read = routine_tools.read_routine("recon_sweep", "scout")
    assert "SHIPPED" in read["code"]

    edited = routine_tools.edit_routine("recon_sweep", _routine_src("MINE"), "scout")
    assert edited.get("updated") is True
    # The edit forked it down; the shipped copy is untouched.
    assert "SHIPPED" in (stock / "routines" / "recon_sweep.py").read_text()
    assert "MINE" in (agent_home("scout") / "routines" / "recon_sweep.py").read_text()


def test_deleting_a_shipped_routine_is_refused_and_names_the_mute(stock):
    from mcp_servers.condor.tools import routines as routine_tools

    _write(stock / "routines" / "recon_sweep.py", _routine_src("SHIPPED"))

    result = routine_tools.delete_routine("recon_sweep", "scout")

    assert "mute" in result["error"]
    assert (stock / "routines" / "recon_sweep.py").exists()


def test_a_promoted_proposal_lands_in_the_local_library(stock):
    """The proposal names a slug the library already ships, so it forks it."""
    from condor.memory import proposals

    proposals.put(
        "scout",
        name="recon",
        description="d",
        when_to_use="whenever",
        body="Promoted.",
    )

    result = proposals.accept("scout")

    assert result.get("accepted") is True, result
    # And the offer itself was this install's from the start.
    assert not (stock / "proposals").exists()
    assert (
        "Promoted."
        in (agent_home("scout") / "skills" / "recon" / "SKILL.md").read_text()
    )
    assert (stock / "skills" / "recon" / "SKILL.md").read_text().endswith("Look.\n")


# ── SEC-648: a slug is one path segment, never a way into the shipped tree ──

TRAVERSAL = "../../agents/brigado"


@pytest.fixture
def repo_layout(tmp_path, monkeypatch):
    """The documented layout: stock ``<repo>/agents``, local ``<repo>/.condor/agents``.

    With it ``<local>/../../agents/brigado`` *is* the shipped brigado, which is
    what made a traversal slug read as a local file to every layering guard.
    """
    stock_root = tmp_path / "agents"
    local_root = tmp_path / ".condor" / "agents"
    monkeypatch.setenv("CONDOR_STOCK_AGENTS_ROOT", str(stock_root))
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(local_root))
    # A real install has its local root; without it the OS cannot walk the
    # ``..`` through a missing directory and the traversal never lands.
    local_root.mkdir(parents=True)
    agent_md = _write(
        stock_root / "brigado" / "AGENT.md",
        AGENT_MD.format(name="Brigado", desc="shipped", body="Ship."),
    )
    _write(
        stock_root / "brigado" / "strategies" / "default" / "strategy.md",
        "---\nname: Default\n---\n\nLoop.\n",
    )
    return stock_root, local_root, agent_md


def _local_files(local_root):
    return sorted(p for p in local_root.rglob("*")) if local_root.exists() else []


def test_a_traversal_slug_never_resolves_into_the_shipped_tree(repo_layout):
    from condor.layering import resolves_to_stock
    from condor.paths import UnsafeIdError

    assert AgentStore().get(TRAVERSAL) is None
    assert StrategyStore().list(TRAVERSAL) == []
    # The layering guard itself refuses the slug rather than answering "local";
    # the stores above are what turn that refusal into "no such agent".
    with pytest.raises(UnsafeIdError):
        resolves_to_stock(TRAVERSAL, "AGENT.md")


def test_the_strategy_slug_cannot_traverse_either(repo_layout):
    _, local_root, _ = repo_layout
    (local_root / "condor" / "strategies").mkdir(parents=True, exist_ok=True)
    sslug = "../../../../agents/brigado/strategies/default"

    assert StrategyStore().get("condor", sslug) is None
    assert StrategyStore().get_by_key(f"condor.{sslug}") is None


def test_deleting_or_updating_through_a_traversal_slug_leaves_the_shipped_copy(
    repo_layout,
):
    from condor.agents.agent import Agent
    from condor.paths import UnsafeIdError

    _, local_root, agent_md = repo_layout
    original = agent_md.read_bytes()

    assert AgentStore().delete(TRAVERSAL) is False
    assert agent_md.read_bytes() == original

    with pytest.raises(UnsafeIdError) as exc:
        AgentStore().update(Agent(slug=TRAVERSAL, name="x"))
    assert isinstance(exc.value, ValueError)
    assert agent_md.read_bytes() == original
    assert _local_files(local_root) == []


def test_manage_skill_cannot_target_a_traversal_slug(repo_layout):
    import asyncio

    from mcp_servers.condor.tools.skills import manage_skill

    stock_root, _, _ = repo_layout
    result = asyncio.run(
        manage_skill(
            action="create",
            agent=TRAVERSAL,
            name="evil",
            description="d",
            when_to_use="w",
            body="b",
        )
    )
    assert result == {"error": f"No agent or strategy found for '{TRAVERSAL}'"}
    assert not (stock_root / "brigado" / "skills").exists()


def test_safe_slug_accepts_every_slug_slugify_produces():
    from condor.frontmatter import slugify
    from condor.memory.paths import CHAT_SLUG, safe_slug
    from condor.paths import UnsafeIdError, local_agents_root

    names = ["RIVER Scalper v2", "BRL MM", "Risk Sentry", "Ñandú Bot", "Émile"]
    names.append("--- ---")
    assert slugify("--- ---") == "unnamed"
    for name in names:
        assert agent_home(slugify(name)) == local_agents_root() / slugify(name)
    assert agent_home(None) == local_agents_root() / CHAT_SLUG
    assert agent_home("") == local_agents_root() / CHAT_SLUG

    for bad in ("..", ".", "a/b", "a\\b", "a\0b", "x..y", "../x"):
        with pytest.raises(UnsafeIdError):
            safe_slug(bad)
    with pytest.raises(UnsafeIdError):
        resolve_agent_file("scout", "skills", "../../other", "SKILL.md")
