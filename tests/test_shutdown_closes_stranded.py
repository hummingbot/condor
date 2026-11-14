"""A winddown must close a position that no live executor owns.

Regression coverage for hummingbot/condor#339: ``create_order_executor``
terminates once it has placed a held position, so a kill switch finds nothing to
stop, nothing resting to cancel, and a `close_position` it never calls -- and
the position survives while the journal reports it stranded.
"""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from condor.agents.shutdown import run_shutdown
from condor.agents.strategy import Strategy
from condor.fetchers import executors as executors_fetcher
from condor.fetchers import positions as positions_fetcher

FLAT = "---\non_kill_switch: flatten_all\n---\nBody\n"
KEEP_SPOT = "---\non_kill_switch: keep_spot_close_perp\n---\nBody\n"


class _FakeExecutorsAPI:
    def __init__(self, positions_sequence, account_executors=None):
        self._positions = list(positions_sequence)
        self.stop_calls = []
        self.cleared = []
        # Rows the account-wide walk sees (``fetch_all_executors``), i.e. every
        # session's executors, not just this one's.
        self._account_executors = list(account_executors or [])
        self.search_failures = 0

    async def search_executors(self, **kwargs):
        if self.search_failures:
            raise RuntimeError("executor search unavailable")
        wanted = str(kwargs.get("status") or "").upper()
        rows = [
            ex
            for ex in self._account_executors
            if not wanted or str(ex.get("status") or "").upper() == wanted
        ]
        return {"data": rows, "pagination": {"next_cursor": None}}

    async def stop_executor(self, executor_id, keep_position=False):
        self.stop_calls.append((executor_id, keep_position))
        return {"status": "ok"}

    async def get_positions_summary(self, controller_id=None):
        if len(self._positions) > 1:
            return {"positions": self._positions.pop(0)}
        last = self._positions[0] if self._positions else []
        if isinstance(last, Exception):
            raise last
        return {"positions": last}

    async def clear_position_held(self, **kwargs):
        self.cleared.append(kwargs)
        return {"status": "ok"}


class _FakeMarketDataAPI:
    """Just enough for ``fetch_current_price``."""

    def __init__(self, price=100.0):
        self.price = price
        self.calls = []

    async def get_prices(self, connector_name=None, trading_pairs=None):
        self.calls.append((connector_name, trading_pairs))
        return {"prices": {trading_pairs: self.price} if trading_pairs else {}}


class _FakeTradingAPI:
    def __init__(self, place_error=None, venue_positions=None):
        self.calls = []
        self.cancel_calls = []
        self._error = place_error
        # When set, MARKET orders are refused the way Derive refuses them.
        self.refuse_market = None
        # What the venue reports account-wide -- the read that sees a position
        # Condor never tracked.
        self.venue_positions = list(venue_positions or [])
        self.position_reads = 0
        self.position_error = None

    async def get_positions(self, **kwargs):
        self.position_reads += 1
        if self.position_error:
            raise self.position_error
        return {"data": list(self.venue_positions), "pagination": {"next_cursor": None}}

    async def place_order(self, **kwargs):
        if self.refuse_market and str(kwargs.get("order_type")) == "MARKET":
            raise RuntimeError(self.refuse_market)
        if self._error:
            raise self._error
        self.calls.append(kwargs)
        return {"order_id": "ord-1"}

    async def get_active_orders(self, **kwargs):
        """Nothing rests: the crossing limit filled."""
        return {"data": [], "pagination": {"next_cursor": None}}

    async def cancel_order(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return {"status": "ok"}


class _FakeClient:
    def __init__(
        self,
        positions_sequence,
        place_error=None,
        venue_positions=None,
        account_executors=None,
    ):
        self.executors = _FakeExecutorsAPI(positions_sequence, account_executors)
        self.trading = _FakeTradingAPI(place_error, venue_positions)
        self.market_data = _FakeMarketDataAPI()


class _FakeJournal:
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
        self.ticks.append(summary)
        self.tick_count += 1
        return self.tick_count

    def actions_named(self, name):
        return [r for a, r in self.actions if a == name]


@pytest.fixture(autouse=True)
def _stub_fetchers(monkeypatch):
    """Default: no stopping executors, and the venue reports the book flat."""

    async def _no_stopping(client, **kwargs):
        return []

    async def _flat_venue(client, **kwargs):
        return []

    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _no_stopping)
    monkeypatch.setattr(positions_fetcher, "fetch_positions", _flat_venue)


def _engine(
    policy_body,
    running_executors,
    positions_sequence,
    place_error=None,
    monkeypatch=None,
    tmp_path=None,
):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    strat = Strategy(agent_slug="acme", name="Scalper")
    strat.home.mkdir(parents=True, exist_ok=True)
    (strat.home / "shutdown.md").write_text(policy_body)

    class _Registry:
        async def run_core_providers(
            self, client, config, agent_id="", bot_names=None, owned=None, names=None
        ):
            return {"executors": SimpleNamespace(data={"executors": running_executors})}

    client = _FakeClient(positions_sequence, place_error)
    notifications = []

    async def _notify(msg):
        notifications.append(msg)

    async def _get_client():
        return client

    engine = SimpleNamespace(
        strategy=strat,
        agent_id="acme.scalper_1",
        config={},
        journal=_FakeJournal(),
        provider_registry=_Registry(),
        ledger=None,
        _last_skill_data={"executors": running_executors},
        _get_client=_get_client,
        _notify=_notify,
    )
    return engine, client, notifications


def _perp_short():
    return {
        "account_name": "master",
        "connector_name": "hyperliquid_perpetual",
        "trading_pair": "SOL-USD",
        "position_side": "SHORT",
        "net_amount_base": 0.18,
    }


def test_stranded_perp_with_no_executor_is_closed(tmp_path, monkeypatch):
    stuck = [_perp_short()]
    engine, client, notes = _engine(
        FLAT, [], [stuck, stuck, []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert len(client.trading.calls) == 1
    order = client.trading.calls[0]
    assert order["connector_name"] == "hyperliquid_perpetual"
    assert order["trading_pair"] == "SOL-USD"
    assert order["trade_type"] == "BUY"  # closing a SHORT
    assert order["amount"] == 0.18
    assert order["order_type"] == "MARKET"
    assert order["position_action"] == "CLOSE"

    assert not any("\U0001f6a8" in n for n in notes)
    assert engine.journal.actions_named("close_stranded_positions")
    assert ("shutdown_done", "stopped=0, bots=0, failures=0, verify=flat") in [
        (a, r) for a, r in engine.journal.actions
    ]


def test_long_is_closed_with_a_sell(tmp_path, monkeypatch):
    stuck = [dict(_perp_short(), position_side="LONG")]
    engine, client, _ = _engine(
        FLAT, [], [stuck, stuck, []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert client.trading.calls[0]["trade_type"] == "SELL"


def test_confirmed_close_clears_the_stale_held_record(tmp_path, monkeypatch):
    """The venue close does not clear Condor's own hold -- the winddown must."""
    stuck = [_perp_short()]
    engine, client, _ = _engine(
        FLAT, [], [stuck, stuck, []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert len(client.executors.cleared) == 1
    assert client.executors.cleared[0]["connector_name"] == "hyperliquid_perpetual"
    assert client.executors.cleared[0]["trading_pair"] == "SOL-USD"
    assert client.executors.cleared[0]["controller_id"] == "acme.scalper_1"


def test_tracking_is_kept_when_the_venue_still_holds_the_position(
    tmp_path, monkeypatch
):
    """A close that has not filled must not be hidden by clearing tracking."""
    stuck = [_perp_short()]

    async def _venue_still_open(client, **kwargs):
        return [{"connector_name": "hyperliquid_perpetual", "trading_pair": "SOL-USD"}]

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue_still_open)
    engine, client, notes = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert client.trading.calls  # the close was still attempted
    assert client.executors.cleared == []  # but the record stays visible
    assert any("\U0001f6a8" in n and "SOL-USD" in n for n in notes)


def test_a_shutting_down_executor_blocks_the_direct_close(tmp_path, monkeypatch):
    """An executor still flattening owns its pair until its close lands."""
    stuck = [_perp_short()]

    async def _stopping(client, **kwargs):
        assert kwargs.get("status") == "SHUTTING_DOWN"
        return [
            {
                "connector": "hyperliquid_perpetual",
                "trading_pair": "SOL-USD",
                "status": "SHUTTING_DOWN",
            }
        ]

    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _stopping)
    engine, client, _ = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert client.trading.calls == []


def test_unreadable_stopping_executors_means_no_close(tmp_path, monkeypatch):
    """Ownership that cannot be proven is not ownership that is absent."""
    stuck = [_perp_short()]

    async def _boom(client, **kwargs):
        raise RuntimeError("executors api down")

    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _boom)
    engine, client, notes = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert client.trading.calls == []
    # The failure is surfaced, but sanitised: never the raw exception text.
    text = "\n".join(notes)
    assert "could not read stopping executors" in text
    assert "RuntimeError" in text
    assert "executors api down" not in text


def test_spot_is_never_market_closed(tmp_path, monkeypatch):
    stuck = [
        {
            "account_name": "master",
            "connector_name": "binance",
            "trading_pair": "ETH-USDT",
            "position_side": "LONG",
            "net_amount_base": 1.0,
        }
    ]
    engine, client, notes = _engine(
        KEEP_SPOT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert client.trading.calls == []
    # keep_spot_close_perp: spot is expected to remain, so nothing is stranded.
    assert not any("\U0001f6a8" in n for n in notes)


def test_unknown_side_is_skipped_not_guessed(tmp_path, monkeypatch):
    stuck = [
        {
            "account_name": "master",
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "SOL-USD",
            "net_amount_base": 0.18,
        }
    ]
    engine, client, notes = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert client.trading.calls == []  # a wrong-way close would open exposure
    assert any("\U0001f6a8" in n for n in notes)


def test_close_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    stuck = [_perp_short()]
    engine, client, notes = _engine(
        FLAT,
        [],
        [stuck, stuck, stuck],
        place_error=RuntimeError("venue rejected"),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    asyncio.run(run_shutdown(engine, "kill"))
    assert any("\U0001f6a8" in n and "SOL-USD" in n for n in notes)
    assert any("venue rejected" in n for n in notes)
    done = engine.journal.actions_named("shutdown_done")
    assert done and "failures=1" in done[0]
    assert "verify=1 stranded" in done[0]


def test_unreadable_book_is_not_reported_as_flat(tmp_path, monkeypatch):
    """A failed verification read is unknown, never flat -- journal and notice."""
    engine, client, notes = _engine(
        FLAT,
        [],
        [RuntimeError("api down")],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert client.trading.calls == []  # cannot close blind
    done = engine.journal.actions_named("shutdown_done")
    assert done and "verify=unknown" in done[0]
    assert "flat" not in done[0]
    assert not any("\u2705" in n for n in notes)
    assert any("could NOT be read" in n for n in notes)


def test_failed_close_confirmation_read_is_not_reported_as_flat(tmp_path, monkeypatch):
    stuck = [_perp_short()]

    async def _venue_boom(client, **kwargs):
        raise RuntimeError("positions api down")

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue_boom)
    engine, client, _ = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))
    done = engine.journal.actions_named("shutdown_done")
    assert done and "verify=unknown" in done[0]
    assert client.executors.cleared == []  # never clear on an unconfirmed close


def test_a_capped_venue_read_never_clears_the_record(tmp_path, monkeypatch):
    """A truncated venue read cannot prove the close filled."""
    stuck = [_perp_short()]

    async def _capped(client, connector_name=None, **kwargs):
        # The account-wide read (no connector filter) is clean and empty; the
        # per-connector *confirmation* read is the one that truncates, which is
        # the read this test is about.
        if connector_name is None:
            return []
        return [
            {"connector_name": "hyperliquid_perpetual", "trading_pair": f"X{i}-USD"}
            for i in range(3)
        ]

    monkeypatch.setattr(positions_fetcher, "MAX_POSITIONS_FETCH", 3)
    monkeypatch.setattr(positions_fetcher, "fetch_positions", _capped)
    engine, client, _ = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert client.trading.calls  # the close was placed
    assert client.executors.cleared == []  # but an inconclusive read clears nothing


def test_read_errors_never_reach_the_notification_with_a_url(tmp_path, monkeypatch):
    """A backend URL in an exception must not leak into the operator message."""
    stuck = [_perp_short()]

    async def _boom(client, **kwargs):
        raise RuntimeError("500 from http://10.0.0.5:8000/bot-orchestration/executors")

    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _boom)
    monkeypatch.setattr(
        executors_fetcher,
        "describe_executor_error",
        lambda exc: (502, "executors unavailable"),
    )
    engine, client, notes = _engine(
        FLAT, [], [stuck, stuck, stuck], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    text = "\n".join(notes)
    assert "executors unavailable" in text
    assert "10.0.0.5:8000" not in text


# ---------------------------------------------------------------------------
# The book includes positions Condor never tracked.
# ---------------------------------------------------------------------------
#
# A desk that signs its own orders (flyby's Derive perp, theta's hedge) leaves
# no ``position_held`` record, so a tracker-only read is empty and the winddown
# announced ``verify=flat`` over a live position. The venue read closes that
# gap, and account-wide ownership keeps it from closing a neighbour's book.


def test_a_position_only_the_venue_knows_about_is_closed(tmp_path, monkeypatch):
    """No tracker row, no executor -- but the venue says the position is open."""
    venue = [_perp_short()]

    async def _venue(client, **kwargs):
        # Open until the winddown closes it, then flat -- what a real venue does.
        return venue if not client.trading.calls else []

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue)
    engine, client, _ = _engine(
        FLAT, [], [[], [], []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert len(client.trading.calls) == 1
    order = client.trading.calls[0]
    assert order["connector_name"] == "hyperliquid_perpetual"
    assert order["trading_pair"] == "SOL-USD"
    assert order["trade_type"] == "BUY"  # closing a SHORT
    assert order["order_type"] == "MARKET"
    assert order["position_action"] == "CLOSE"
    done = engine.journal.actions_named("shutdown_done")
    assert done and "verify=flat" in done[0]


def test_a_pair_owned_by_another_sessions_executor_is_never_closed(
    tmp_path, monkeypatch
):
    """Second strategy on the same account: its executor owns the pair."""
    venue = [_perp_short()]

    async def _venue(client, **kwargs):
        return venue

    async def _account_executors(client, status=None, **kwargs):
        if str(status or "").upper() == "RUNNING":
            return [
                {
                    "id": "someone-elses",
                    "status": "RUNNING",
                    "connector": "hyperliquid_perpetual",
                    "trading_pair": "SOL-USD",
                }
            ]
        return []

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue)
    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _account_executors)
    engine, client, _ = _engine(
        FLAT, [], [[], [], []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert client.trading.calls == []  # not this winddown's book
    done = engine.journal.actions_named("shutdown_done")
    # Owned by a live executor, so it is not stranded either -- reporting it
    # would raise a false alarm over a neighbour's position.
    assert done and "verify=flat" in done[0]


def test_an_unreadable_account_executor_list_closes_nothing(tmp_path, monkeypatch):
    """Unproven ownership is not absent ownership."""
    venue = [_perp_short()]

    async def _venue(client, **kwargs):
        return venue

    async def _account_executors(client, status=None, **kwargs):
        raise RuntimeError("executor search unavailable")

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue)
    monkeypatch.setattr(executors_fetcher, "fetch_all_executors", _account_executors)
    engine, client, notes = _engine(
        FLAT, [], [[], [], []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    assert client.trading.calls == []
    done = engine.journal.actions_named("shutdown_done")
    assert done and "verify=flat" not in done[0]
    assert "failures=1" in done[0]  # the unreadable executor list is reported


def test_a_failed_venue_read_is_not_reported_as_flat(tmp_path, monkeypatch):
    async def _boom(client, **kwargs):
        raise RuntimeError("venue down")

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _boom)
    engine, client, notes = _engine(
        FLAT, [], [[], [], []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    asyncio.run(run_shutdown(engine, "kill"))

    done = engine.journal.actions_named("shutdown_done")
    assert done and "verify=unknown" in done[0]
    assert client.trading.calls == []
    assert any("could NOT be read" in n for n in notes)


def test_a_refused_market_close_falls_back_to_a_crossing_ioc_limit(
    tmp_path, monkeypatch
):
    """Derive answers 'No order book exists' for MARKET; the close must still land."""
    venue = [_perp_short()]

    async def _venue(client, **kwargs):
        return venue if not client.trading.calls else []

    monkeypatch.setattr(positions_fetcher, "fetch_positions", _venue)
    engine, client, notes = _engine(
        FLAT, [], [[], [], []], monkeypatch=monkeypatch, tmp_path=tmp_path
    )
    client.trading.refuse_market = "No order book exists for 'SOL-USD'."
    client.market_data.price = 100.0

    asyncio.run(run_shutdown(engine, "kill"))

    assert len(client.trading.calls) == 1
    order = client.trading.calls[0]
    assert order["order_type"] == "LIMIT"
    assert "time_in_force" not in order  # the router has no such field
    assert order["trade_type"] == "BUY"  # closing a SHORT
    assert order["position_action"] == "CLOSE"
    assert order["price"] == pytest.approx(100.3)  # 0.3% through the mark
    done = engine.journal.actions_named("shutdown_done")
    assert done and "verify=flat" in done[0]
