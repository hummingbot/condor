"""Chat-driven loop edits are confirmed with a diff (FEAT-192).

``manage_loops(action="update")`` used to write straight to the store with no
human in the loop at all and no way to see what would change — these pin the
gate (``condor/runtime/danger.py``), the diff it shows
(``condor/agents/config.py``'s ``describe_loop_update``/``humanize_config_value``)
and the detail builder that wires the two together
(``condor/runtime/confirmations.py``'s ``_loop_update_detail``).
"""

from __future__ import annotations

import asyncio

import pytest

from condor.agents.config import describe_loop_update, humanize_config_value
from condor.agents.strategy import Strategy, StrategyStore
from condor.runtime import confirmations as confirmations_module
from condor.runtime import danger
from condor.runtime.confirmations import ConfirmationRegistry, build_permission_callback
from condor.runtime.danger import (
    DANGEROUS_LOOP_ACTIONS,
    MUTATING_LOOP_ACTIONS,
    READ_ONLY_LOOP_ACTIONS,
    is_dangerous_tool_call,
    is_mutating_tool_call,
)

# ── The diff itself ──


def test_describe_loop_update_reports_changed_scalars():
    current = {"name": "Momentum", "description": "old", "agent_key": "claude-code"}
    incoming = {"description": "new", "agent_key": "claude-code"}

    lines = describe_loop_update(current, incoming)

    assert lines == ["description: old → new"]


def test_describe_loop_update_names_an_instructions_change_without_the_body():
    lines = describe_loop_update(
        {"instructions": "old playbook"}, {"instructions": "new playbook"}
    )

    assert lines == ["instructions: (playbook text changed)"]
    assert "old playbook" not in lines[0]
    assert "new playbook" not in lines[0]


def test_describe_loop_update_reports_skill_list_changes():
    lines = describe_loop_update(
        {"skills": ["backtesting"]}, {"skills": ["backtesting", "lp_range_config"]}
    )

    assert lines == ["skills: ['backtesting'] → ['backtesting', 'lp_range_config']"]


def test_describe_loop_update_translates_cadence_and_budget_units():
    lines = describe_loop_update(
        {"config": {"frequency_sec": 60, "total_amount_quote": 100.0}},
        {"config": {"frequency_sec": 1800, "total_amount_quote": 500.0}},
    )

    assert "frequency_sec: 60s (1m) → 1800s (30m)" in lines
    assert "total_amount_quote: 100 quote → 500 quote" in lines


def test_describe_loop_update_diffs_risk_limits_per_key_dotted():
    lines = describe_loop_update(
        {"config": {"risk_limits": {"max_leverage": 5.0, "max_open_executors": 5}}},
        {"config": {"risk_limits": {"max_leverage": 20.0, "max_open_executors": 5}}},
    )

    # No modeled unit for a bare multiplier, but still trimmed of ``.0``.
    assert lines == ["risk_limits.max_leverage: 5 → 20"]


def test_describe_loop_update_translates_a_risk_limit_denominated_in_quote():
    lines = describe_loop_update(
        {"config": {"risk_limits": {"max_position_size_quote": 500.0}}},
        {"config": {"risk_limits": {"max_position_size_quote": 1000.0}}},
    )

    assert lines == ["risk_limits.max_position_size_quote: 500 quote → 1000 quote"]


def test_describe_loop_update_reports_a_full_replace_as_removed_keys():
    """A key ``current`` has and ``incoming`` omits reads as a real deletion —
    only `_diff_config_values` is pinned here; production callers merge first."""
    lines = describe_loop_update(
        {"config": {"frequency_sec": 60, "max_ticks": 10}},
        {"config": {"frequency_sec": 60}},
    )

    assert lines == ["max_ticks: 10 → (removed)"]


def test_describe_loop_update_is_empty_when_nothing_differs():
    assert describe_loop_update({"name": "x"}, {"name": "x"}) == []
    assert describe_loop_update({"config": {"a": 1}}, {}) == []


def test_humanize_config_value_units():
    assert humanize_config_value("frequency_sec", 1800) == "1800s (30m)"
    assert humanize_config_value("frequency_sec", 3600) == "3600s (1h)"
    assert humanize_config_value("frequency_sec", 90) == "90s (1m30s)"
    assert humanize_config_value("total_amount_quote", 500) == "500 quote"
    assert humanize_config_value("risk_limits.max_drawdown_pct", -1) == "disabled"
    assert humanize_config_value("risk_limits.max_drawdown_pct", 5) == "5%"
    assert humanize_config_value("name", "Momentum") == "Momentum"
    assert humanize_config_value("agent_key", None) == "(not set)"


# ── The gate: manage_loops(update) is the one action that needs a human ──


def test_manage_loops_update_requires_confirmation():
    for action in ("update", "update_strategy"):
        call = {"tool": "manage_loops", "input": {"action": action, "loop_id": "a.b"}}
        assert is_dangerous_tool_call(call), f"manage_loops({action}) was not gated"


def test_manage_loops_reads_and_create_are_not_gated():
    for action in ("list", "get", "create"):
        call = {"tool": "manage_loops", "input": {"action": action, "loop_id": "a.b"}}
        assert not is_dangerous_tool_call(
            call
        ), f"manage_loops({action}) needlessly gated"


def test_manage_loops_fails_closed_on_an_unreadable_action():
    for bad in ({}, {"action": None}, {"action": ""}, None):
        assert is_dangerous_tool_call(
            {"tool": "manage_loops", "input": bad}
        ), f"{bad!r} slipped past the manage_loops gate"


def test_manage_loops_update_is_recorded():
    call = {"tool": "manage_loops", "input": {"action": "update", "loop_id": "a.b"}}
    assert is_mutating_tool_call(call)


def test_manage_loops_reads_are_not_recorded():
    for action in ("list", "get"):
        call = {"tool": "manage_loops", "input": {"action": action}}
        assert not is_mutating_tool_call(call)


def test_manage_loops_create_and_delete_are_ungated_but_recorded():
    """Ungated (loop_builder already walks create; delete is CORR-1134's concern) —
    but still logged, same as the other dispatch tools' ungated writes."""
    for action in ("create", "delete"):
        call = {"tool": "manage_loops", "input": {"action": action, "loop_id": "a.b"}}
        assert not is_dangerous_tool_call(call)
        assert is_mutating_tool_call(call)


def test_manage_loops_action_sets_do_not_overlap():
    assert DANGEROUS_LOOP_ACTIONS <= MUTATING_LOOP_ACTIONS
    assert MUTATING_LOOP_ACTIONS.isdisjoint(READ_ONLY_LOOP_ACTIONS)


def test_format_tool_summary_names_the_loop_being_updated():
    summary = danger.format_tool_summary(
        {"tool": "manage_loops", "input": {"action": "update", "loop_id": "mm.grid"}}
    )
    assert "mm.grid" in summary
    assert summary != "manage_loops"


def test_format_tool_summary_for_an_ungated_action_still_names_the_loop():
    summary = danger.format_tool_summary(
        {"tool": "manage_loops", "input": {"action": "get", "loop_id": "mm.grid"}}
    )
    assert "mm.grid" in summary


# ── The detail: the diff a human actually sees in the prompt ──


def _update_call(**extra):
    return {
        "tool": "manage_loops",
        "input": {"action": "update", "loop_id": "mm.grid", **extra},
    }


def test_loop_update_detail_is_empty_for_a_non_update_action():
    assert (
        confirmations_module._loop_update_detail(
            {"tool": "manage_loops", "input": {"action": "get", "loop_id": "mm.grid"}}
        )
        == ""
    )


def test_loop_update_detail_is_empty_for_another_tool():
    assert (
        confirmations_module._loop_update_detail({"tool": "place_order", "input": {}})
        == ""
    )


def test_loop_update_detail_reports_unknown_loop(monkeypatch):
    monkeypatch.setattr(StrategyStore, "get_by_key", lambda self, key: None)

    detail = confirmations_module._loop_update_detail(_update_call())

    assert "mm.grid" in detail
    assert "not found" in detail


def test_loop_update_detail_reports_no_change(monkeypatch):
    strategy = Strategy(
        agent_slug="mm", name="Grid", default_config={"frequency_sec": 60}
    )
    monkeypatch.setattr(StrategyStore, "get_by_key", lambda self, key: strategy)

    detail = confirmations_module._loop_update_detail(
        _update_call(config={"frequency_sec": 60})
    )

    assert "No field" in detail


def test_loop_update_detail_shows_the_translated_diff(monkeypatch):
    strategy = Strategy(
        agent_slug="mm",
        name="Grid",
        default_config={"frequency_sec": 60, "total_amount_quote": 100.0},
    )
    monkeypatch.setattr(StrategyStore, "get_by_key", lambda self, key: strategy)

    detail = confirmations_module._loop_update_detail(
        _update_call(config={"frequency_sec": 1800, "total_amount_quote": 500.0})
    )

    assert "frequency_sec: 60s (1m) → 1800s (30m)" in detail
    assert "total_amount_quote: 100 quote → 500 quote" in detail


def test_loop_update_detail_ignores_an_empty_config_no_op(monkeypatch):
    """``update_strategy`` treats an empty ``config`` dict as a no-op (explore note)."""
    strategy = Strategy(
        agent_slug="mm", name="Grid", default_config={"frequency_sec": 60}
    )
    monkeypatch.setattr(StrategyStore, "get_by_key", lambda self, key: strategy)

    detail = confirmations_module._loop_update_detail(_update_call(config={}))

    assert "No field" in detail


# ── End to end: the permission callback shows the diff and waits for a human ──

OPTIONS = [
    {"optionId": "allow", "kind": "allow_once"},
    {"optionId": "deny", "kind": "reject_once"},
]


class _RecordingChannel:
    def __init__(self):
        self.delivered = []

    async def deliver(self, pending):
        self.delivered.append(pending)


@pytest.fixture
def registry(monkeypatch):
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


def _ask(registry, tool_call):
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(tool_call, OPTIONS))
        for _ in range(200):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        pending = channel.delivered[0]
        await registry.resolve(pending.id, approved=False, by_user_id=1)
        await task
        return pending

    return asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_a_loop_update_pauses_for_confirmation_with_the_diff(registry, monkeypatch):
    strategy = Strategy(
        agent_slug="mm", name="Grid", default_config={"frequency_sec": 60}
    )
    monkeypatch.setattr(StrategyStore, "get_by_key", lambda self, key: strategy)

    pending = _ask(registry, _update_call(config={"frequency_sec": 1800}))

    assert "mm.grid" in pending.summary
    assert "frequency_sec: 60s (1m) → 1800s (30m)" in pending.detail


def test_a_loop_read_is_never_put_in_front_of_a_human():
    call = {"tool": "manage_loops", "input": {"action": "get", "loop_id": "mm.grid"}}
    assert not is_dangerous_tool_call(call)
