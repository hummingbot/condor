"""Unit tests for DELEGATE -- fire-and-forget background agent tasks.

Covers the lifecycle (running -> done/error/stopped), result capture, the flat
transcript persistence (delegations/{date}-dN.md: husk at start, full rewrite
in finally — refactor-07), the completion notification, and the refactor-02
authorization flip: trading agents run under a zero-seeded risk_gate (AGENT.md
baseline or per-call override, loud error with neither), serverless agents
keep full auto-approve.
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import delegate as delegate_module
from condor.agents import run as run_module
from condor.agents.delegate import (
    get_all_delegations,
    get_delegation,
    start_delegation,
    stop_delegation,
)
from condor.agents.run import RunResult


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, *a, **kw):
        self.messages.append(kw.get("text") or (a[1] if len(a) > 1 else ""))


def _write_agent(root, slug, *, server_required=False, risk_limits=None, tools=None):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {slug}", "when_to_consult: always",
          f"server_required: {str(server_required).lower()}"]
    if tools is not None:
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


async def _drain(dt):
    """Await the background task to completion (ignoring cancellation)."""
    if dt._task is not None:
        try:
            await dt._task
        except asyncio.CancelledError:
            pass


def _patch_roots(monkeypatch, tmp_path):
    import condor.agents.journal as journal_module

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(journal_module, "_DATA_ROOT", tmp_path)


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
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan SOL pools",
            bot=bot,
        )
        # Returns immediately, still running before we await it.
        assert dt.status == "running"
        assert get_delegation(dt.task_id) is dt
        # The flat-file husk exists from the start (crash-inspectable).
        husk = dt.file_path.read_text()
        assert "**Status:** running" in husk
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    # Delegations have their own id namespace: "{slug}-d{N}", not a session id.
    assert dt.task_id == "scout-d1"
    # Lifecycle + result capture.
    assert dt.status == "done"
    assert dt.result == "scan complete: 3 pools"
    # Serverless agent → AUTO policy (no permission callback).
    assert seen["permission_policy"] is None
    assert "scan SOL pools" in seen["prompt"]
    # One flat transcript; NO session dir was consumed.
    text = dt.file_path.read_text()
    assert "scan complete: 3 pools" in text
    assert "**Status:** done" in text
    assert "**Ended:**" in text and "**Ended:** -" not in text
    assert not (tmp_path / "scout" / "sessions").exists()
    # Notification delivered.
    assert any("done" in m for m in bot.messages)


def test_delegation_captures_error(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    async def boom(agent, prompt, **kw):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(run_module, "run_agent", boom)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="do thing",
            bot=bot,
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "error"
    assert "model exploded" in dt.error
    assert any("failed" in m for m in bot.messages)
    text = dt.file_path.read_text()
    assert "**Status:** error" in text
    assert "model exploded" in text


def test_stop_cancels_running_delegation(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    async def slow(agent, prompt, **kw):
        await asyncio.sleep(60)
        return RunResult(text="never")

    monkeypatch.setattr(run_module, "run_agent", slow)
    bot = _FakeBot()

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="long task",
            bot=bot,
        )
        await asyncio.sleep(0)  # let the runner start
        assert dt.task_id in get_all_delegations()
        stopped = await stop_delegation(dt.task_id)
        await _drain(dt)
        return dt, stopped

    dt, stopped = asyncio.run(scenario())

    assert stopped is True
    assert dt.status == "stopped"
    # A stopped task does not spam a completion notification.
    assert bot.messages == []
    # But its transcript records the terminal state.
    assert "**Status:** stopped" in dt.file_path.read_text()


def test_stop_unknown_returns_false():
    assert asyncio.run(stop_delegation("nope_99")) is False


def test_unknown_agent_errors_at_start(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="No agent named"):
        asyncio.run(
            start_delegation(
                agent_slug="ghost",
                user_id=1,
                chat_id=42,
                server_name=None,
                task="x",
            )
        )


# ── refactor-02 §4.1: authorization flip ──


def test_trading_delegation_without_limits_errors_loudly(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "trader", server_required=True)  # no baseline

    with pytest.raises(ValueError, match="risk baseline"):
        asyncio.run(
            start_delegation(
                agent_slug="trader",
                user_id=1,
                chat_id=42,
                server_name="srv",
                task="deploy a bot",
            )
        )
    # No husk left behind for a rejected start.
    assert not (tmp_path / "trader" / "delegations").exists() or not list(
        (tmp_path / "trader" / "delegations").iterdir()
    )


def test_serverless_trading_agent_without_baseline_errors(tmp_path, monkeypatch):
    """#4: a SERVERLESS agent that owns manage_executors can still move real
    capital via condor-native executors, so it must carry a baseline — a
    delegation with none must error loudly, not silently auto-approve."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "native_trader", server_required=False,
                 tools=["manage_executors"])  # trades, but no baseline

    with pytest.raises(ValueError, match="risk baseline"):
        asyncio.run(
            start_delegation(
                agent_slug="native_trader",
                user_id=1,
                chat_id=42,
                server_name="",
                task="open a position",
            )
        )


def test_trading_delegation_uses_agent_baseline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "trader",
        server_required=True,
        risk_limits={"max_position_size_quote": 500, "max_open_executors": 5},
    )

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="trader",
            user_id=1,
            chat_id=42,
            server_name="srv",
            task="deploy",
            bot=_FakeBot(),
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())
    assert dt.status == "done"
    assert dt.risk_limits == {"max_position_size_quote": 500, "max_open_executors": 5}
    # Trading agent → a real risk_gate callback, never AUTO.
    assert seen["permission_policy"] is not None


def test_serverless_agent_with_baseline_is_risk_gated(tmp_path, monkeypatch):
    """A SERVERLESS agent with an AGENT.md risk baseline must be gated: it can
    trade through condor-native executors (gateway venues), so server_required
    alone is not the trading test."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "lp_agent",
        server_required=False,
        risk_limits={"max_position_size_quote": 50, "max_open_executors": 2},
    )

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="lp_agent",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="open an LP position",
            bot=_FakeBot(),
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())
    assert dt.risk_limits == {"max_position_size_quote": 50, "max_open_executors": 2}
    assert seen["permission_policy"] is not None  # gated, never AUTO


def test_serverless_agent_explicit_caps_not_discarded(tmp_path, monkeypatch):
    """Per-call caps on a serverless delegation must govern the run (they were
    silently dropped before the condor-simple fix)."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "lp_agent", server_required=False)  # no baseline

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="lp_agent",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="open an LP position",
            bot=_FakeBot(),
            risk_limits={"max_position_size_quote": 5},
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())
    assert dt.risk_limits == {"max_position_size_quote": 5}
    assert seen["permission_policy"] is not None


def test_serverless_agent_without_baseline_stays_auto(tmp_path, monkeypatch):
    """True serverless specialists (no baseline, no caps) keep full auto-approve."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "builder", server_required=False)

    seen = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        seen["permission_policy"] = permission_policy
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="builder",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="build a routine",
            bot=_FakeBot(),
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())
    assert dt.risk_limits is None
    assert seen["permission_policy"] is None  # AUTO


def test_per_call_override_replaces_baseline(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "trader",
        server_required=True,
        risk_limits={"max_position_size_quote": 500},
    )

    async def fake_run(agent, prompt, **kw):
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="trader",
            user_id=1,
            chat_id=42,
            server_name="srv",
            task="deploy with more room",
            bot=_FakeBot(),
            risk_limits={"max_position_size_quote": 2000},
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())
    # REPLACE, not merge: exactly what was passed governs the run — and the
    # transcript header records exactly those numbers.
    assert dt.risk_limits == {"max_position_size_quote": 2000}
    assert '"max_position_size_quote": 2000' in dt.file_path.read_text()


def test_zero_seeded_gate_blocks_uncapped_deploy_and_place_order(tmp_path, monkeypatch):
    """End-to-end policy check: the delegation's risk_gate cancels an uncapped
    bot deploy and a direct place_order, and approves a capped deploy."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "trader",
        server_required=True,
        risk_limits={"max_position_size_quote": 500},
    )

    captured = {}

    async def fake_run(agent, prompt, *, permission_policy, **kw):
        captured["policy"] = permission_policy
        return RunResult(text="ok")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    options = [{"kind": "allow_once", "optionId": "allow"}]

    async def scenario():
        dt = await start_delegation(
            agent_slug="trader",
            user_id=1,
            chat_id=42,
            server_name="srv",
            task="deploy",
            bot=_FakeBot(),
        )
        await _drain(dt)
        policy = captured["policy"]

        uncapped = await policy(
            {"tool": "manage_bots", "input": {"action": "deploy"}}, options
        )
        capped = await policy(
            {
                "tool": "manage_bots",
                "input": {"action": "deploy", "max_global_drawdown_quote": 100},
            },
            options,
        )
        order = await policy({"tool": "place_order", "input": {}}, options)
        return uncapped, capped, order

    uncapped, capped, order = asyncio.run(scenario())
    assert uncapped["outcome"]["outcome"] == "cancelled"
    assert capped["outcome"]["outcome"] == "selected"
    assert order["outcome"]["outcome"] == "cancelled"


def test_delegation_persists_full_session_transcript(tmp_path, monkeypatch):
    """The runner feeds streamed events to an event_sink and the transcript
    captures reasoning, tool calls (input/output), and the final result."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "scout")

    from condor.acp.client import TextChunk, ThoughtChunk, ToolCallEvent, ToolCallUpdate

    async def fake_run(agent, prompt, *, event_sink, **kw):
        # Simulate the agent streaming reasoning, a tool call, and a final answer.
        event_sink(ThoughtChunk(text="I should scan the pools first."))
        event_sink(
            ToolCallEvent(
                tool_call_id="t1",
                title="get_market_data",
                status="in_progress",
                kind="other",
                input={"connector": "binance", "pair": "SOL-USDC"},
            )
        )
        event_sink(
            ToolCallUpdate(
                tool_call_id="t1", status="completed", output="3 pools found"
            )
        )
        event_sink(TextChunk(text="Done: 3 pools."))
        return RunResult(text="Done: 3 pools.")

    monkeypatch.setattr(run_module, "run_agent", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug="scout",
            user_id=1,
            chat_id=42,
            server_name=None,
            task="scan SOL pools",
            bot=_FakeBot(),
        )
        await _drain(dt)
        return dt

    dt = asyncio.run(scenario())

    assert dt.status == "done"
    # Events captured in order on the task.
    assert [e["type"] for e in dt.events] == ["thought", "tool", "text"]
    tool_ev = dt.events[1]
    assert tool_ev["name"] == "get_market_data"
    assert tool_ev["status"] == "completed"  # patched by the ToolCallUpdate
    assert tool_ev["output"] == "3 pools found"

    # Transcript renders the full session, not just the result.
    text = dt.file_path.read_text()
    assert "## Session" in text
    assert "I should scan the pools first." in text
    assert "get_market_data" in text
    assert "3 pools found" in text
    assert "**Tool calls:** 1" in text
    assert "Done: 3 pools." in text
