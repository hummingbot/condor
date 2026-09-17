"""Condor Vaults, the `~/condor` hooks.

A vault run is an ordinary strategy run whose config carries two extra things:
a ``system_prompt`` (the vault's sweep rule) and a public ``vault`` block.
These pin what each one does to the surfaces the run writes:

1. ``system_prompt`` rides as a true ACP system prompt and never renders into
   the tick prompt; a non-ACP model is refused at start rather than silently
   dropping it.
2. ``vault`` reaches the hummingbot MCP subprocess as ``--vault-json`` with the
   session dir appended, and ``get_info`` names the vault so the dashboard can
   label the run.

A vault's parameters are private, not confidential: they live server-side and
the chain carries their hash (docs/plans/PRIVATE_VAULT_CONFIGS.md in
condor-app). There is nothing for this process to withhold from its own
artefacts, so the redaction hooks this file used to pin are gone.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

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


