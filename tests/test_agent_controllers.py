"""An agent's own controller source — the folder model, the local tool actions,
the prompt index and the gating (FEAT-126).

Server I/O lives in ``test_agent_controllers_sync.py``. Every test here runs on
the isolated roots of ``conftest._isolated_runtime_root``: ``tmp_path/agents``
is the writable (local) root and ``tmp_path/stock-agents`` the shipped one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from condor import agent_controllers as ac
from condor.agent_controllers import ControllerError

MM_SOURCE = '''"""Pure maker around mid."""
from hummingbot.strategy_v2.controllers.market_making_controller_base import (
    MarketMakingControllerBase,
    MarketMakingControllerConfigBase,
)


class PmmKingConfig(MarketMakingControllerConfigBase):
    controller_name: str = "pmm_king"


class PmmKing(MarketMakingControllerBase):
    pass
'''

DIRECTIONAL_SOURCE = """from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
)


class EmaCross(DirectionalTradingControllerBase):
    pass
"""

GENERIC_SOURCE = """from hummingbot.strategy_v2.controllers import ControllerBase


class Chessboard(ControllerBase):
    pass
"""

UNTYPED_SOURCE = "class Mystery:\n    pass\n"


@pytest.fixture
def roots(tmp_path):
    return SimpleNamespace(
        local=tmp_path / "agents", stock=tmp_path / "stock-agents", tmp=tmp_path
    )


def _agent(root: Path, slug: str) -> Path:
    home = root / slug
    home.mkdir(parents=True, exist_ok=True)
    (home / "AGENT.md").write_text(f"---\nname: {slug}\n---\n\nBody.\n")
    return home


def _controller(
    base: Path,
    name: str,
    source: str = MM_SOURCE,
    samples: dict[str, str] | None = None,
    md: str | None = None,
) -> Path:
    d = base / "controllers" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(source)
    if md is not None:
        (d / "CONTROLLER.md").write_text(md)
    for stem, text in (samples or {}).items():
        (d / "sample_configs").mkdir(exist_ok=True)
        (d / "sample_configs" / f"{stem}.yml").write_text(text)
    return d


# ── discovery and layering ──


def test_own_local_shadows_own_stock_and_shared(roots):
    _controller(roots.stock / "_shared", "pmm_king", MM_SOURCE + "# shared stock\n")
    _controller(roots.local / "_shared", "pmm_king", MM_SOURCE + "# shared local\n")
    _controller(roots.stock / "mm", "pmm_king", MM_SOURCE + "# own stock\n")
    _controller(roots.local / "mm", "pmm_king", MM_SOURCE + "# own local\n")

    src = ac.agent_controllers("mm")["pmm_king"]
    assert "# own local" in src.source_path.read_text()
    assert src.origin == "agent:mm" and not src.shared and not src.stock


def test_shared_is_visible_to_every_agent_and_marked(roots):
    _controller(roots.stock / "_shared", "rebate_mill", GENERIC_SOURCE)
    for slug in ("mm", "other"):
        src = ac.agent_controllers(slug)["rebate_mill"]
        assert src.shared and src.stock and src.to_dict()["shared"] is True


def test_shared_local_shadows_shared_stock(roots):
    _controller(roots.stock / "_shared", "x", MM_SOURCE + "# stock\n")
    _controller(roots.local / "_shared", "x", MM_SOURCE + "# local\n")
    assert "# local" in ac.agent_controllers("mm")["x"].source_path.read_text()


def test_folders_without_a_matching_module_are_skipped(roots):
    d = roots.local / "mm" / "controllers"
    (d / "loose").mkdir(parents=True)
    (d / "loose" / "other.py").write_text(MM_SOURCE)
    _controller(roots.local / "mm", "_private")
    assert ac.agent_controllers("mm") == {}


def test_type_from_frontmatter_beats_the_ast(roots):
    _controller(
        roots.local / "mm",
        "odd",
        DIRECTIONAL_SOURCE,
        md="---\ntype: generic\ndescription: From the md\n---\n",
    )
    src = ac.agent_controllers("mm")["odd"]
    assert src.controller_type == "generic"
    assert src.description == "From the md"


@pytest.mark.parametrize(
    "source,expected",
    [
        (MM_SOURCE, "market_making"),
        (DIRECTIONAL_SOURCE, "directional_trading"),
        (GENERIC_SOURCE, "generic"),
        (UNTYPED_SOURCE, None),
        ("def broken(:\n", None),
    ],
)
def test_infer_type(source, expected):
    assert ac.infer_type(source) == expected


def test_unresolvable_type_is_listed_with_a_hint(roots):
    _controller(roots.local / "mm", "mystery", UNTYPED_SOURCE)
    src = ac.agent_controllers("mm")["mystery"]
    assert src.controller_type is None
    assert "CONTROLLER.md" in src.to_dict()["type_error"]


def test_description_falls_back_to_the_docstring(roots):
    _controller(roots.local / "mm", "pmm_king")
    assert (
        ac.agent_controllers("mm")["pmm_king"].description == "Pure maker around mid."
    )


def test_crlf_and_trailing_whitespace_share_a_digest():
    assert ac.source_digest("a = 1\nb = 2\n") == ac.source_digest(
        "a = 1\r\nb = 2\r\n\r\n   \n"
    )
    assert ac.source_digest("a = 1\n") != ac.source_digest("a = 2\n")


# ── samples ──


def test_samples_are_listed_sorted_and_config_is_never_one(roots):
    _controller(
        roots.local / "mm",
        "pmm_king",
        samples={"conservative": "a: 1\n", "aggressive": "a: 2\n", "config": "a: 3\n"},
    )
    assert list(ac.agent_controllers("mm")["pmm_king"].samples) == [
        "aggressive",
        "conservative",
    ]


def test_load_sample_fills_the_ids(roots):
    _controller(
        roots.local / "mm", "pmm_king", samples={"aggressive": "spread: 0.01\n"}
    )
    data = ac.load_sample(ac.get_controller("mm", "pmm_king"), "aggressive")
    assert data == {
        "spread": 0.01,
        "controller_name": "pmm_king",
        "controller_type": "market_making",
    }


def test_load_sample_rejects_a_foreign_controller_name(roots):
    _controller(
        roots.local / "mm", "pmm_king", samples={"bad": "controller_name: pmm_queen\n"}
    )
    with pytest.raises(ControllerError, match="pmm_queen"):
        ac.load_sample(ac.get_controller("mm", "pmm_king"), "bad")


def test_load_sample_rejects_a_mismatched_type(roots):
    _controller(
        roots.local / "mm", "pmm_king", samples={"bad": "controller_type: generic\n"}
    )
    with pytest.raises(ControllerError, match="market_making"):
        ac.load_sample(ac.get_controller("mm", "pmm_king"), "bad")


def test_a_sample_named_config_is_refused_on_write(roots):
    _controller(roots.local / "mm", "pmm_king")
    with pytest.raises(ControllerError, match="dropped when an agent is published"):
        ac.write_sample("mm", "pmm_king", "config", "a: 1\n")


def test_a_non_mapping_sample_is_refused(roots):
    _controller(roots.local / "mm", "pmm_king")
    with pytest.raises(ControllerError, match="mapping"):
        ac.write_sample("mm", "pmm_king", "list", "- 1\n- 2\n")


# ── writes ──


def test_write_forks_a_stock_controller_down(roots):
    stock_dir = _controller(
        roots.stock / "mm", "pmm_king", samples={"aggressive": "a: 1\n"}
    )
    result = ac.write_source("mm", "pmm_king", MM_SOURCE + "# edited\n")

    assert result["forked_from_stock"] is True
    local = roots.local / "mm" / "controllers" / "pmm_king"
    assert "# edited" in (local / "pmm_king.py").read_text()
    assert (local / "sample_configs" / "aggressive.yml").is_file()
    assert "# edited" not in (stock_dir / "pmm_king.py").read_text()


def test_write_refuses_code_that_does_not_parse_or_has_no_type(roots):
    with pytest.raises(ControllerError, match="does not parse"):
        ac.write_source("mm", "x", "def broken(:\n")
    with pytest.raises(ControllerError, match="CONTROLLER.md"):
        ac.write_source("mm", "x", UNTYPED_SOURCE)


def test_write_accepts_an_untyped_source_once_controller_md_says(roots):
    ac.write_controller_md("mm", "x", "---\ntype: generic\n---\n")
    assert ac.write_source("mm", "x", UNTYPED_SOURCE)["controller_type"] == "generic"


def test_delete_refuses_stock_and_reverts_a_fork(roots):
    _controller(roots.stock / "mm", "pmm_king")
    with pytest.raises(ControllerError, match="ships with Condor"):
        ac.delete("mm", "pmm_king")
    ac.write_source("mm", "pmm_king", MM_SOURCE + "# fork\n")
    assert ac.delete("mm", "pmm_king")["reverted_to_stock"] is True


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x.py", "1abc"])
def test_controller_names_are_one_identifier(bad):
    with pytest.raises(ControllerError):
        ac.check_controller_name(bad)


# ── publishing ──


def test_publish_carries_controllers_and_never_backups(roots):
    from condor.layering import publishable_files

    home = _agent(roots.local, "mm")
    _controller(roots.local / "mm", "pmm_king", samples={"aggressive": "a: 1\n"})
    ac.backup_server_copy("mm", "pmm_king", "brigado", "old = 1\n")

    files = {str(p) for p in publishable_files(home)}
    assert "controllers/pmm_king/pmm_king.py" in files
    assert "controllers/pmm_king/sample_configs/aggressive.yml" in files
    assert not [f for f in files if ".server_backups" in f]


# ── the MCP tool's local actions ──


@pytest.fixture
def tool(monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import agent_controllers as tool_module

    def run(slug: str = "", **kwargs):
        monkeypatch.setattr(settings, "agent_slug", slug or "condor")
        return asyncio.run(tool_module.manage_agent_controllers(**kwargs))

    return run


def test_agent_cannot_write_or_delete_shared(roots, tool):
    _agent(roots.local, "mm")
    _controller(roots.stock / "_shared", "rebate_mill", GENERIC_SOURCE)

    assert (
        "read-only"
        in tool("mm", action="write", name="x", code=MM_SOURCE, shared=True)["error"]
    )
    assert (
        "shared controller"
        in tool("mm", action="write", name="rebate_mill", code=GENERIC_SOURCE)["error"]
    )
    assert (
        "shared controller"
        in tool("mm", action="write", name="rebate_mill", sample="s", code="a: 1\n")[
            "error"
        ]
    )
    assert (
        "shared controller" in tool("mm", action="delete", name="rebate_mill")["error"]
    )


def test_agent_cannot_target_another_agent(roots, tool):
    _agent(roots.local, "mm")
    _agent(roots.local, "other")
    out = tool("mm", action="list", agent="other")
    assert "only its own" in out["error"]


def test_condor_targets_an_agent_and_publishes_shared(roots, tool):
    _agent(roots.local, "mm")
    out = tool(action="write", agent="mm", name="pmm_king", code=MM_SOURCE)
    assert out["written"] and (roots.local / "mm" / "controllers" / "pmm_king").is_dir()

    tool(action="write", name="rebate_mill", code=GENERIC_SOURCE, shared=True)
    assert (roots.local / "_shared" / "controllers" / "rebate_mill").is_dir()

    listed = tool("mm", action="list")["controllers"]
    assert {(r["name"], r["shared"]) for r in listed} == {
        ("pmm_king", False),
        ("rebate_mill", True),
    }


def test_read_returns_code_md_and_styles(roots, tool):
    _agent(roots.local, "mm")
    _controller(roots.local / "mm", "pmm_king", samples={"aggressive": "spread: 1\n"})
    out = tool("mm", action="read", name="pmm_king")
    assert out["code"] == MM_SOURCE and out["samples"] == {"aggressive": "spread: 1\n"}
    assert tool("mm", action="read", name="pmm_king", sample="aggressive")["yaml"] == (
        "spread: 1\n"
    )


def test_server_actions_need_an_active_server(roots, tool, monkeypatch):
    from mcp_servers.condor.settings import settings

    monkeypatch.setattr(settings, "active_server", "")
    _agent(roots.local, "mm")
    assert "No active server" in tool("mm", action="status")["error"]


def test_server_actions_call_the_main_process_routes(roots, tool, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import agent_controllers as tool_module

    calls = []

    async def fake_call(method, path, body=None, timeout=None):
        calls.append((method, path, body))
        return {"controllers": [{"name": "pmm_king"}, {"name": "other"}]}

    monkeypatch.setattr(settings, "active_server", "srv one")
    monkeypatch.setattr(tool_module, "call_main_api", fake_call)
    _agent(roots.local, "mm")

    status = tool("mm", action="status", name="pmm_king")
    assert status["controllers"] == [{"name": "pmm_king"}]
    tool("mm", action="sync", name="pmm_king", overwrite=True)
    tool("mm", action="upload_config", name="pmm_king", sample="aggressive")
    tool(
        "mm",
        action="pull",
        name="pmm_king",
        controller_type="market_making",
        configs=["pmm_king__a"],
    )
    assert calls == [
        ("GET", "/agents/mm/controllers?server_name=srv%20one", None),
        (
            "POST",
            "/agents/mm/controllers/pmm_king/sync",
            {"server_name": "srv one", "overwrite": True},
        ),
        (
            "POST",
            "/agents/mm/controllers/pmm_king/configs/aggressive",
            {"server_name": "srv one", "config_name": None, "overwrite": False},
        ),
        (
            "POST",
            "/agents/mm/controllers/pull",
            {
                "server_name": "srv one",
                "controller_type": "market_making",
                "controller_name": "pmm_king",
                "configs": ["pmm_king__a"],
                "overwrite": False,
            },
        ),
    ]


def test_the_registered_literal_matches_the_classified_actions():
    """A new action must be classified before it can ship (the danger-set pin)."""
    import typing

    from condor.runtime import danger
    from mcp_servers.condor import server
    from mcp_servers.condor.tools import agent_controllers as tool_module

    fn = getattr(server.manage_agent_controllers, "fn", server.manage_agent_controllers)
    literal = set(typing.get_args(typing.get_type_hints(fn)["action"]))
    assert literal == set(tool_module.LOCAL_ACTIONS + tool_module.SERVER_ACTIONS)
    assert literal == (
        danger.MUTATING_AGENT_CONTROLLER_ACTIONS
        | danger.READ_ONLY_AGENT_CONTROLLER_ACTIONS
    )


# ── gating ──


def _call(**args):
    return {"tool": "mcp__condor__manage_agent_controllers", "input": args}


def test_only_an_overwriting_server_push_is_gated():
    from condor.runtime.danger import is_dangerous_tool_call, is_mutating_tool_call

    for action in ("sync", "upload_config"):
        assert is_dangerous_tool_call(_call(action=action, name="x", overwrite=True))
        assert not is_dangerous_tool_call(_call(action=action, name="x"))
    assert not is_dangerous_tool_call(_call(action="pull", name="x", overwrite=True))
    assert is_dangerous_tool_call({"tool": "manage_agent_controllers", "input": None})
    # The gate's calls are a subset of the log's.
    assert is_mutating_tool_call(_call(action="sync", name="x", overwrite=True))


def test_every_write_is_recorded_and_reads_are_not():
    from condor.runtime.danger import is_recordable_tool_call

    for action in ("write", "delete", "sync", "upload_config", "pull"):
        assert is_recordable_tool_call(_call(action=action, name="x"))
    for action in ("list", "read", "status"):
        assert not is_recordable_tool_call(_call(action=action, name="x"))


def test_dry_run_refuses_writes_and_keeps_reads():
    from condor.runtime.danger import dry_run_refusal

    assert dry_run_refusal(_call(action="sync", name="x"))
    assert dry_run_refusal(_call(action="write", name="x"))
    assert dry_run_refusal(_call(action="status")) is None


def test_the_action_log_keys_on_the_action():
    from condor.agents.actions import _DISPATCH_TOOLS
    from condor.runtime.danger import format_tool_summary

    assert "manage_agent_controllers" in _DISPATCH_TOOLS
    line = format_tool_summary(_call(action="sync", name="pmm_king", overwrite=True))
    assert "sync 'pmm_king'" in line and "OVERWRITE" in line


# ── the prompt index ──


def _tick_prompt(slug: str) -> str:
    from condor.agents.prompts import build_tick_prompt

    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug=slug)
    loop = SimpleNamespace(
        instructions="Do the thing.",
        agent_key="claude-code",
        slug="grid",
        agent_slug=slug,
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=loop,
        config={"execution_mode": "loop"},
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        cached_routines_section="",
    )


def test_tick_and_domain_prompts_list_owned_controllers(roots):
    from condor.memory import domain_context

    _agent(roots.local, "mm")
    _controller(
        roots.local / "mm",
        "pmm_king",
        samples={"aggressive": "a: 1\n", "conservative": "a: 2\n"},
    )
    tick = _tick_prompt("mm")
    assert "CONTROLLERS — Hummingbot controller source you own" in tick
    assert (
        "  - pmm_king (market_making): Pure maker around mid. · styles: "
        "aggressive, conservative"
    ) in tick
    assert "controller_sources" in tick

    delegated = "\n\n".join(domain_context("mm", 1))
    assert "pmm_king (market_making)" in delegated


def test_no_controllers_means_no_section(roots):
    from condor.memory import domain_context

    _agent(roots.local, "plain")
    assert "CONTROLLERS" not in _tick_prompt("plain")
    assert not any("CONTROLLERS" in s for s in domain_context("plain", 1))


def test_shared_controllers_are_marked_in_the_index(roots):
    from condor.agent_controllers import controllers_section

    _controller(roots.stock / "_shared", "rebate_mill", GENERIC_SOURCE)
    assert "  - rebate_mill (generic, shared)" in controllers_section("any")


def test_controller_mode_says_status_before_deploy():
    from condor.agents.prompts import _build_controller_mode_section

    text = _build_controller_mode_section("mm-bot", None)
    assert 'manage_agent_controllers(action="status")' in text
    assert "overwrite=true" in text


# ── the shared skill ──


def test_the_skill_is_shared_and_in_every_agents_index(roots, monkeypatch):
    from condor import paths
    from condor.memory import SkillStore

    shipped = Path(__file__).resolve().parent.parent / "agents"
    monkeypatch.setenv(paths.STOCK_AGENTS_ROOT_ENV, str(shipped))
    for slug in ("directional_trader", "market_making_expert", "condor"):
        assert "controller_sources" in SkillStore(slug).list_index()
    assert SkillStore("market_making_expert").read("controller_sources") is not None


def test_the_chat_context_is_unchanged(roots):
    """Condor reaches controllers through `agent=`; its own context gains nothing."""
    from condor.memory import domain_context

    _controller(roots.stock / "_shared", "rebate_mill", GENERIC_SOURCE)
    assert not any("CONTROLLERS" in s for s in domain_context("condor", 1))


# ── a forked allowlist still reaches the tool its controllers need ──


def _forked_agent(root: Path, slug: str, tools: list[str]) -> Path:
    home = root / slug
    home.mkdir(parents=True, exist_ok=True)
    listed = "".join(f"- {t}\n" for t in tools)
    (home / "AGENT.md").write_text(f"---\nname: {slug}\ntools:\n{listed}---\n\nBody.\n")
    return home


def test_a_customized_allowlist_gains_the_controllers_tool_when_it_has_controllers(
    roots,
):
    """QA on PR 244: a local AGENT.md froze its ``tools:`` before the tool
    shipped, while the prompt still told the agent to call it before deploying."""
    from condor.agents.agent import AgentStore

    _forked_agent(roots.local, "mm", ["manage_bots", "manage_controllers"])
    _controller(roots.local / "mm", "pmm_qa")

    tools = AgentStore().get("mm").tools
    assert tools == ["manage_bots", "manage_controllers", "manage_agent_controllers"]


def test_an_allowlist_without_controllers_is_left_as_authored(roots):
    from condor.agents.agent import AgentStore

    _forked_agent(roots.local, "mm", ["manage_bots"])
    assert AgentStore().get("mm").tools == ["manage_bots"]


def test_an_unrestricted_agent_stays_unrestricted(roots):
    from condor.agents.agent import AgentStore

    _agent(roots.local, "mm")
    _controller(roots.local / "mm", "pmm_qa")
    assert AgentStore().get("mm").tools == []


def test_a_namespaced_listing_is_not_added_twice(roots):
    from condor.agents.agent import AgentStore

    _forked_agent(roots.local, "mm", ["mcp__condor__manage_agent_controllers"])
    _controller(roots.local / "mm", "pmm_qa")
    assert AgentStore().get("mm").tools == ["mcp__condor__manage_agent_controllers"]
