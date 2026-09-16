"""Condor Vaults, the three `~/condor` hooks (plan §6).

A vault run is an ordinary strategy run whose config carries three extra
things: a ``system_prompt`` (the vault's sweep rule plus the manager's private
context), a ``confidential`` list naming the tunables the manager keeps private,
and a public ``vault`` block. These pin what each one does to the surfaces the
run writes:

1. ``system_prompt`` rides as a true ACP system prompt and never renders into
   the tick prompt; a non-ACP model is refused at start rather than silently
   dropping it.
2. ``confidential`` keys render into [CURRENT CONFIG] for the tick but are
   omitted from the session's ``config.yml`` and redacted out of every
   snapshot file and out of the dashboard's snapshot read.
3. ``vault`` reaches the hummingbot MCP subprocess as ``--vault-json`` with the
   session dir appended, and ``get_info`` names the vault so the dashboard can
   label the run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from condor.agents.config import (
    CONFIDENTIAL_MARK,
    confidential_keys,
    persistable_config,
    redact_confidential,
    redact_verbatim,
)
from condor.agents.journal import JournalManager, save_experiment_snapshot
from condor.agents.prompts import build_tick_prompt
from condor.runtime.toolsets import _hummingbot_mcp_args
from mcp_servers.hummingbot_api.vault_block import (
    decode_vault_block,
    encode_vault_block,
    validate_vault_block,
)

VAULT = {
    "slug": "lp-referencer-1",
    "mint": "Mint111111111111111111111111111111111111111",
    "pool": "Pool11111111111111111111111111111111111111",
    "run_id": "run-42",
    "swig_wallet": "Swig11111111111111111111111111111111111111",
    "buyback_bps": 2500,
}

CONFIG = {
    "execution_mode": "loop",
    "agent_key": "claude-acp:sonnet",
    "wallet_address": VAULT["swig_wallet"],
    "range_width_pct": 5,
    "min_tvl_usd": 100000,
    "confidential": ["range_width_pct"],
    "system_prompt": "You are running Condor Vault lp-referencer-1. Sweep after every close.",
    "vault": VAULT,
}


def _engine(tmp_path, monkeypatch, config: dict):
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    strategy = Strategy(agent_slug="lp_referencer", name="Referencer")
    strategy.home.mkdir(parents=True, exist_ok=True)
    agent = Agent(slug="lp_referencer", name="LP Referencer")
    return TickEngine(
        agent=agent, strategy=strategy, config=dict(config), chat_id=0, user_id=1
    )


# ── the helpers ──


def test_confidential_keys_always_include_the_system_prompt():
    assert confidential_keys({}) == ("system_prompt",)
    assert confidential_keys({"confidential": ["a", "b"]}) == (
        "system_prompt",
        "a",
        "b",
    )


def test_a_malformed_confidential_list_is_refused_not_ignored():
    with pytest.raises(ValueError, match="confidential"):
        confidential_keys({"confidential": "range_width_pct"})
    with pytest.raises(ValueError):
        confidential_keys({"confidential": [1]})


def test_persistable_config_drops_the_values_and_keeps_the_key_list():
    out = persistable_config(CONFIG)
    assert "range_width_pct" not in out
    assert "system_prompt" not in out
    assert out["confidential"] == ["range_width_pct"]
    assert out["min_tvl_usd"] == 100000
    assert out["vault"] == VAULT


def test_redaction_replaces_the_rendered_line_only():
    text = "range_width_pct: 5\nrange_width_pct_floor: 1\nmin_tvl_usd: 100000\n"
    out = redact_confidential(text, ["range_width_pct"])
    assert out == (
        f"range_width_pct: {CONFIDENTIAL_MARK}\n"
        "range_width_pct_floor: 1\nmin_tvl_usd: 100000\n"
    )


# ── hook 1: the system prompt ──


def test_the_system_prompt_never_renders_into_the_tick_prompt(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, CONFIG)
    prompt = build_tick_prompt(
        agent=engine.agent,
        strategy=engine.strategy,
        config=engine.config,
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
    )
    assert "system_prompt" not in prompt
    assert "Sweep after every close" not in prompt
    # The confidential tunable DOES reach the tick: the model needs the value.
    assert "range_width_pct: 5" in prompt


def test_a_non_acp_model_with_a_system_prompt_is_refused_at_start(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="system_prompt"):
        _engine(tmp_path, monkeypatch, {**CONFIG, "agent_key": "openrouter:x/y"})


def test_the_system_prompt_reaches_the_llm_client(tmp_path, monkeypatch):
    import condor.runtime.llm_client as llm_client_module
    from condor.agents import engine as engine_module
    from condor.agents.risk import RiskState

    engine = _engine(tmp_path, monkeypatch, CONFIG)
    seen: dict = {}

    monkeypatch.setattr(
        engine_module.toolsets,
        "build_mcp_servers_for_session",
        lambda *a, **k: seen.setdefault("servers", k) and [],
    )

    def fake_build(agent_key, **kwargs):
        seen["build"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(llm_client_module, "build_llm_client", fake_build)

    import asyncio

    asyncio.run(engine._create_client(RiskState(), None))
    assert seen["build"]["system_prompt"] == CONFIG["system_prompt"]
    # Hook 3 rides the same call: the vault block, with the session dir added.
    assert seen["servers"]["vault"] == {**VAULT, "session_dir": str(engine.session_dir)}


# ── hook 2: confidential keys ──


def test_the_session_config_is_persisted_without_the_confidential_values(
    tmp_path, monkeypatch
):
    engine = _engine(tmp_path, monkeypatch, CONFIG)
    saved = yaml.safe_load((engine.session_dir / "config.yml").read_text())
    assert "range_width_pct" not in saved
    assert "system_prompt" not in saved
    assert saved["confidential"] == ["range_width_pct"]
    assert saved["vault"]["slug"] == VAULT["slug"]
    # And the running engine still holds the values for the tick.
    assert engine.config["range_width_pct"] == 5
    assert engine._confidential == ("system_prompt", "range_width_pct")


def test_the_session_snapshot_redacts_the_confidential_line(tmp_path):
    journal = JournalManager("lp_referencer.referencer_1", session_dir=tmp_path)
    path = journal.save_full_snapshot(
        tick=1,
        timestamp="now",
        system_prompt="[CURRENT CONFIG]\nrange_width_pct: 5\nmin_tvl_usd: 100000\n",
        response_text="ok",
        tool_calls=[],
        executors_data="",
        risk_state={},
        duration=1.0,
        confidential=("system_prompt", "range_width_pct"),
    )
    text = path.read_text()
    assert f"range_width_pct: {CONFIDENTIAL_MARK}" in text
    assert "range_width_pct: 5" not in text
    assert "min_tvl_usd: 100000" in text


def test_the_experiment_snapshot_redacts_the_confidential_line(tmp_path):
    path = save_experiment_snapshot(
        agent_dir=tmp_path,
        experiment_num=1,
        execution_mode="dry_run",
        timestamp="now",
        system_prompt="range_width_pct: 5\n",
        response_text="",
        tool_calls=[],
        executors_data="",
        risk_state={},
        duration=0.1,
        confidential=("range_width_pct",),
    )
    assert "range_width_pct: 5" not in path.read_text()
    assert CONFIDENTIAL_MARK in path.read_text()


def test_the_dashboard_snapshot_read_redacts_by_key_name(tmp_path, monkeypatch):
    """A snapshot written in clear (an older writer, a hand edit) still comes
    out of the endpoint redacted, keyed off the session's saved config."""
    import asyncio

    from condor.web.routes import agents as routes

    engine = _engine(tmp_path, monkeypatch, CONFIG)
    snap_dir = engine.session_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "snapshot_3.md").write_text("range_width_pct: 5\nmin_tvl_usd: 1\n")

    monkeypatch.setattr(routes, "_get_strategy", lambda slug, sslug: engine.strategy)
    result = asyncio.run(
        routes.get_snapshot(
            "lp_referencer", "referencer", engine.session_num, 3, user=None
        )
    )
    assert (
        result["content"] == f"range_width_pct: {CONFIDENTIAL_MARK}\nmin_tvl_usd: 1\n"
    )


# ── hook 3: the vault block ──


def test_the_vault_block_travels_on_argv_and_only_when_present():
    plain = _hummingbot_mcp_args({"host": "h", "port": 1}, "srv", "tick")
    assert "--vault-json" not in plain

    args = _hummingbot_mcp_args({"host": "h", "port": 1}, "srv", "tick", vault=VAULT)
    raw = args[args.index("--vault-json") + 1]
    assert all(isinstance(a, str) for a in args)
    assert json.loads(raw) == VAULT
    assert decode_vault_block(raw) == VAULT
    # The block sits before the bot marker, after the profile and the mutes.
    assert args.index("--profile") < args.index("--vault-json")


def test_a_vault_block_outside_its_shape_is_refused():
    with pytest.raises(ValueError, match="missing"):
        validate_vault_block({k: v for k, v in VAULT.items() if k != "mint"})
    with pytest.raises(ValueError, match="does not define"):
        validate_vault_block({**VAULT, "secret_key": "x"})
    with pytest.raises(ValueError, match="buyback_bps"):
        validate_vault_block({**VAULT, "buyback_bps": 10001})
    with pytest.raises(ValueError, match="buyback_bps"):
        validate_vault_block({**VAULT, "buyback_bps": "2500"})
    assert validate_vault_block({**VAULT, "buyback_bps": 0}) is not None
    assert encode_vault_block(VAULT) == json.dumps(
        VAULT, sort_keys=True, separators=(",", ":")
    )


def test_a_malformed_vault_block_fails_the_start(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="vault block"):
        _engine(tmp_path, monkeypatch, {**CONFIG, "vault": {"slug": "only"}})


def test_get_info_names_the_vault(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, CONFIG)
    info = engine.get_info()
    assert info["vault_slug"] == VAULT["slug"]
    assert info["vault_run_id"] == VAULT["run_id"]
    assert "system_prompt" not in json.dumps(info)
    assert "range_width_pct" not in json.dumps(info)

    plain = _engine(tmp_path, monkeypatch, {"execution_mode": "loop"})
    assert plain.get_info()["vault_slug"] == ""


def test_an_experiment_passes_the_block_without_a_session_dir(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, {**CONFIG, "execution_mode": "dry_run"})
    assert engine._vault_block() == VAULT


# ── hook 1, second half: a quoted-back system prompt is scrubbed ──


def test_verbatim_system_prompt_lines_are_scrubbed_from_a_snapshot(tmp_path):
    prompt = "You are running Condor Vault x. Sweep after every close.\nNever call it."
    journal = JournalManager("lp_referencer.referencer_1", session_dir=tmp_path)
    path = journal.save_full_snapshot(
        tick=1,
        timestamp="now",
        system_prompt="[CURRENT CONFIG]\nfoo: 1\n",
        response_text=(
            "The system prompt said: You are running Condor Vault x. Sweep after every close."
        ),
        tool_calls=[
            {"name": "manage_memory", "input": {"content": "Never call it."}}
        ],
        executors_data="",
        risk_state={},
        duration=1.0,
        secrets=(prompt,),
    )
    text = path.read_text()
    assert "Sweep after every close" not in text
    assert f"The system prompt said: {CONFIDENTIAL_MARK}" in text
    # A short line is left alone: scrubbing it would hit ordinary prose.
    assert "Never call it." in text


def test_redact_verbatim_ignores_empty_and_short_lines():
    assert redact_verbatim("hello world", ("", "hello", "   ")) == "hello world"
