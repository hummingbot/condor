"""Phase 6 acceptance (§11): every SHIPPED AgentSpec parses, validates,
freezes, and renders a tick prompt against the post-Phase-6 tool surface
(no spec references deleted routines/tools); grep gate — no dead-layer
references left in live code, with an explicit allowlist for the files the
pending auth-deletion pass owns."""

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

SHIPPED = sorted(
    p.parent.name
    for p in (REPO / "agents").glob("*/AGENT.md")
    if not p.parent.name.startswith("_")
)

# Tool surface after Phase 5 (§8) — what a spec may declare.
POST_PHASE6_TOOLS = {
    "create_agent",
    "update_agent",
    "delete_agent",
    "run_agent",
    "get_run",
    "get_agent",
    "list_agents",
    "list_runs",
    "control_run",
    "complete_run",
    "shutdown_agent",
    "consult",
    "record_learning",
    "resolve_approval",
    "list_approvals",
    "get_notifications",
    "manage_executors",
    "manage_memory",
    "manage_skill",
    "manage_routines",
    "send_notification",
}


def test_shipped_specs_exist():
    assert SHIPPED, "no shipped agents found"


@pytest.mark.parametrize("slug", SHIPPED)
def test_shipped_spec_parses_validates_and_freezes(slug):
    from condor.agents.agent import AgentStore
    from condor.agents.config import normalize_config
    from condor.agents.spec import freeze_spec, validate_agent_spec

    agent = AgentStore().get(slug)
    assert agent is not None, f"AGENT.md for {slug} failed to parse"
    validate_agent_spec(agent)

    # Declared tools must exist on the post-Phase-6 surface.
    for tool in agent.tools or []:
        short = tool.rsplit("__", 1)[-1]
        assert (
            short in POST_PHASE6_TOOLS
        ), f"{slug} declares retired/unknown tool {tool!r}"

    # The launch path freezes without error (schema defaults + baseline).
    defaults = dict(agent.default_config or {})
    if not defaults.get("risk_limits") and agent.risk_limits:
        defaults["risk_limits"] = dict(agent.risk_limits)
    config = normalize_config(defaults)
    frozen = freeze_spec(agent, config, source_text=AgentStore().source_text(slug))
    assert frozen.source_hash and frozen.resolved_hash


@pytest.mark.parametrize("slug", SHIPPED)
def test_shipped_spec_renders_tick_prompt(slug):
    """The dry-run surface: the tick prompt builds against the current tool
    surface and references no deleted routine/tool names."""
    from condor.agents.agent import AgentStore
    from condor.agents.config import normalize_config
    from condor.agents.prompts import build_tick_prompt

    agent = AgentStore().get(slug)
    config = normalize_config(dict(agent.default_config or {}))
    config["execution_mode"] = "experiment"
    prompt = build_tick_prompt(
        agent=agent,
        config=config,
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        tick_number=1,
        agent_id="01JZX5B7Q2K4N8P1T3V5W7Y9ZB",
    )
    for dead in (
        "perp_requote",
        "trading_agent_journal_write",
        "manage_bots",
        "manage_trading_agent",
        "hummingbot",
    ):
        assert dead not in prompt, f"{slug} tick prompt references {dead!r}"


def test_real_experiment_tick_completes_without_venue_calls(tmp_path, monkeypatch):
    """The acceptance dry run is the real in-memory experiment path, not
    prompt rendering.

    The model boundary is stubbed, while the real launch-config resolution,
    provider pass, and experiment risk gate execute. An experiment must
    return its report and leave NOTHING on disk — no run, no folder.
    """
    from condor.agents import agent as agent_module
    from condor.agents import run as run_module
    from condor.agents.experiment import run_experiment
    from condor.agents.run import RunResult
    from condor.agents.runstore import RunStore, set_run_store
    from condor.executors.runtime import ExecutorRuntime

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    set_run_store(RunStore(root=tmp_path / "agents"))
    agent_dir = agent_module._DATA_ROOT / "dry_watcher"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\n"
        "name: Dry Watcher\n"
        "agent_key: claude-code\n"
        "tools: [manage_executors]\n"
        "risk_limits:\n"
        "  max_position_size_quote: 10\n"
        "  max_open_executors: 1\n"
        "denomination: USDC\n"
        "default_config:\n"
        "  venue: solana\n"
        "  execution_mode: loop\n"
        "---\n\nObserve once and report without trading.\n"
    )

    venue_calls = 0

    def forbidden_connector(*args, **kwargs):
        nonlocal venue_calls
        venue_calls += 1
        raise AssertionError("experiment tick reached a venue connector")

    monkeypatch.setattr(ExecutorRuntime, "connector_for_spec", forbidden_connector)

    async def fake_model(agent, prompt, **kwargs):
        assert kwargs["execution_mode"] == "experiment"
        assert "EXPERIMENT mode" in prompt
        # A mutating attempt (cancelled by the gate in a live run) plus one
        # read-only call — both must land in the report.
        kwargs["on_tool_call"](
            {
                "name": "manage_executors",
                "status": "failed",
                "input": {"action": "create", "executor_type": "order_spot"},
            }
        )
        kwargs["on_tool_call"](
            {"name": "manage_skill", "status": "completed", "input": {"action": "read"}}
        )
        return RunResult(text="🧪 Would place a 10 USDC order.", model="claude-code")

    monkeypatch.setattr(run_module, "run_agent", fake_model)

    report = asyncio.run(run_experiment("dry_watcher"))

    assert "Would place a 10 USDC order" in report
    assert "manage_executors" in report  # the cancelled would-be action
    assert "manage_skill" in report  # read-only activity is summarized
    assert venue_calls == 0
    # Nothing persisted: no run stream, no kind dir, no companion files.
    agent_entries = {p.name for p in agent_dir.iterdir()}
    assert agent_entries == {"AGENT.md"}
    assert list((tmp_path / "agents").glob("**/*.jsonl")) == []


# ---------------------------------------------------------------------------
# Grep gate (§11 Phase 6)
# ---------------------------------------------------------------------------

# The auth pass (§5.5 final step) landed 2026-07-15: login/JWT, the
# user-role model, and config_manager.py are gone — loopback posture (§5.5)
# is the sole gate. Nothing left to allowlist.
_AUTH_PASS_ALLOWLIST: set[str] = set()

_GATE_PATTERNS = (
    "telegram",
    "hummingbot_api",
    "get_bots_client",
    "bot_name",
    "server_required",
    "active_server",
    "config_manager",
    "ServerDataService",
    "jose",
    "whisper",
    "transcribe",
    "VoiceSettings",
    "getVoiceSettings",
    "created_by",
    "pydantic[-_ ]ai",
    "ADMIN_(USERNAME|PASSWORD|USER_ID)",
    "HB_(USERNAME|PASSWORD)",
)

# Word-ish matches that are NOT the dead layers.
_FALSE_POSITIVES = re.compile(
    r"TELEGRAM_.*deny|_DENY_PREFIXES|scrubs? the secret|test_", re.IGNORECASE
)


def test_grep_gate_no_dead_layer_references():
    out = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-i",
            "-E",
            "|".join(_GATE_PATTERNS),
            "--",
            "condor",
            "mcp_servers",
            "utils",
            "routines",
            "frontend/src",
            "scripts",
            "skills",
            "integrations",
            "agents/*/AGENT.md",
            "Makefile",
            "install.sh",
            "pyproject.toml",
            ".env.example",
            ":!*.jsonl",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    ).stdout.splitlines()

    offenders = []
    for line in out:
        path = line.split(":", 1)[0]
        if path in _AUTH_PASS_ALLOWLIST:
            continue
        if path.startswith("agents/") and "/AGENT.md" not in path:
            continue  # runtime agent state, not live code
        if _FALSE_POSITIVES.search(line):
            continue
        # The routines worker's env scrub deliberately names TELEGRAM_ as a
        # deny prefix; integrations relays only tolerate a legacy field.
        if path in ("condor/routines_worker.py",):
            continue
        offenders.append(line)

    assert not offenders, "dead-layer references in live code:\n" + "\n".join(
        offenders[:40]
    )
