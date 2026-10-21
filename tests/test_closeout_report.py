"""The close-out report every stop emits, read from the venue (FEAT-134).

One test per acceptance criterion, and the fake venue deliberately *disagrees*
with the local rows: that is the race's actual case
(hummingbot/condor#273) — an archived agent listed two Gate spot
``LIMIT_MAKER`` sells as non-terminal, the venue subaccount held none, and the
local rows were simply stale. A report built from those rows would have
confirmed the wrong answer; the whole point of this module is that it does not.
"""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from condor.agents import closeout as closeout_mod
from condor.agents.closeout import (
    CLOSED,
    KIND_EXECUTOR,
    KIND_ORDER,
    KIND_POSITION,
    LEFT,
    SOURCE_RECORD,
    SOURCE_VENUE,
    UNCONFIRMABLE,
    CloseoutReport,
    build_closeout,
    close_out_agent,
    emit_closeout,
    lineage_rows,
    read_closeout,
    record_orders,
)
from condor.agents.engine import TickEngine
from condor.agents.shutdown import CancelOutcome, ShutdownPolicy
from condor.agents.strategy import Strategy

LINEAGE = "acme.scalper_"


# ── Fakes ──


class _FakeExecutorsAPI:
    def __init__(self, by_status, positions=(), stop_error=None):
        self.by_status = dict(by_status)
        self.positions = list(positions)
        self.stop_error = stop_error
        self.searches = []
        self.stop_calls = []

    async def search_executors(self, limit=50, status=None, controller_ids=None, **_kw):
        self.searches.append((status, tuple(controller_ids or ())))
        rows = list(self.by_status.get(status, []))
        if controller_ids is not None:
            rows = [r for r in rows if r.get("controller_id") in set(controller_ids)]
        return {"data": rows[: int(limit)]}

    async def get_positions_summary(self, controller_id=None):
        return {"positions": list(self.positions)}

    async def stop_executor(self, executor_id, keep_position=False):
        self.stop_calls.append((executor_id, keep_position))
        if self.stop_error is not None:
            raise self.stop_error
        return {"status": "ok"}


class _FakeTradingAPI:
    def __init__(self, active=(), venue_positions=(), refuse=(), list_error=None):
        self.active = list(active)
        self.venue_positions = list(venue_positions)
        self.refuse = set(refuse)
        self.list_error = list_error
        self.list_calls = 0
        self.cancel_calls = []

    async def get_active_orders(
        self, limit=50, connector_names=None, trading_pairs=None, **_kw
    ):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        rows = [
            o
            for o in self.active
            if (connector_names is None or o["connector_name"] in connector_names)
            and (trading_pairs is None or o["trading_pair"] in trading_pairs)
        ]
        return {"data": rows[: int(limit)]}

    async def get_positions(self, limit=50, **_kw):
        return {"data": list(self.venue_positions)}

    async def cancel_order(self, account_name, connector_name, client_order_id):
        self.cancel_calls.append((account_name, connector_name, client_order_id))
        if client_order_id in self.refuse:
            raise RuntimeError("order already filled")
        return {"message": "ok"}


class _FakeClient:
    def __init__(self, executors=None, trading=None):
        self.executors = executors or _FakeExecutorsAPI({})
        self.trading = trading or _FakeTradingAPI()


class _FakeJournal:
    def __init__(self):
        self.tick_count = 0
        self.actions = []

    @contextmanager
    def batch(self):
        yield self

    def append_action(self, tick, action, reasoning, risk_note=""):
        self.actions.append((action, reasoning))

    def record_tick(self, summary="", actions=0):
        self.tick_count += 1
        return self.tick_count


def _executor(
    ex_id,
    *,
    session=1,
    status="TERMINATED",
    connector="gate_io",
    pair="SOL-USDC",
    ex_type="position",
    custom_info=None,
):
    return {
        "id": ex_id,
        "status": status,
        "type": ex_type,
        "controller_id": f"{LINEAGE}{session}",
        "connector_name": connector,
        "trading_pair": pair,
        "custom_info": custom_info or {},
        "config": {"type": ex_type, "connector_name": connector, "trading_pair": pair},
    }


def _order(order_id, connector="gate_io", pair="SOL-USDC", status="OPEN"):
    return {
        "order_id": order_id,
        "account_name": "master_account",
        "connector_name": connector,
        "trading_pair": pair,
        "status": status,
    }


def _claims(*order_ids, status="OPEN"):
    """``custom_info`` for an executor record that owns ``order_ids`` (SEC-739).

    The sweep cancels only the orders this lineage's own executors claim, so a
    fixture whose leftover order is meant to be swept has to say whose it is.
    """
    return {"orders": [{"order_id": oid, "status": status} for oid in order_ids]}


def _strategy(tmp_path, monkeypatch, *, sessions=(1,)) -> Strategy:
    """A real strategy on disk, so the lineage's session tags resolve."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    s = Strategy(agent_slug="acme", name="Scalper")
    for num in sessions:
        (s.home / "sessions" / f"session_{num}").mkdir(parents=True, exist_ok=True)
    return s


# ── Criterion 1: a graceful stop emits a report, each thing closed or left ──


def test_a_graceful_stop_emits_a_report_of_executors_orders_and_positions(
    tmp_path, monkeypatch
):
    strat = _strategy(tmp_path, monkeypatch)
    session_dir = strat.home / "sessions" / "session_1"
    executors = _FakeExecutorsAPI(
        {
            "RUNNING": [_executor("e-live", status="RUNNING")],
            "TERMINATED": [_executor("e-done")],
        },
        positions=[
            {
                "account_name": "master_account",
                "connector_name": "binance_perpetual",
                "trading_pair": "BTC-USDT",
                "position_side": "LONG",
                "net_amount_base": 0.5,
                "buy_breakeven_price": 60_000.0,
                "controller_id": f"{LINEAGE}1",
            }
        ],
    )
    trading = _FakeTradingAPI(
        active=[_order("o-resting")],
        venue_positions=[
            {
                "account_name": "master_account",
                "connector_name": "binance_perpetual",
                "trading_pair": "BTC-USDT",
                "side": "LONG",
                "amount": 0.5,
                "entry_price": 60_000.0,
            }
        ],
    )
    client = _FakeClient(executors, trading)
    engine = _stop_engine(strat, client, session_dir)

    report = asyncio.run(emit_closeout(engine))

    # Every kind is named, and nothing is silently absent.
    assert {i.kind for i in report.items} == {
        KIND_EXECUTOR,
        KIND_ORDER,
        KIND_POSITION,
    }
    outcomes = {(i.kind, i.ref): i.outcome for i in report.items}
    assert outcomes[(KIND_EXECUTOR, "e-done")] == CLOSED
    assert outcomes[(KIND_EXECUTOR, "e-live")] == LEFT
    assert outcomes[(KIND_ORDER, "o-resting")] == LEFT
    assert outcomes[(KIND_POSITION, "binance_perpetual BTC-USDT")] == LEFT

    # Recorded where an operator finds it, and in the journal.
    assert (session_dir / "closeout.md").is_file()
    stored = read_closeout(session_dir)
    assert stored["summary"] == report.summary()
    assert [a for a, _ in engine.journal.actions] == ["closeout"]
    assert report.clean is False


def _stop_engine(strat, client, session_dir, *, cancel=None):
    """A ``_finish``-shaped stand-in: the stop's close-out and nothing else."""

    async def _get_client():
        return client

    return SimpleNamespace(
        agent_id=f"{LINEAGE}1",
        strategy=strat,
        config={"execution_mode": "loop"},
        journal=_FakeJournal(),
        session_dir=session_dir,
        _cancel_outcome=cancel,
        _get_client=_get_client,
    )


# ── Criterion 2: the venue contradicts the local rows, and wins ──


def test_two_stale_local_orders_are_reported_closed_not_open(tmp_path, monkeypatch):
    """#273 exactly: the records claim two resting sells, the venue holds none.

    A report built from local rows would have said "2 open orders" — the answer
    the operator had to disprove by hand. This one says the venue shows none and
    names the rows stale.
    """
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {
            "TERMINATED": [
                _executor(
                    "e1",
                    custom_info={
                        "open_orders": [
                            {"order_id": "sell-1", "status": "OPEN"},
                            {"order_id": "sell-2", "status": "PARTIALLY_FILLED"},
                        ]
                    },
                )
            ]
        }
    )
    client = _FakeClient(executors, _FakeTradingAPI(active=[]))  # venue: nothing

    report = asyncio.run(build_closeout(client, "acme", "scalper"))

    orders = {i.ref: i for i in report.of_kind(KIND_ORDER)}
    assert set(orders) == {"sell-1", "sell-2"}
    for item in orders.values():
        assert item.outcome == CLOSED
        assert item.source == SOURCE_VENUE
        assert "STALE" in item.detail
    assert not report.of_outcome(LEFT)
    assert not report.of_outcome(UNCONFIRMABLE)
    assert len(report.stale_records) == 2
    assert "stale local row" in report.summary()


def test_record_orders_reads_the_claims_a_local_report_would_have_believed():
    """The stale side, by union over the shapes an executor record uses."""
    rows = record_orders(
        [
            _executor(
                "e1",
                custom_info={
                    "open_orders": [{"order_id": "a", "status": "OPEN"}],
                    "close_orders": [{"order_id": "b", "status": "FILLED"}],
                    "active_orders": [{"order_id": "c", "status": "PARTIALLY_FILLED"}],
                },
            )
        ]
    )
    # ``b`` is terminal, so it makes no claim worth checking; the rest do.
    assert sorted(r["order_id"] for r in rows) == ["a", "c"]
    assert {r["connector_name"] for r in rows} == {"gate_io"}


def test_a_record_that_states_no_status_is_not_a_claim():
    """Silence is not a claim: inferring one would invent stale rows wholesale."""
    rows = record_orders(
        [
            _executor(
                "e1",
                custom_info={
                    "orders": [{"order_id": "a"}, {"order_id": "b", "status": ""}],
                    # Already filled by definition, and named terminal: both are
                    # out of scope whatever they carry.
                    "held_position_orders": [{"order_id": "c", "price": 7.0}],
                    "failed_orders": [{"order_id": "d", "status": "OPEN"}],
                },
            )
        ]
    )
    assert rows == []


# ── Criterion 3: unconfirmable is its own thing ──


def test_an_order_that_cannot_be_cancelled_or_confirmed_is_unconfirmable(
    tmp_path, monkeypatch
):
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI({"TERMINATED": [_executor("e1")]})
    # The live read fails outright, so nothing settles what is out there.
    client = _FakeClient(
        executors, _FakeTradingAPI(list_error=RuntimeError("gateway timeout"))
    )
    cancel = CancelOutcome(
        requested=True, failed=["gate_io SOL-USDC sell-9: order already filled"]
    )

    report = asyncio.run(build_closeout(client, "acme", "scalper", cancel=cancel))

    orders = report.of_kind(KIND_ORDER)
    assert orders, "an unread book must still be reported"
    assert {i.outcome for i in orders} == {UNCONFIRMABLE}
    # Not collapsed into either of the two answers it is not.
    assert not [i for i in orders if i.outcome in (CLOSED, LEFT)]
    assert any("sell-9" in i.label for i in orders)
    assert any("order read" in u for u in report.unanswered)
    assert "UNCONFIRMABLE" in report.summary()


def test_a_refused_cancel_the_venue_answered_for_is_not_unconfirmable(
    tmp_path, monkeypatch
):
    """The venue outranks the sweep's own answer, in both directions.

    One refused cancel whose order the live read shows resting is ``left``; one
    whose order it does not show is ``closed`` — the usual reason a cancel is
    refused is that the order already filled. Neither is ``unconfirmable``: the
    read answered.
    """
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI({"TERMINATED": [_executor("e1")]})
    client = _FakeClient(executors, _FakeTradingAPI(active=[_order("still-there")]))
    cancel = CancelOutcome(
        requested=True,
        failed=[
            "gate_io SOL-USDC still-there: rate limited",
            "gate_io SOL-USDC gone-already: order already filled",
        ],
    )

    report = asyncio.run(build_closeout(client, "acme", "scalper", cancel=cancel))

    by_outcome = {i.outcome: i.label for i in report.of_kind(KIND_ORDER)}
    assert set(by_outcome) == {LEFT, CLOSED}
    assert by_outcome[LEFT] == "gate_io SOL-USDC still-there"
    assert "gone-already" in by_outcome[CLOSED]
    # Counted once, not once per side.
    assert len([i for i in report.of_kind(KIND_ORDER) if "still-there" in i.label]) == 1


def test_a_failed_venue_position_read_is_unconfirmable_not_flat(tmp_path, monkeypatch):
    """An unreachable venue is never scored as a closed position."""
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {
            "TERMINATED": [
                _executor("e1", connector="binance_perpetual", pair="BTC-USDT")
            ]
        },
        positions=[
            {
                "account_name": "master_account",
                "connector_name": "binance_perpetual",
                "trading_pair": "BTC-USDT",
                "position_side": "LONG",
                "net_amount_base": 0.5,
                "buy_breakeven_price": 60_000.0,
                "controller_id": f"{LINEAGE}1",
            }
        ],
    )

    class _Broken(_FakeTradingAPI):
        async def get_positions(self, limit=50, **_kw):
            raise RuntimeError("venue unreachable")

    client = _FakeClient(executors, _Broken())
    report = asyncio.run(build_closeout(client, "acme", "scalper"))

    positions = report.of_kind(KIND_POSITION)
    assert [i.outcome for i in positions] == [UNCONFIRMABLE]
    assert "did not answer" in positions[0].detail


def test_a_spot_hold_is_reported_from_the_record_and_says_so(tmp_path, monkeypatch):
    """``/trading/positions`` is perps-only, so a spot hold is not venue-confirmed."""
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {"TERMINATED": [_executor("e1")]},
        positions=[
            {
                "account_name": "master_account",
                "connector_name": "gate_io",
                "trading_pair": "SOL-USDC",
                "position_side": "LONG",
                "net_amount_base": 12.0,
                "controller_id": f"{LINEAGE}1",
            }
        ],
    )
    client = _FakeClient(executors, _FakeTradingAPI())
    report = asyncio.run(build_closeout(client, "acme", "scalper"))

    spot = [i for i in report.of_kind(KIND_POSITION) if i.ref == "gate_io SOL-USDC"]
    assert [i.outcome for i in spot] == [LEFT]
    assert spot[0].source == SOURCE_RECORD
    assert "perps only" in spot[0].detail


# ── Criterion 4: a previous session's executor is in the report ──


def test_a_previous_sessions_executor_is_in_the_report(tmp_path, monkeypatch):
    """What ``shutdown.py``'s ``agent_id`` scoping skips, the lineage catches."""
    _strategy(tmp_path, monkeypatch, sessions=(1, 2))
    executors = _FakeExecutorsAPI(
        {
            "RUNNING": [
                _executor("e-old", session=1, status="RUNNING"),
                _executor("e-new", session=2, status="RUNNING"),
                # Another strategy's, and an experiment's: never in scope.
                {
                    "id": "e-other",
                    "status": "RUNNING",
                    "controller_id": "acme.hedger_1",
                    "connector_name": "gate_io",
                    "trading_pair": "SOL-USDC",
                },
                {
                    "id": "e-exp",
                    "status": "RUNNING",
                    "controller_id": f"{LINEAGE}e3",
                    "connector_name": "gate_io",
                    "trading_pair": "SOL-USDC",
                },
            ]
        }
    )
    client = _FakeClient(executors, _FakeTradingAPI())

    # Session 2 is the one stopping; session 1's engine is long gone.
    report = asyncio.run(
        build_closeout(client, "acme", "scalper", agent_id=f"{LINEAGE}2")
    )

    found = {i.ref: i.session for i in report.of_kind(KIND_EXECUTOR)}
    assert found == {"e-old": f"{LINEAGE}1", "e-new": f"{LINEAGE}2"}
    assert all(i.outcome == LEFT for i in report.of_kind(KIND_EXECUTOR))


def test_lineage_rows_rejects_other_strategies_and_experiments():
    rows = lineage_rows(
        [
            {"controller_id": f"{LINEAGE}1"},
            {"controller_id": f"{LINEAGE}e2"},
            {"controller_id": "acme.scalper2_1"},
            {"controller_id": "main"},
            {"config": {"controller_id": f"{LINEAGE}7"}},
            "not a row",
        ],
        "acme",
        "scalper",
    )
    assert [r.get("controller_id") or r["config"]["controller_id"] for r in rows] == [
        f"{LINEAGE}1",
        f"{LINEAGE}7",
    ]


def test_the_executor_read_is_narrowed_to_the_lineages_own_session_tags(
    tmp_path, monkeypatch
):
    """Server-side, so the terminated rows cost a scoped page and not a walk."""
    _strategy(tmp_path, monkeypatch, sessions=(1, 2, 3))
    executors = _FakeExecutorsAPI({})
    asyncio.run(
        build_closeout(_FakeClient(executors, _FakeTradingAPI()), "acme", "scalper")
    )

    tags = {f"{LINEAGE}1", f"{LINEAGE}2", f"{LINEAGE}3"}
    assert {s for s, _ in executors.searches} == {
        "RUNNING",
        "SHUTTING_DOWN",
        "TERMINATED",
    }
    assert all(set(ids) == tags for _, ids in executors.searches)


# ── Criterion 5: a builder that raises cannot fail or delay the stop ──


def test_a_builder_that_raises_does_not_fail_the_stop_and_is_recorded(
    tmp_path, monkeypatch
):
    strat = _strategy(tmp_path, monkeypatch)
    session_dir = strat.home / "sessions" / "session_1"
    engine = _stop_engine(strat, _FakeClient(), session_dir)

    def _boom(*_a, **_kw):
        raise RuntimeError("the builder is broken")

    monkeypatch.setattr(closeout_mod, "build_closeout", _boom)
    report = asyncio.run(emit_closeout(engine))

    assert "the builder is broken" in report.error
    assert "could NOT be built" in report.summary()
    assert read_closeout(session_dir)["error"] == report.error
    assert engine.journal.actions[0][0] == "closeout"


def test_a_builder_that_hangs_is_capped_and_recorded(tmp_path, monkeypatch):
    strat = _strategy(tmp_path, monkeypatch)
    engine = _stop_engine(strat, _FakeClient(), strat.home / "sessions" / "session_1")

    async def _hang(*_a, **_kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(closeout_mod, "build_closeout", _hang)
    monkeypatch.setattr(closeout_mod, "EMIT_TIMEOUT_SEC", 0.01)
    report = asyncio.run(emit_closeout(engine))

    assert "did not finish within" in report.error


def test_an_unreachable_api_is_recorded_rather_than_read_as_nothing(
    tmp_path, monkeypatch
):
    strat = _strategy(tmp_path, monkeypatch)
    engine = _stop_engine(strat, None, strat.home / "sessions" / "session_1")

    async def _no_client():
        return None

    engine._get_client = _no_client
    report = asyncio.run(emit_closeout(engine))

    assert "could not reach the API" in report.error
    assert report.clean is False


def test_finish_still_completes_when_the_close_out_fails(tmp_path, monkeypatch):
    """The whole guarantee, through ``_finish`` itself and not around it."""
    strat = _strategy(tmp_path, monkeypatch)
    recorded = []

    class _Supervisor:
        def unregister(self, agent_id, state):
            recorded.append((agent_id, state))

    monkeypatch.setattr("condor.agents.engine._supervisor", lambda: _Supervisor())

    async def _boom(_engine):
        raise RuntimeError("close-out exploded")

    monkeypatch.setattr(closeout_mod, "emit_closeout", _boom)

    async def _reap():
        return None

    stub = SimpleNamespace(
        agent_id=f"{LINEAGE}1",
        strategy=strat,
        config={"execution_mode": "loop"},
        journal=None,
        ledger=None,
        session_dir=None,
        _cancel_outcome=None,
        _reap_client=_reap,
        _cancel_resting_orders=None,
    )
    stub._emit_closeout = lambda: TickEngine._emit_closeout(stub)

    with pytest.raises(RuntimeError):
        # The raise is the *stub's*: it proves _finish reached the emission.
        asyncio.run(TickEngine._finish(stub, "stopped", "user"))
    # And the teardown's own finally still ran, which is what a stop depends on.
    assert recorded == [(f"{LINEAGE}1", "stopped")]


def test_the_real_emission_never_lets_an_error_escape(tmp_path, monkeypatch):
    """``emit_closeout`` is the layer that swallows, so ``_finish`` need not."""
    strat = _strategy(tmp_path, monkeypatch)
    engine = _stop_engine(strat, _FakeClient(), None)

    async def _boom():
        raise RuntimeError("no client for you")

    engine._get_client = _boom
    report = asyncio.run(emit_closeout(engine))
    assert "no client for you" in report.error


def test_an_experiment_emits_nothing(tmp_path, monkeypatch):
    strat = _strategy(tmp_path, monkeypatch)
    engine = _stop_engine(strat, _FakeClient(), None)
    engine.config = {"execution_mode": "dry_run"}
    assert asyncio.run(emit_closeout(engine)) is None


# ── Criterion 6: close everything for an agent with no live session ──


def test_close_everything_runs_against_a_stopped_agent(tmp_path, monkeypatch):
    """No engine, no session: two slugs and a client are the whole input."""
    strat = _strategy(tmp_path, monkeypatch, sessions=(1, 2))
    running = [
        _executor("e-old", session=1, status="RUNNING"),
        _executor("e-new", session=2, status="RUNNING", custom_info=_claims("o1")),
    ]
    executors = _FakeExecutorsAPI({"RUNNING": running, "TERMINATED": []})
    trading = _FakeTradingAPI(active=[_order("o1")])
    client = _FakeClient(executors, trading)

    report = asyncio.run(
        close_out_agent(
            client,
            "acme",
            "scalper",
            policy=ShutdownPolicy(on_kill_switch="flatten_all"),
            session_dir=strat.home / "sessions" / "session_2",
        )
    )

    # A previous session's executor is stopped too, which the per-session stop
    # gate refuses (CORR-726), and its position is not kept.
    assert sorted(executors.stop_calls) == [("e-new", False), ("e-old", False)]
    assert trading.cancel_calls == [("master_account", "gate_io", "o1")]
    assert report.lineage == LINEAGE
    assert (strat.home / "sessions" / "session_2" / "closeout.json").is_file()


def test_close_everything_keeps_spot_under_the_default_policy(tmp_path, monkeypatch):
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {
            "RUNNING": [
                _executor("e-spot", status="RUNNING", connector="gate_io"),
                _executor("e-perp", status="RUNNING", connector="binance_perpetual"),
            ]
        }
    )
    client = _FakeClient(executors, _FakeTradingAPI())
    asyncio.run(close_out_agent(client, "acme", "scalper"))
    assert sorted(executors.stop_calls) == [("e-perp", False), ("e-spot", True)]


def test_a_refused_stop_is_not_reported_as_closed(tmp_path, monkeypatch):
    """The venue read has the last word, not the stop call's own answer."""
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {"RUNNING": [_executor("e1", status="RUNNING")]},
        stop_error=RuntimeError("executor not found"),
    )
    client = _FakeClient(executors, _FakeTradingAPI())
    report = asyncio.run(close_out_agent(client, "acme", "scalper"))
    # Still RUNNING on the re-read, so still LEFT — the refusal did not hide it.
    assert [i.outcome for i in report.of_kind(KIND_EXECUTOR)] == [LEFT]


def test_close_everything_refuses_without_a_lineage():
    report = asyncio.run(close_out_agent(_FakeClient(), "", "scalper"))
    assert "no lineage" in report.error


def test_close_everything_cancels_an_order_a_dead_executor_left_behind(
    tmp_path, monkeypatch
):
    """The stopped-agent case this action exists for.

    Scoped to the live executor rows, a lineage whose executors have all
    terminated gave the sweep no market at all: nothing was cancelled, and the
    report then named the leftover order a few lines later without anything
    having tried to remove it. Stops stay on the live rows — there is nothing
    to stop on a terminated one.
    """
    _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {
            "RUNNING": [],
            "TERMINATED": [
                _executor(
                    "e-done", status="TERMINATED", custom_info=_claims("stranded")
                )
            ],
        }
    )
    trading = _FakeTradingAPI(active=[_order("stranded")])
    client = _FakeClient(executors, trading)

    report = asyncio.run(close_out_agent(client, "acme", "scalper"))

    assert executors.stop_calls == []
    assert trading.cancel_calls == [("master_account", "gate_io", "stranded")]
    assert report.of_kind(KIND_ORDER)


# ── The record itself ──


def test_a_clean_report_says_so():
    report = CloseoutReport(agent_id="a", lineage=LINEAGE, at="now")
    assert report.clean is True
    assert report.summary() == "nothing to close out"
    assert "(none)" in report.render()


def test_a_read_that_did_not_answer_costs_the_report_its_clean():
    """A failed read returns an empty list, so it leaves no row to be
    ``UNCONFIRMABLE``: judged on the items alone, a close-out that never
    checked the held positions reported ``clean`` and "nothing to close out".
    """
    report = CloseoutReport(
        agent_id="a",
        lineage=LINEAGE,
        at="now",
        unanswered=("tracked-position read: connection reset",),
    )

    assert report.clean is False
    assert "did not answer" in report.summary()
    assert report.to_dict()["clean"] is False


def test_the_rendered_report_names_every_section():
    report = CloseoutReport(
        agent_id=f"{LINEAGE}1",
        lineage=LINEAGE,
        at="2026-10-15T00:00:00+00:00",
        stale_records=("gate_io SOL-USDC sell-1: record says OPEN",),
        unanswered=("order read: boom",),
        error="partial",
    )
    text = report.render()
    for heading in ("## Executors", "## Orders", "## Positions", "## Stale local rows"):
        assert heading in text
    assert "## Reads that did not answer" in text
    assert "## Report failure" in text


def test_read_closeout_of_a_session_without_one_is_none(tmp_path):
    assert read_closeout(None) is None
    assert read_closeout(tmp_path) is None


def test_read_closeout_survives_a_corrupt_file(tmp_path):
    (tmp_path / "closeout.json").write_text("{not json")
    assert read_closeout(tmp_path) is None


# ── The agent route ──


def _route_client(user, strategy, client_or_none, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.routes import agents as routes

    monkeypatch.setattr(routes, "_get_strategy", lambda slug, sslug: strategy)
    monkeypatch.setattr(routes, "_strategy_principal", lambda s, u: u.id)
    monkeypatch.setattr(
        routes,
        "_get_client_for_strategy",
        _async_return(
            routes.StrategyClient(
                client_or_none, "local", "" if client_or_none else "unreachable"
            )
        ),
    )
    monkeypatch.setattr(routes, "_require_no_foreign_live_run", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _async_return(value):
    async def _fn(*_a, **_kw):
        return value

    return _fn


def _web_user():
    from condor.web.models import WebUser

    return WebUser(id=7, username="fede", first_name="F", role="user")


def test_the_route_serves_the_stored_closeout_of_a_session(tmp_path, monkeypatch):
    strat = _strategy(tmp_path, monkeypatch)
    closeout_mod.write_closeout(
        strat.home / "sessions" / "session_1",
        CloseoutReport(agent_id=f"{LINEAGE}1", lineage=LINEAGE, at="then"),
    )
    http = _route_client(_web_user(), strat, _FakeClient(), monkeypatch)

    resp = http.get("/agents/acme/strategies/scalper/sessions/1/closeout")
    assert resp.status_code == 200
    assert resp.json()["closeout"]["lineage"] == LINEAGE


def test_a_session_without_a_closeout_answers_null_not_404(tmp_path, monkeypatch):
    strat = _strategy(tmp_path, monkeypatch)
    http = _route_client(_web_user(), strat, _FakeClient(), monkeypatch)

    resp = http.get("/agents/acme/strategies/scalper/sessions/1/closeout")
    assert resp.status_code == 200
    assert resp.json() == {"closeout": None}


def test_the_close_out_action_runs_on_a_stopped_strategy(tmp_path, monkeypatch):
    """No engine is registered, so ``/stop`` would 404 and this still works."""
    strat = _strategy(tmp_path, monkeypatch)
    executors = _FakeExecutorsAPI(
        {
            "RUNNING": [_executor("e1", status="RUNNING", custom_info=_claims("o1"))],
            "TERMINATED": [],
        }
    )
    trading = _FakeTradingAPI(active=[_order("o1")])
    http = _route_client(
        _web_user(), strat, _FakeClient(executors, trading), monkeypatch
    )

    resp = http.post("/agents/acme/strategies/scalper/close-out")
    assert resp.status_code == 200
    body = resp.json()["closeout"]
    assert body["lineage"] == LINEAGE
    assert executors.stop_calls == [("e1", True)]  # spot kept, per the default
    assert trading.cancel_calls == [("master_account", "gate_io", "o1")]
    # Filed beside the newest session's journal.
    assert (strat.home / "sessions" / "session_1" / "closeout.json").is_file()


def test_the_close_out_action_refuses_without_a_client(tmp_path, monkeypatch):
    strat = _strategy(tmp_path, monkeypatch)
    http = _route_client(_web_user(), strat, None, monkeypatch)

    resp = http.post("/agents/acme/strategies/scalper/close-out")
    assert resp.status_code == 503
    assert "unreachable" in resp.json()["detail"]
