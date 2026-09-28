"""READ-649: TickEngine._tick is three phases, each testable on its own.

``_tick`` orchestrates ``_gather_tick_context`` (may end the tick),
``_run_model`` (one client, one timeout, always reaped) and ``_persist_tick``.
These tests drive a single phase without setting up a whole tick.
"""

import asyncio

from condor.agents.risk import RiskState


def _engine(tmp_path, monkeypatch, **config):
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="brigado", name="Grid")
    strategy.home.mkdir(parents=True, exist_ok=True)
    return TickEngine(
        agent=Agent(slug="brigado", name="Brigado"),
        strategy=strategy,
        config={"execution_mode": "loop", **config},
        chat_id=1,
        user_id=1,
    )


def _async(result):
    async def go(*args, **kwargs):
        return result

    return go


class _HangingClient:
    """A model that never answers: its stream awaits forever without yielding."""

    def __init__(self):
        self.stops = 0

    async def start(self):
        return None

    async def stop(self):
        self.stops += 1

    async def prompt_stream(self, prompt):
        await asyncio.Event().wait()
        yield  # pragma: no cover -- makes this an async generator


def test_run_model_reaps_client_on_timeout(tmp_path, monkeypatch):
    # resolve_tick_timeout returns int(strategy): a fractional budget would be 0.
    engine = _engine(tmp_path, monkeypatch, tick_timeout_sec=1)
    fake = _HangingClient()
    monkeypatch.setattr(engine, "_create_client", _async(fake))

    text, tool_calls, stop_reason = asyncio.run(
        engine._run_model("prompt", RiskState(), client=object())
    )

    assert text.endswith("(timed out)")
    assert tool_calls == []
    assert stop_reason == "end_turn"
    assert fake.stops == 1
    assert engine._active_client is None


def test_gather_tick_context_returns_none_when_blocked(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    assert engine.journal is not None, "session mode keeps a journal"
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(
        engine.risk,
        "get_state",
        lambda tracker: RiskState(is_blocked=True, block_reason="max drawdown"),
    )
    notices: list[str] = []

    async def notify(message):
        notices.append(message)

    monkeypatch.setattr(engine, "_notify", notify)

    assert asyncio.run(engine._gather_tick_context(object())) is None

    text = engine.journal._path.read_text()
    assert "tick_blocked" in text
    assert "blocked: max drawdown" in text
    assert engine.journal.tick_count == 1
    assert len(notices) == 1 and "max drawdown" in notices[0]

    # Same block on the next tick: journalled again, announced only once.
    assert asyncio.run(engine._gather_tick_context(object())) is None
    assert engine.journal.tick_count == 2
    assert len(notices) == 1


def test_gather_tick_context_builds_the_prompt_when_not_blocked(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))

    ctx = asyncio.run(engine._gather_tick_context(object()))

    assert ctx is not None
    assert isinstance(ctx.prompt, str) and ctx.prompt
    assert ctx.risk_state.is_blocked is False
    assert ctx.executors_summary == "No executor data."
    assert engine.journal.tick_count == 0, "gathering records no tick"
