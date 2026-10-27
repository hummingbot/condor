"""Every tool call a skill writes down must be one the runtime accepts (SS-1).

The shared playbooks are instructions to a model, and a model follows them
literally. ``agent_framework`` documented four parameters that do not exist —
``agent=`` and ``limit=`` on the journal tools, ``entry=`` on the write,
``instance_id=`` on ``control_agent`` — so an agent that did exactly as it was
told was refused by the runtime, with no way to tell a wrong instruction from a
broken tool. QA found them by running each example by hand.

This reads the examples back out of the markdown and checks them against the
live signatures instead, which is the only form of this test that keeps working:
a rename in ``mcp_servers`` now fails here rather than in somebody's session.

Scope is deliberately narrow — a call is checked only when the skill writes it
as ``tool(args)`` and the tool is one this repo defines. Prose, shell snippets
and other people's APIs are left alone; the aim is to catch a parameter that
cannot exist, not to lint English.
"""

import inspect
import re
from pathlib import Path

import pytest

AGENTS = Path("agents")

#: ``tool(arg=..., ...)`` inside backticks — the form the tool maps use.
CALL_RE = re.compile(r"`([a-z_][a-z0-9_]*)\(([^`]*)\)`")

#: Tools whose signature this repo owns. Anything else named in a skill — a
#: pandas call, a Gateway REST path, ``client.market_data.*`` — is not ours to
#: check and is skipped rather than guessed at.
OWNED: dict[str, object] = {}


def _load_owned() -> dict[str, object]:
    if OWNED:
        return OWNED
    from mcp_servers.condor import server as condor_server
    from mcp_servers.hummingbot_api import server as hb_server

    for module in (condor_server, hb_server):
        for name, fn in vars(module).items():
            if inspect.iscoroutinefunction(fn) and not name.startswith("_"):
                OWNED[name] = fn
    return OWNED


def _documented_calls(path: Path) -> list[tuple[str, list[str], str]]:
    """``(tool, kwarg_names, raw)`` for every owned tool call in one file."""
    owned = _load_owned()
    found = []
    for tool, args in CALL_RE.findall(path.read_text()):
        if tool not in owned:
            continue
        names = []
        for part in re.findall(r"(\w+)\s*=", args):
            names.append(part)
        found.append((tool, names, f"{tool}({args})"))
    return found


SKILLS = sorted(AGENTS.rglob("skills/**/*.md"))


def test_the_skills_are_actually_being_read():
    """A guard on the guard: a glob that matches nothing passes every test."""
    assert len(SKILLS) > 10


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: str(p.relative_to(AGENTS)))
def test_every_documented_call_uses_parameters_that_exist(path):
    owned = _load_owned()
    problems = []
    for tool, names, raw in _documented_calls(path):
        real = set(inspect.signature(owned[tool]).parameters)
        for name in names:
            if name not in real:
                problems.append(f"{raw} — {tool} has no '{name}' (has: {sorted(real)})")
    assert not problems, "\n".join(problems)


# ── the specific claims SS-1..SS-4 made ─────────────────────────────────────

FRAMEWORK = AGENTS / "_shared/skills/agent_framework/SKILL.md"


@pytest.mark.parametrize(
    "tool,param",
    [
        ("trading_agent_journal_read", "agent_id"),
        ("trading_agent_journal_read", "max_entries"),
        ("trading_agent_journal_write", "agent_id"),
        ("trading_agent_journal_write", "entry_type"),
        ("control_agent", "agent_id"),
    ],
)
def test_the_framework_map_names_the_real_parameter(tool, param):
    owned = _load_owned()
    assert param in inspect.signature(owned[tool]).parameters

    body = FRAMEWORK.read_text()
    documented = [c for c in _documented_calls(FRAMEWORK) if c[0] == tool]
    assert documented, f"{tool} is no longer documented"
    assert any(
        param in names for _, names, _ in documented
    ), f"{tool} never passes {param}"
    assert body  # the table is what carries it


def test_the_dead_parameters_are_gone_from_the_map():
    """``agent``, ``limit``, ``entry`` and ``instance_id`` on the lifecycle and
    journal tools. Checked on the calls, not the prose — the skill names them in
    a note that explains why they are wrong."""
    for tool, names, raw in _documented_calls(FRAMEWORK):
        if tool in {"trading_agent_journal_read", "trading_agent_journal_write"}:
            assert "agent" not in names, raw
            assert "limit" not in names, raw
            assert "entry" not in names, raw
        if tool == "control_agent":
            assert "instance_id" not in names, raw


def test_cross_agent_memory_is_not_promised():
    """SS-2: ``manage_memory`` has no ``agent`` parameter and never did, so a
    playbook telling an agent to pass one was describing a capability the
    runtime does not have."""
    owned = _load_owned()
    assert "agent" not in inspect.signature(owned["manage_memory"]).parameters

    body = FRAMEWORK.read_text()
    assert "memory is NOT readable across agents" in body
    assert 'delegate(action="ask"' in body


def test_the_library_tools_really_do_take_an_agent():
    """The other half of SS-2: skills and routines ARE readable across agents,
    so the skill must not over-correct into saying nothing is."""
    owned = _load_owned()
    assert "agent" in inspect.signature(owned["manage_skill"]).parameters
    assert "agent" in inspect.signature(owned["manage_routines"]).parameters


# ── SS-3: an agent's own routine resolves by agent= ─────────────────────────


def test_a_shipped_agent_routine_lives_only_in_the_stock_layer():
    """The premise of SS-3, read off the repo.

    ``conftest._isolated_runtime_root`` repoints both agent roots at a tmp dir
    for every test — deliberately, so a suite never reads the developer's live
    install — so this cannot resolve through ``assistant_routines_dirs`` without
    testing the fixture instead of the code. The repo path is the fact that
    matters: ``mm_dashboard`` ships in the stock tree and has no writable copy,
    which is exactly the file a WRITABLE-only search could never find.
    """
    stock = AGENTS / "market_making_expert/routines/mm_dashboard.py"

    assert stock.exists()
    assert not (
        Path(".condor/agents/market_making_expert/routines") / "mm_dashboard.py"
    ).exists()


def test_both_layers_are_searched_in_read_order():
    import mcp_servers.condor.tools.routines as routines_mod
    from routines.base import assistant_routines_dirs

    source = inspect.getsource(routines_mod._resolve_with_owner)

    assert "assistant_routines_dirs" in source
    assert "for routines_dir in" in source
    assert len(assistant_routines_dirs("market_making_expert")) >= 1


# ── SS-4: the connector guide points at tools the seat has ──────────────────

CONNECTORS = AGENTS / "_shared/skills/verify_connector_support/SKILL.md"


def test_the_candle_route_is_the_one_every_seat_mounts():
    from mcp_servers.hummingbot_api.profiles import PROFILE_TOOLS

    assert "get_market_data" in PROFILE_TOOLS["tick"]

    body = CONNECTORS.read_text()
    assert 'get_market_data(action="connectors")' in body


def test_run_code_is_not_presented_as_the_only_way():
    """A dry run refuses it and a constrained seat may not mount it, so a
    playbook naming it as the only road sends those seats into a refusal."""
    body = CONNECTORS.read_text()

    assert "the only way candles are read" not in body
    assert "NOT the" in body


def test_the_owner_only_tool_is_marked_as_such():
    from mcp_servers.hummingbot_api.profiles import PROFILE_TOOLS

    rings = [
        r for r, tools in PROFILE_TOOLS.items() if "manage_gateway_config" in tools
    ]
    assert rings == ["full"]

    body = CONNECTORS.read_text()
    assert "server-owner) seat only" in body


# ── CO-3 / CO-5: routing and the decimal playbook ───────────────────────────


def test_no_playbook_routes_to_an_agent_that_does_not_exist():
    """``executor_manager`` was named as a delegation target in three files."""
    shipped = {
        p.name for p in AGENTS.iterdir() if p.is_dir() and not p.name.startswith("_")
    }
    assert "executor_manager" not in shipped
    assert "adaptive_grid_trader" in shipped

    for path in AGENTS.rglob("*.md"):
        body = " ".join(path.read_text().split())
        if "executor_manager" in body:
            assert (
                "no `executor_manager`" in body or "does not exist" in body
            ), f"{path} names executor_manager without saying it is gone"


def test_the_decimal_playbook_uses_a_tool_that_exists():
    body = (
        AGENTS / "condor/skills/diagnose_solana_token_decimal_mismatch/SKILL.md"
    ).read_text()

    assert 'manage_gateway_config(resource_type="tokens"' in body
    assert "no `GatewayRouter.get_tokens`" in body


def test_it_no_longer_depends_on_a_browser():
    body = (
        AGENTS / "condor/skills/diagnose_solana_token_decimal_mismatch/SKILL.md"
    ).read_text()

    assert "no browser" in " ".join(body.split())
    assert "explore_geckoterminal" in body


def test_it_says_which_seat_can_apply_the_fix():
    body = (
        AGENTS / "condor/skills/diagnose_solana_token_decimal_mismatch/SKILL.md"
    ).read_text()

    assert "owner-seat" in body.lower()
