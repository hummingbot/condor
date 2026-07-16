"""Phase 6 acceptance (§11): every SHIPPED AgentSpec parses, validates,
freezes, and renders a tick prompt against the post-Phase-6 tool surface
(no spec references deleted routines/tools); grep gate — no dead-layer
references left in live code, with an explicit allowlist for the files the
pending auth-deletion pass owns."""

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
    "create_agent", "update_agent", "delete_agent", "run_agent", "get_run",
    "get_agent", "list_agents", "list_runs", "control_run", "shutdown_agent",
    "consult", "delegate", "resolve_approval", "list_approvals",
    "get_notifications", "manage_executors", "manage_memory", "manage_skill",
    "manage_routines", "send_notification",
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
        assert short in POST_PHASE6_TOOLS, (
            f"{slug} declares retired/unknown tool {tool!r}"
        )

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
    for dead in ("perp_requote", "trading_agent_journal_write", "manage_bots",
                 "manage_trading_agent", "hummingbot"):
        assert dead not in prompt, f"{slug} tick prompt references {dead!r}"


# ---------------------------------------------------------------------------
# Grep gate (§11 Phase 6)
# ---------------------------------------------------------------------------

# Files the PENDING auth-deletion pass owns (blocked awaiting operator
# approval): login/JWT and the user-role remnant live only here. When the
# auth pass lands, empty this set — the gate then tightens automatically.
_AUTH_PASS_ALLOWLIST = {
    "condor/web/auth.py",
    "condor/web/routes/auth.py",
    "condor/web/routes/chat_ws.py",
    "condor/web/routes/ws.py",
    "condor/web/ws_manager.py",
    "condor/web/models.py",
    "condor/web/routes/agents.py",   # chat_id doc comment only
    "condor/web/routes/routines.py",  # get_current_user dependency
    "condor/web/routes/reports.py",
    "condor/web/routes/settings.py",
    "condor/web/routes/native_executors.py",
    "condor/web/routes/venues.py",
    "condor/web/routes/notifications.py",
    "config_manager.py",
    "condor/preferences.py",
    "condor/cli.py",  # login-token + ADMIN_USER_ID (auth pass)
    "utils/config.py",
}

_GATE_PATTERNS = (
    "telegram", "hummingbot_api", "get_bots_client", "bot_name",
    "server_required", "active_server", "config_manager",
    "ServerDataService", "jose", "whisper", "transcribe",
    "VoiceSettings", "getVoiceSettings", "created_by",
)

# Word-ish matches that are NOT the dead layers.
_FALSE_POSITIVES = re.compile(
    r"TELEGRAM_.*deny|_DENY_PREFIXES|scrubs? the secret|test_", re.IGNORECASE
)


def test_grep_gate_no_dead_layer_references():
    out = subprocess.run(
        ["git", "grep", "-n", "-i", "-E", "|".join(_GATE_PATTERNS), "--",
         "condor", "mcp_servers", "utils", "routines", "agents/*/AGENT.md",
         ":!*.jsonl"],
        capture_output=True, text=True, cwd=REPO,
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
