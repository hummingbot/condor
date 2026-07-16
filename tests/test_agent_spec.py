"""Phase 2 acceptance: AgentSpec hashes, canonicalization, stricter-only risk,
schedule schema, denomination rule (simplification plan §5.3/§5.4/§11)."""

import pytest

from condor.accounts.model import AccountRef
from condor.agents.agent import Agent
from condor.agents.spec import (
    SpecValidationError,
    canonical_json,
    freeze_spec,
    merge_risk_stricter,
    resolved_spec_hash,
    source_spec_hash,
    validate_agent_spec,
    validate_cron,
    validate_schedule,
)


def _agent(**kw):
    base = dict(
        slug="t",
        name="T",
        description="d",
        instructions="body",
        agent_key="claude-code",
        risk_limits={"max_position_size_quote": 100, "max_open_executors": 3},
        denomination="USDC",
        default_config={"frequency_sec": 60},
    )
    base.update(kw)
    return Agent(**base)


# -- canonicalization --


def test_canonical_json_key_order_and_number_forms_hash_identically():
    a = {"b": 1, "a": {"y": 500.0, "x": [1, 2]}}
    b = {"a": {"x": [1, 2], "y": 500}, "b": 1.0}
    assert canonical_json(a) == canonical_json(b)
    assert resolved_spec_hash(a) == resolved_spec_hash(b)


def test_canonical_json_omits_nulls_and_normalizes_decimals():
    assert canonical_json({"a": None, "b": 0.10}) == canonical_json({"b": "0.1"}) or (
        canonical_json({"a": None, "b": 0.10}) == '{"b":"0.1"}'
    )
    # bools are not numbers
    assert '"c":true' in canonical_json({"c": True})


def test_explicit_vs_implicit_defaults_hash_identically():
    """Freezing the same agent with an explicitly-passed default equals the
    implicit default (normalize_config materializes schema defaults)."""
    from condor.agents.config import normalize_config

    agent = _agent()
    cfg_implicit = normalize_config(dict(agent.default_config))
    cfg_explicit = normalize_config({**agent.default_config, "max_ticks": 0})
    f1 = freeze_spec(agent, cfg_implicit, source_text="X")
    f2 = freeze_spec(agent, cfg_explicit, source_text="X")
    assert f1.resolved_hash == f2.resolved_hash


# -- two hashes --


def test_source_and_resolved_hashes_are_independent():
    agent = _agent()
    f = freeze_spec(agent, {"frequency_sec": 60}, source_text="AUTHORED BYTES")
    assert f.source_hash == source_spec_hash("AUTHORED BYTES")
    assert f.resolved_hash and f.resolved_hash != f.source_hash


def test_account_change_alters_only_resolved_hash():
    """The §5.3 default_account rule: same authored spec, different resolved
    account → source hash unchanged, resolved hash changes."""
    agent = _agent()
    src = "SAME AUTHORED TEXT"
    f_main = freeze_spec(
        agent,
        {"frequency_sec": 60},
        source_text=src,
        account_ref=AccountRef("hyperliquid", "0x" + "a" * 40),
    )
    f_alt = freeze_spec(
        agent,
        {"frequency_sec": 60},
        source_text=src,
        account_ref=AccountRef("hyperliquid", "0x" + "b" * 40),
    )
    assert f_main.source_hash == f_alt.source_hash
    assert f_main.resolved_hash != f_alt.resolved_hash


# -- stricter-only risk overrides --


def test_widening_risk_override_rejected():
    base = {"max_position_size_quote": 100, "max_open_executors": 3}
    with pytest.raises(SpecValidationError, match="widens max_position_size_quote"):
        merge_risk_stricter(base, {"max_position_size_quote": 200})
    with pytest.raises(SpecValidationError, match="widens max_open_executors"):
        merge_risk_stricter(base, {"max_open_executors": 5})


def test_tightening_risk_override_accepted():
    base = {"max_position_size_quote": 100, "max_open_executors": 3}
    merged = merge_risk_stricter(base, {"max_position_size_quote": 50})
    assert merged["max_position_size_quote"] == 50
    assert merged["max_open_executors"] == 3


def test_drawdown_sentinel_semantics():
    base = {"max_drawdown_pct": -1.0}
    # enabling a disabled drawdown stop is stricter
    assert merge_risk_stricter(base, {"max_drawdown_pct": 10})["max_drawdown_pct"] == 10
    # disabling an enabled one is widening
    with pytest.raises(SpecValidationError):
        merge_risk_stricter({"max_drawdown_pct": 10}, {"max_drawdown_pct": -1})
    # raising the bound is widening
    with pytest.raises(SpecValidationError):
        merge_risk_stricter({"max_drawdown_pct": 10}, {"max_drawdown_pct": 50})


@pytest.mark.asyncio
async def test_start_session_rejects_widening_override(tmp_path, monkeypatch):
    """End-to-end through lifecycle.start_session: a launch override widening
    the AGENT.md baseline is a 422, never a started run."""
    import condor.agents.agent as agent_mod
    from condor.agents.agent import AgentStore
    from condor.agents.lifecycle import LifecycleError, start_session

    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path)
    AgentStore().create(
        name="widen_me",
        instructions="body",
        tools=["manage_executors"],
        risk_limits={"max_position_size_quote": 10, "max_open_executors": 2},
        denomination="USDC",
        default_config={"execution_mode": "loop"},
    )
    with pytest.raises(LifecycleError) as ei:
        await start_session(
            "widen_me",
            config={"risk_limits": {"max_position_size_quote": 1000}},
        )
    assert ei.value.status == 422
    assert "widens" in ei.value.message


@pytest.mark.asyncio
async def test_start_session_rejects_forbidden_launch_overrides(tmp_path, monkeypatch):
    import condor.agents.agent as agent_mod
    from condor.agents.agent import AgentStore
    from condor.agents.lifecycle import LifecycleError, start_session

    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path)
    AgentStore().create(
        name="fixed_spec",
        instructions="body",
        tools=["manage_routines"],
        default_config={"frequency_sec": 60},
    )
    with pytest.raises(LifecycleError, match="forbidden"):
        await start_session("fixed_spec", config={"frequency_sec": 1})
    with pytest.raises(LifecycleError, match="forbidden"):
        await start_session("fixed_spec", config={"agent_key": "other-model"})


# -- schedule schema (§5.4 — schema only in Phase 2) --


def test_valid_cron_and_tz_accepted():
    validate_schedule({"cron": "0 * * * *", "tz": "America/New_York"}, {"max_ticks": 5})
    validate_schedule({"cron": "*/15 9-17 * * 1-5"}, {"max_ticks": 1})


def test_schedule_without_bounded_duration_rejected():
    with pytest.raises(SpecValidationError, match="bounded duration"):
        validate_schedule({"cron": "0 * * * *"}, {"max_ticks": 0})
    with pytest.raises(SpecValidationError, match="bounded duration"):
        validate_schedule({"cron": "0 * * * *"}, {})


def test_bad_cron_and_tz_rejected():
    with pytest.raises(SpecValidationError):
        validate_cron("not a cron")
    with pytest.raises(SpecValidationError):
        validate_cron("99 * * * *")
    with pytest.raises(SpecValidationError, match="IANA"):
        validate_schedule({"cron": "0 * * * *", "tz": "Mars/Olympus"}, {"max_ticks": 1})


def test_agent_save_validates_schedule(tmp_path, monkeypatch):
    import condor.agents.agent as agent_mod
    from condor.agents.agent import AgentStore

    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path)
    with pytest.raises(SpecValidationError, match="bounded duration"):
        AgentStore().create(
            name="sched",
            instructions="x",
            tools=["manage_routines"],  # non-trading scope: no baseline needed
            schedule={"cron": "0 * * * *"},
            default_config={},
        )


# -- denomination (§5.3/§6.1) --


def test_risk_limits_without_denomination_fails_validation():
    agent = _agent(denomination="")
    with pytest.raises(SpecValidationError, match="denomination"):
        validate_agent_spec(agent)


def test_no_risk_limits_needs_no_denomination():
    validate_agent_spec(_agent(risk_limits={}, denomination=""))


def test_agent_account_name_is_persisted_as_canonical_address(tmp_path, monkeypatch):
    import condor.agents.agent as agent_mod
    from condor.agents.agent import AgentStore
    from condor.executors.wallets import account_store

    address = "0x" + "c" * 40
    account_store().upsert_account("hyperliquid", address, {"name": "alt"})
    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path)
    agent = AgentStore().create(
        name="bound",
        instructions="body",
        tools=["manage_executors"],
        risk_limits={"max_position_size_quote": 10, "max_open_executors": 1},
        denomination="USDC",
        account="alt",
        default_config={"venue": "hyperliquid"},
    )
    assert agent.account == address
    assert AgentStore().get(agent.slug).account == address
    assert "account: alt\n" not in agent.path.read_text()
