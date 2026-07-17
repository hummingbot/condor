"""Unit tests for DELEGATE -- fire-and-forget background agent tasks.

Covers the lifecycle (running -> done/error/stopped), result capture, the
RunStore persistence (a delegation IS a run: run_started husk at start,
tool_call events + result state_snapshot + run_ended at close), the completion
notification, and the refactor-02 authorization flip: trading agents run under
a zero-seeded risk_gate (AGENT.md baseline or per-call override, loud error
with neither), non-trading specialists keep full auto-approve.

Post-C.1/C.2: ``start_delegation`` returns a ``run_id`` (str); there is no
in-memory status record — status/result come from the RunStore stream, and
cancellation is ``cancel_delegation`` (the wire-in for ``control_run stop``).
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import delegate as delegate_module
from condor.agents import run as run_module
from condor.agents.delegate import cancel_delegation, start_delegation
from condor.agents.run import RunResult


def _outbox_texts() -> list[str]:
    """Texts of the notifications recorded in the (test-isolated) outbox."""
    from condor.notifications import read_notifications

    return [e["text"] for e in read_notifications()]


def _write_agent(root, slug, *, risk_limits=None, tools=("manage_routines",)):
    """Write an AGENT.md. Default tools are a declared NON-trading scope; pass
    tools=() for an unrestricted (⇒ trading) agent or an explicit list."""
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {slug}", "when_to_consult: always"]
    if tools:
        fm.append("tools:")
        for t in tools:
            fm.append(f"- {t}")
    if risk_limits:
        fm.append("risk_limits:")
        for k, v in risk_limits.items():
            fm.append(f"  {k}: {v}")
    (d / "AGENT.md").write_text("---\n" + "\n".join(fm) + "\n---\n\nBody.\n")
    return d


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


async def _drain(run_id):
    """Await the background task to completion (ignoring cancellation).

    The task is popped from the registry in its finally block, so capture the
    handle right after start (before it runs) to await it here."""
    task = delegate_module._delegations.get(run_id)
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _patch_roots(monkeypatch, tmp_path):
    from condor.agents.runstore import RunStore, set_run_store

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    set_run_store(RunStore(root=tmp_path))


def _run_events(slug, run_id):
    from condor.agents.runstore import get_run_store

    return get_run_store().read_events(slug, run_id)


def _run_status(slug, run_id):
    from condor.agents.runstore import get_run_store

    return get_run_store().run_meta(slug, run_id)["status"]


def _run_result(slug, run_id):
    """The captured result text from the run's last state_snapshot, or ''."""
    snaps = [e for e in _run_events(slug, run_id) if e["type"] == "state_snapshot"]
    return snaps[-1]["payload"]["result"] if snaps else ""


def test_delegation_runs_to_done_and_persists(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["agent"] = agent
        seen["prompt"] = prompt
        seen["permission_policy"] = permission_policy
        seen.update(kw)
        return RunResult(text="scan complete: 3 pools")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="scout",
            task="scan SOL pools",
        )
        # Returns immediately with a run_id; still running before we await it.
        assert run_id in delegate_module._delegations
        # run_started persisted from the start (crash-inspectable stream).
        events = _run_events("scout", run_id)
        assert events[0]["type"] == "run_started"
        assert events[0]["payload"]["kind"] == "delegation"
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())

    # The delegation id IS a run id — an opaque ULID, no "-dN" grammar.
    from condor.agents.runstore import is_run_id

    assert is_run_id(run_id)
    # Lifecycle + result capture (from the RunStore stream).
    assert _run_status("scout", run_id) == "completed"
    assert _run_result("scout", run_id) == "scan complete: 3 pools"
    # Serverless agent → AUTO policy (no permission callback).
    assert seen["permission_policy"] is None
    assert "scan SOL pools" in seen["prompt"]
    # The stream records the result and terminal status; no session dir.
    events = _run_events("scout", run_id)
    assert events[-1]["type"] == "run_ended"
    assert events[-1]["payload"]["status"] == "completed"
    assert not (tmp_path / "scout" / "sessions").exists()
    # Notification delivered.
    assert any("done" in m for m in _outbox_texts())


def test_delegation_captures_error(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    async def boom(agent, prompt, **kw):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(run_module, "run_agent", boom)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="scout",
            task="do thing",
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())

    assert _run_status("scout", run_id) == "error"
    assert any("failed" in m and "model exploded" in m for m in _outbox_texts())
    events = _run_events("scout", run_id)
    assert "model exploded" in events[-1]["payload"]["reason"]


def test_stop_cancels_running_delegation(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    async def slow(agent, prompt, **kw):
        await asyncio.sleep(60)
        return RunResult(text="never")

    monkeypatch.setattr(run_module, "run_agent", slow)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="scout",
            task="long task",
        )
        await asyncio.sleep(0)  # let the runner start
        assert run_id in delegate_module._delegations
        stopped = cancel_delegation(run_id)
        await _drain(run_id)
        return run_id, stopped

    run_id, stopped = asyncio.run(scenario())

    assert stopped is True
    # A stopped task does not spam a completion notification.
    assert _outbox_texts() == []
    # But its stream records the terminal state.
    assert _run_status("scout", run_id) == "stopped"


def test_stop_unknown_returns_false():
    assert cancel_delegation("nope_99") is False


def test_unknown_agent_errors_at_start(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="No agent named"):
        asyncio.run(
            start_delegation(
                agent_slug="ghost",
                task="x",
            )
        )


# ── refactor-02 §4.1: authorization flip ──


def test_trading_delegation_without_limits_errors_loudly(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    # No tool scope declared = unrestricted = trading, and no baseline.
    _write_agent(tmp_path, "trader", tools=())

    with pytest.raises(ValueError, match="risk baseline"):
        asyncio.run(
            start_delegation(
                agent_slug="trader",
                task="deploy a bot",
            )
        )
    # No husk left behind for a rejected start.
    assert not (tmp_path / "trader" / "runs").exists() or not list(
        (tmp_path / "trader" / "runs").iterdir()
    )


def test_executor_owning_agent_without_baseline_errors(tmp_path, monkeypatch):
    """#4: an agent that owns manage_executors can move real capital via
    condor-native executors, so it must carry a baseline — a delegation with
    none must error loudly, not silently auto-approve."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path, "native_trader", tools=["manage_executors"]
    )  # trades, but no baseline

    with pytest.raises(ValueError, match="risk baseline"):
        asyncio.run(
            start_delegation(
                agent_slug="native_trader",
                task="open a position",
            )
        )


def test_trading_delegation_uses_agent_baseline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "trader",
        tools=["manage_executors"],
        risk_limits={"max_position_size_quote": 500, "max_open_executors": 5},
    )

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        seen["risk_limits"] = kw.get("risk_limits")
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="trader",
            task="deploy",
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())
    assert _run_status("trader", run_id) == "completed"
    # The baseline governs the run and is recorded in the run_started payload.
    assert seen["risk_limits"] == {
        "max_position_size_quote": 500,
        "max_open_executors": 5,
    }
    started = _run_events("trader", run_id)[0]
    assert started["payload"]["risk_limits"] == {
        "max_position_size_quote": 500,
        "max_open_executors": 5,
    }
    # Trading agent → a real risk_gate callback, never AUTO.
    assert seen["permission_policy"] is not None


def test_agent_with_baseline_is_risk_gated_even_without_executor_tool(
    tmp_path, monkeypatch
):
    """An agent with an AGENT.md risk baseline must be gated regardless of its
    declared tool scope — the baseline is the operator saying "bound this"."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "lp_agent",
        risk_limits={"max_position_size_quote": 50, "max_open_executors": 2},
    )

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        seen["risk_limits"] = kw.get("risk_limits")
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="lp_agent",
            task="open an LP position",
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())
    assert seen["risk_limits"] == {
        "max_position_size_quote": 50,
        "max_open_executors": 2,
    }
    assert seen["permission_policy"] is not None  # gated, never AUTO


def test_explicit_caps_not_discarded(tmp_path, monkeypatch):
    """Per-call caps on a delegation must govern the run (they were silently
    dropped before the condor-simple fix)."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "lp_agent")  # non-trading scope, no baseline

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        seen["risk_limits"] = kw.get("risk_limits")
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="lp_agent",
            task="open an LP position",
            risk_limits={"max_position_size_quote": 5},
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())
    assert seen["risk_limits"] == {"max_position_size_quote": 5}
    assert seen["permission_policy"] is not None


def test_non_trading_specialist_without_baseline_stays_auto(tmp_path, monkeypatch):
    """Specialists with a declared non-trading scope (no baseline, no caps)
    keep full auto-approve."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "builder")

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        seen["risk_limits"] = kw.get("risk_limits")
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="builder",
            task="build a routine",
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())
    assert seen["risk_limits"] is None
    assert seen["permission_policy"] is None  # AUTO


def test_per_call_override_replaces_baseline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "trader",
        tools=["manage_executors"],
        risk_limits={"max_position_size_quote": 500},
    )

    async def fake_run(agent, prompt, **kw):
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="trader",
            task="deploy with more room",
            risk_limits={"max_position_size_quote": 2000},
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())
    # REPLACE, not merge: exactly what was passed governs the run — and the
    # run_started payload records exactly those numbers.
    started = _run_events("trader", run_id)[0]
    assert started["payload"]["risk_limits"] == {"max_position_size_quote": 2000}


def test_zero_seeded_gate_bounds_native_creates(tmp_path, monkeypatch):
    """End-to-end policy check: the delegation's risk_gate cancels a native
    create over the position cap (and one whose risk can't be computed — fail
    closed), and approves an in-cap create."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "trader",
        tools=["manage_executors"],
        risk_limits={"max_position_size_quote": 500},
    )

    captured = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        captured["policy"] = permission_policy
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    options = [{"kind": "allow_once", "optionId": "allow"}]

    def _create(config):
        return {
            "tool": "manage_executors",
            "input": {
                "action": "create",
                "executor_type": "position_pred",
                "config": config,
            },
        }

    async def scenario():
        run_id = await start_delegation(
            agent_slug="trader",
            task="deploy",
        )
        await _drain(run_id)
        policy = captured["policy"]

        over_cap = await policy(
            _create({"market": "world-cup", "amount_quote": 600}), options
        )
        in_cap = await policy(
            _create({"market": "world-cup", "amount_quote": 100}), options
        )
        unknown = await policy(
            {
                "tool": "manage_executors",
                "input": {"action": "create", "executor_type": "mystery"},
            },
            options,
        )
        return over_cap, in_cap, unknown

    over_cap, in_cap, unknown = asyncio.run(scenario())
    assert over_cap["outcome"]["outcome"] == "cancelled"
    assert in_cap["outcome"]["outcome"] == "selected"
    assert unknown["outcome"]["outcome"] == "cancelled"  # fail closed


def test_delegation_persists_tool_calls_and_result(tmp_path, monkeypatch):
    """Persistence matches CONSULT: tool calls stream to the RunStore via
    on_tool_call and the result lands as a state_snapshot — the generated
    export renders both."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    async def fake_run(agent, prompt, *, on_tool_call, **kw):
        # The runtime hands folded tool-call dicts to on_tool_call; simulate one.
        on_tool_call(
            {
                "id": "t1",
                "name": "get_market_data",
                "status": "completed",
                "input": {"connector": "binance", "pair": "SOL-USDC"},
                "output": "3 pools found",
            }
        )
        return RunResult(text="Done: 3 pools.")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        run_id = await start_delegation(
            agent_slug="scout",
            task="scan SOL pools",
        )
        await _drain(run_id)
        return run_id

    run_id = asyncio.run(scenario())

    assert _run_status("scout", run_id) == "completed"
    events = _run_events("scout", run_id)
    tool_evs = [e for e in events if e["type"] == "tool_call"]
    assert tool_evs and tool_evs[0]["payload"]["name"] == "get_market_data"
    assert tool_evs[0]["payload"]["output"] == "3 pools found"
    assert _run_result("scout", run_id) == "Done: 3 pools."

    # The generated markdown export renders the run, not just the result.
    from condor.agents.exports import render_run_markdown
    from condor.agents.runstore import get_run_store

    store = get_run_store()
    text = render_run_markdown(store.run_meta("scout", run_id), events)
    assert "Done: 3 pools." in text
    assert "Run ended — completed" in text
