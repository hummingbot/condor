"""Unit tests for the drift core-data provider ([[FEAT-113]]).

The provider is I/O and nothing else, so what is tested here is the plumbing:
that a silent venue becomes an ``unanswered`` report instead of a crashed tick,
that the tracked side is fetched unscoped, and that an agent is never told a
sibling's drift is its own.
"""

import asyncio

import pytest

from condor.agents.providers import ProviderRegistry, get_provider, list_core_providers
from condor.agents.providers.drift import DriftProvider, owned_controller_ids


class _Executors:
    def __init__(self, positions, raises=None):
        self.positions = positions
        self.raises = raises
        self.calls = []

    async def get_positions_summary(self, controller_id=None):
        self.calls.append(controller_id)
        if self.raises:
            raise self.raises
        return {"positions": self.positions}


class _Trading:
    def __init__(self, rows, raises=None):
        self.rows = rows
        self.raises = raises

    async def get_positions(self, **kwargs):
        if self.raises:
            raise self.raises
        return {"data": self.rows, "pagination": {"has_more": False}}


class _Client:
    def __init__(self, tracked=(), venue=(), tracked_raises=None, venue_raises=None):
        self.executors = _Executors(list(tracked), tracked_raises)
        self.trading = _Trading(list(venue), venue_raises)


def _held(pair="SOL-PERP", amount=10.0, controller="brigado.mm_1"):
    return {
        "account_name": "master",
        "connector_name": "binance_perpetual",
        "trading_pair": pair,
        "position_side": "LONG",
        "net_amount_base": amount,
        "buy_breakeven_price": 100.0,
        "controller_id": controller,
    }


def _venue_row(pair="SOL-PERP", amount=10.0):
    return {
        "account_name": "master",
        "connector_name": "binance_perpetual",
        "trading_pair": pair,
        "side": "LONG",
        "amount": amount,
        "entry_price": 100.0,
    }


def _run(client, agent_id="brigado.mm_1"):
    return asyncio.run(DriftProvider().execute(client, {}, agent_id=agent_id))


# ── Registration ──


def test_drift_is_a_registered_core_provider():
    provider = get_provider("drift")
    assert provider is not None
    assert provider.is_core
    assert "drift" in {p.name for p in list_core_providers()}


# ── The happy path ──


def test_agreeing_books_report_trusted_and_no_drift():
    result = _run(_Client(tracked=[_held()], venue=[_venue_row()]))
    assert result.name == "drift"
    assert result.data["trusted"] is True
    assert result.data["drifting"] == 0
    assert result.data["worst_quote"] is None
    assert "agreed" in result.summary


def test_the_tracked_side_is_fetched_unscoped():
    """The venue answers for the whole account, so the book must too."""
    client = _Client(tracked=[_held()], venue=[_venue_row()])
    _run(client)
    assert client.executors.calls == [None]


def test_a_mismatch_reaches_the_summary_and_the_worst_quote():
    result = _run(
        _Client(tracked=[_held(amount=100.0)], venue=[_venue_row(amount=50.0)])
    )
    assert result.data["drifting"] == 1
    assert result.data["worst_quote"] == 5000.0
    assert "MISMATCH" in result.summary
    assert "SOL-PERP" in result.summary


# ── A venue that does not answer ──


def test_a_venue_exception_yields_unanswered_and_not_a_crash():
    client = _Client(tracked=[_held()], venue_raises=RuntimeError("connection reset"))
    result = _run(client)
    assert result.data["trusted"] is False
    assert "connection reset" in result.data["reason"]
    assert [r["verdict"] for r in result.data["report"]["rows"]] == ["unanswered"]
    assert "DID NOT ANSWER" in result.summary


def test_the_unanswered_reason_is_clipped():
    client = _Client(tracked=[], venue_raises=RuntimeError("x" * 400))
    result = _run(client)
    assert len(result.data["reason"]) <= 120


def test_a_failing_tracked_fetch_degrades_through_the_registry():
    """``run_core_providers`` catches per provider — the tick still runs."""
    client = _Client(tracked_raises=RuntimeError("api down"), venue=[_venue_row()])
    with pytest.raises(RuntimeError):
        _run(client)

    results = asyncio.run(ProviderRegistry().run_core_providers(client, {}))
    assert results["drift"].summary == "(provider drift failed)"
    assert results["drift"].data == {}


# ── "Yours": the annotation, never a filter ──


def test_owned_controller_ids_matches_exactly_and_by_separator():
    tracked = [
        _held(controller="brigado.mm_1"),
        _held(pair="A", controller="brigado.mm_1_sub"),
        _held(pair="B", controller="brigado.mm_10"),  # a sibling, not a suffix
        _held(pair="C", controller="other.strat_2"),
    ]
    assert owned_controller_ids("brigado.mm_1", tracked) == [
        "brigado.mm_1",
        "brigado.mm_1_sub",
    ]


def test_no_agent_id_claims_nothing():
    assert owned_controller_ids("", [_held()]) == []


def test_a_siblings_drift_is_never_reported_as_your_own():
    client = _Client(
        tracked=[
            _held(pair="MINE-PERP", amount=100.0, controller="brigado.mm_1"),
            _held(pair="THEIRS-PERP", amount=100.0, controller="other.strat_2"),
        ],
        venue=[],
    )
    result = _run(client, agent_id="brigado.mm_1")

    # The account's whole drift is reported...
    assert result.data["drifting"] == 2
    assert "MINE-PERP" in result.summary and "THEIRS-PERP" in result.summary
    # ...and exactly one row is claimed.
    assert result.data["mine"] == ["brigado.mm_1"]
    assert result.summary.count("← yours") == 1
    assert "1 of 2 involves your controllers." in result.summary
    # The gate reads only what this agent is party to.
    assert result.data["worst_quote"] == 10000.0


def test_an_agent_with_no_rows_of_its_own_gets_no_gate_signal():
    client = _Client(
        tracked=[_held(controller="other.strat_2", amount=100.0)], venue=[]
    )
    result = _run(client, agent_id="brigado.mm_1")
    assert result.data["mine"] == []
    assert result.data["worst_quote"] is None
    assert result.data["drifting"] == 1
