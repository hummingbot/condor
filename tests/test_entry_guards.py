"""Spec-declared entry guards: per-agent hard rules on the create path.

The stop_loss_cooldown guard is memecoin_trender's falling-knife fix — it
applies only to agents that declare it in default_config.entry_guards, and an
unknown guard name blocks loudly (a spec typo must not silently disable a
financial protection).
"""

import time
from decimal import Decimal
from types import SimpleNamespace

from condor.agents import agent as agent_module
from condor.executors.base import ExecutorStatus
from condor.executors.guards import entry_guard_reason
from condor.executors.log import ExecutorLog
from condor.executors.position import (
    PositionExecutor,
    PositionSpotConfig,
    PositionStates,
)

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"


def _write_spec(root, slug, entry_guards_yaml=""):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(
        "---\n"
        f"name: {slug}\n"
        "agent_key: claude-code\n"
        "default_config:\n"
        "  frequency_sec: 60\n"
        f"{entry_guards_yaml}"
        "---\n\nbody\n"
    )


def _guarded_spec(root, slug, hours=4):
    _write_spec(
        root,
        slug,
        f"  entry_guards:\n    stop_loss_cooldown:\n      hours: {hours}\n",
    )


def _closed(store, slug, mint, opened_at, close_type="stop_loss"):
    ex = PositionExecutor(
        f"pos_{mint}_{int(opened_at)}",
        PositionSpotConfig(
            chain_network="solana-mainnet-beta", wallet_address=WALLET,
            base_token=mint, quote_token="SOL", amount_quote=Decimal("0.03"),
            agent_slug=slug, agent_id=f"{slug}_1"),
        SimpleNamespace(), store)
    ex.status = ExecutorStatus.CLOSED
    ex.state.state = PositionStates.COMPLETE
    ex.state.close_type = close_type
    ex.state.opened_at = opened_at
    store.save(ex)


def _reason(slug, mint, store):
    return entry_guard_reason(slug, "position_spot", {"base_token": mint}, store=store)


def test_recent_stop_blocks_reentry_when_declared(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _guarded_spec(tmp_path, "mm")
    store = ExecutorLog(tmp_path)
    _closed(store, "mm", "MINT_A", time.time() - 60)  # 1 min ago
    assert "cooldown blacklist" in _reason("mm", "MINT_A", store)


def test_undeclared_agent_is_unguarded(tmp_path, monkeypatch):
    # Same recent stop, but the spec declares no entry_guards → no block.
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_spec(tmp_path, "mm")
    store = ExecutorLog(tmp_path)
    _closed(store, "mm", "MINT_A", time.time() - 60)
    assert _reason("mm", "MINT_A", store) is None


def test_old_stop_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _guarded_spec(tmp_path, "mm", hours=4)
    store = ExecutorLog(tmp_path)
    _closed(store, "mm", "MINT_A", time.time() - 5 * 3600)  # 5h ago
    assert _reason("mm", "MINT_A", store) is None


def test_take_profit_is_not_blacklisted(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _guarded_spec(tmp_path, "mm")
    store = ExecutorLog(tmp_path)
    _closed(store, "mm", "MINT_A", time.time() - 60, close_type="take_profit")
    assert _reason("mm", "MINT_A", store) is None


def test_untouched_token_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _guarded_spec(tmp_path, "mm")
    store = ExecutorLog(tmp_path)
    _closed(store, "mm", "MINT_A", time.time() - 60)
    assert _reason("mm", "MINT_B", store) is None


def test_other_agents_stop_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _guarded_spec(tmp_path, "mm")
    store = ExecutorLog(tmp_path)
    _closed(store, "other_agent", "MINT_A", time.time() - 60)
    assert _reason("mm", "MINT_A", store) is None


def test_guard_only_gates_its_executor_type(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _guarded_spec(tmp_path, "mm")
    store = ExecutorLog(tmp_path)
    _closed(store, "mm", "MINT_A", time.time() - 60)
    assert (
        entry_guard_reason("mm", "grid", {"base_token": "MINT_A"}, store=store)
        is None
    )


def test_unknown_guard_name_blocks_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_spec(tmp_path, "mm", "  entry_guards:\n    no_such_guard: {}\n")
    store = ExecutorLog(tmp_path)
    reason = _reason("mm", "MINT_A", store)
    assert reason is not None and "unknown entry guard" in reason


def test_manual_creates_have_no_spec_and_no_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    store = ExecutorLog(tmp_path)
    assert entry_guard_reason("", "position_spot", {"base_token": "X"}, store=store) is None
    assert _reason("ghost_agent", "X", store) is None  # no AGENT.md → unguarded
