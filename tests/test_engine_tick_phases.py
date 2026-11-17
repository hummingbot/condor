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
        lambda tracker, **kw: RiskState(is_blocked=True, block_reason="max drawdown"),
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


# ── AG1-01: a failed executor read is not a healthy empty book ──


def _create_call():
    return {
        "tool": "create_grid_executor",
        "input": {
            "connector_name": "binance",
            "trading_pair": "SOL-USDC",
            "total_amount_quote": 10,
        },
    }


class _SearchRaises:
    async def search_executors(self, **kwargs):
        raise TimeoutError("search_executors timed out")


def _executors_only(engine, monkeypatch, result_for):
    """Run the gather phase with only the executors provider answering."""
    from condor.agents.providers.base import ProviderResult

    async def run_core_providers(*args, **kwargs):
        data, summary = result_for()
        return {
            "executors": ProviderResult(name="executors", data=data, summary=summary)
        }

    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_adopt_running_executors", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", run_core_providers
    )
    return asyncio.run(engine._gather_tick_context(object()))


def test_a_failed_executor_search_leaves_the_book_untrusted(tmp_path, monkeypatch):
    """End to end through the real provider: the API's ``search_executors``
    times out, and a create is refused instead of being sized against $0."""
    from types import SimpleNamespace

    from condor.agents.providers.executors import ExecutorsProvider

    engine = _engine(tmp_path, monkeypatch)
    client = SimpleNamespace(executors=_SearchRaises())

    async def run_core_providers(client_, config, **kwargs):
        result = await ExecutorsProvider().execute(
            client_, config, agent_id=kwargs["agent_id"]
        )
        return {"executors": result}

    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_adopt_running_executors", _async(None))
    monkeypatch.setattr(
        engine.provider_registry, "run_core_providers", run_core_providers
    )

    ctx = asyncio.run(engine._gather_tick_context(client))

    assert ctx is not None, "an unreadable book refuses creates, it does not block"
    assert ctx.risk_state.book_trusted is False
    assert "could not be read" in ctx.risk_state.drift_reason
    assert engine._tick_reading == ""
    assert engine._last_reading_at == 0.0, "a failed read is not a reading"
    assert "none running" not in ctx.prompt

    allowed, reason = engine.risk.check_executor_action(
        _create_call(), ctx.risk_state, 10.0
    )
    assert not allowed
    assert "Book untrusted" in reason


def test_a_provider_that_raised_outright_leaves_the_book_untrusted(
    tmp_path, monkeypatch
):
    """The registry's catch-all for a provider that raised is ``data={}``."""
    engine = _engine(tmp_path, monkeypatch)

    ctx = _executors_only(
        engine, monkeypatch, lambda: ({}, "(provider executors failed)")
    )

    assert ctx is not None
    assert ctx.risk_state.book_trusted is False


def test_an_answering_executors_provider_keeps_the_book_trusted(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)

    ctx = _executors_only(
        engine,
        monkeypatch,
        lambda: ({"executors": [], "reading": "ok"}, "Active Executors: none running"),
    )

    assert ctx is not None
    assert ctx.risk_state.book_trusted is True


def test_a_failed_read_keeps_the_last_good_rows(tmp_path, monkeypatch):
    """The stop gate's owner map and the stop sweep read ``_last_skill_data``.

    A failed read used to replace it with ``{"error": ...}``, so a stop on the
    tick the API struggled had no rows to scope itself to. The last good rows
    stay; the count and exposure the gate sizes against are theirs, and the
    book is untrusted because they are stale.
    """
    engine = _engine(tmp_path, monkeypatch)
    rows = [{"id": "ex-1", "pair": "SOL-USDC", "amount": 200.0}]
    good = {
        "executors": rows,
        "all_executors": rows,
        "total_exposure": 200.0,
        "total_pnl": 3.0,
        "reading": "ok",
    }
    answers = iter(
        [
            (good, "Active Executors (1)"),
            ({"error": "the trading API is unreachable"}, "failed to fetch"),
        ]
    )

    assert _executors_only(engine, monkeypatch, lambda: next(answers)) is not None
    stamped = engine._last_reading_at
    assert stamped > 0.0

    ctx = _executors_only(engine, monkeypatch, lambda: next(answers))

    assert ctx is not None
    assert engine._last_skill_data["executors"] == rows
    assert "error" not in engine._last_skill_data
    assert engine._tick_reading == "", "this tick read nothing"
    assert engine._last_reading_at == stamped, "and stamps no reading"
    assert ctx.risk_state.book_trusted is False
    assert ctx.risk_state.executor_count == 1
    assert ctx.risk_state.total_exposure == 200.0
    assert "ex-1" in engine._executor_owners()
