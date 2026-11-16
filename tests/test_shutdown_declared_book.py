"""Tests for a declared venue book in the winddown.

An agent that signs its own orders has no tracked rows and no executors, so the
winddown used to close nothing and journal `verify=flat` over live exposure. A
`shutdown_positions` declaration makes the venue read real.
"""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from condor.agents.shutdown import run_shutdown
from condor.agents.strategy import Strategy
from condor.fetchers import executors as executors_fetcher
from condor.fetchers import positions as positions_fetcher

KEEP_SPOT = "---\non_kill_switch: keep_spot_close_perp\n---\nBody\n"

XRP_SHORT = {
    "connector_name": "derive_perpetual",
    "trading_pair": "XRP-USDC",
    "side": "SHORT",
    "amount": 10.0,
    "account_name": "master_account",
}


class _ExecutorsAPI:
    def __init__(self):
        self.stop_calls = []
        self.cleared = []

    async def stop_executor(self, executor_id, keep_position=False):
        self.stop_calls.append((executor_id, keep_position))
        return {"status": "ok"}

    async def get_positions_summary(self, controller_id=None):
        return {"positions": []}

    async def clear_position_held(self, **kwargs):
        self.cleared.append(kwargs)
        return {"status": "ok"}


class _TradingAPI:
    def __init__(self):
        self.calls = []

    async def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"order_id": "ord-1"}


class _Client:
    def __init__(self):
        self.executors = _ExecutorsAPI()
        self.trading = _TradingAPI()


class _Journal:
    def __init__(self):
        self.tick_count = 0
        self.actions = []
        self.ticks = []

    @contextmanager
    def batch(self):
        yield self

    def append_action(self, tick, action, reasoning, risk_note=""):
        self.actions.append((action, reasoning))

    def record_tick(self, summary="", actions=0):
        self.tick_count += 1
        self.ticks.append(summary)
        return self.tick_count

    def actions_named(self, name):
        return [r for a, r in self.actions if a == name]


def _build(tmp_path, monkeypatch, config, venue):
    """Engine with no tracked rows: the direct-signing shape."""

    async def _no_stopping(client, **kwargs):
        return []

    async def _venue(client, **kwargs):
        if isinstance(venue, Exception):
            raise venue
        return list(venue)

    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _no_stopping)
    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue)

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    strat = Strategy(agent_slug="acme", name="Desk")
    strat.home.mkdir(parents=True, exist_ok=True)
    (strat.home / "shutdown.md").write_text(KEEP_SPOT)

    class _Registry:
        async def run_core_providers(
            self, client, config, agent_id="", bot_names=None, owned=None, names=None
        ):
            return {"executors": SimpleNamespace(data={"executors": []})}

    client = _Client()
    notes = []

    async def _notify(msg):
        notes.append(msg)

    async def _get_client():
        return client

    engine = SimpleNamespace(
        strategy=strat,
        agent_id="acme.desk_1",
        config=config,
        journal=_Journal(),
        provider_registry=_Registry(),
        ledger=None,
        _last_skill_data={"executors": []},
        _get_client=_get_client,
        _notify=_notify,
    )
    return engine, client, notes


def test_a_declared_venue_position_is_closed_with_nothing_tracked(
    tmp_path, monkeypatch
):
    engine, client, notes = _build(
        tmp_path,
        monkeypatch,
        {"shutdown_positions": ["derive_perpetual:XRP-USDC"]},
        [XRP_SHORT],
    )

    asyncio.run(run_shutdown(engine, "manual"))

    assert len(client.trading.calls) == 1, "the venue position was never closed"
    order = client.trading.calls[0]
    assert order["connector_name"] == "derive_perpetual"
    assert order["trading_pair"] == "XRP-USDC"
    assert order["trade_type"] == "BUY"  # closing a SHORT
    assert order["amount"] == 10.0
    assert order["position_action"] == "CLOSE"
    assert engine.journal.actions_named("close_stranded_positions")


def test_a_declared_pair_absent_from_the_venue_closes_nothing(tmp_path, monkeypatch):
    engine, client, notes = _build(
        tmp_path, monkeypatch, {"shutdown_positions": ["derive_perpetual:XRP-USDC"]}, []
    )

    asyncio.run(run_shutdown(engine, "manual"))

    assert client.trading.calls == []
    done = [r for a, r in engine.journal.actions if a == "shutdown_done"]
    assert done and "verify=flat" in done[0]


def test_an_unreadable_declared_book_is_unknown_not_flat(tmp_path, monkeypatch):
    engine, client, notes = _build(
        tmp_path,
        monkeypatch,
        {"shutdown_positions": ["derive_perpetual:XRP-USDC"]},
        RuntimeError("venue down"),
    )

    asyncio.run(run_shutdown(engine, "manual"))

    assert client.trading.calls == []  # never close blind
    assert getattr(engine, "_verify_read_failed", False) is True
    done = [r for a, r in engine.journal.actions if a == "shutdown_done"]
    assert done, "the winddown must still record what it did"
    assert "verify=flat" not in done[0]


def test_without_a_declaration_the_account_wide_read_is_kept(tmp_path, monkeypatch):
    """No declaration keeps #340's behaviour: read the account, close untouched pairs."""
    calls = []

    async def _spy(client, **kwargs):
        calls.append(kwargs)
        return []

    engine, client, notes = _build(tmp_path, monkeypatch, {}, [])
    monkeypatch.setattr(positions_fetcher, "fetch_positions", _spy)

    asyncio.run(run_shutdown(engine, "manual"))

    assert calls, "an undeclared desk falls back to the account-wide read"
    assert (
        "connector_name" not in calls[0]
    ), "the fallback is account-wide, not per connector"


def test_a_declaration_reads_only_its_own_connector(tmp_path, monkeypatch):
    calls = []

    async def _spy(client, **kwargs):
        calls.append(kwargs)
        return []

    engine, client, notes = _build(
        tmp_path, monkeypatch, {"shutdown_positions": ["derive_perpetual:XRP-USDC"]}, []
    )
    monkeypatch.setattr(positions_fetcher, "fetch_positions", _spy)

    asyncio.run(run_shutdown(engine, "manual"))

    assert calls == [{"connector_name": "derive_perpetual", "strict": True}]


def test_a_declared_but_untraded_pair_is_not_closed(tmp_path, monkeypatch):
    other = {
        "connector_name": "derive_perpetual",
        "trading_pair": "ETH-USDC",
        "side": "SHORT",
        "amount": 0.1,
        "account_name": "master_account",
    }
    engine, client, notes = _build(
        tmp_path,
        monkeypatch,
        {"shutdown_positions": ["derive_perpetual:XRP-USDC"]},
        [other],
    )

    asyncio.run(run_shutdown(engine, "manual"))

    assert client.trading.calls == [], "only the declared pair may be touched"
