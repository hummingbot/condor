"""Unit tests for the drift core-data provider ([[FEAT-113]]).

The provider is I/O and nothing else, so what is tested here is the plumbing:
that a silent venue becomes an ``unanswered`` report instead of a crashed tick,
that the tracked side is fetched unscoped, and that an agent is never told a
sibling's drift is its own.
"""

import asyncio
import logging

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from condor.agents.providers import ProviderRegistry, get_provider, list_core_providers
from condor.agents.providers.drift import DriftProvider, owned_controller_ids


class _Executors:
    def __init__(
        self,
        positions,
        raises=None,
        running=(),
        running_raises=None,
        shutting=(),
        shutting_raises=None,
    ):
        self.positions = positions
        self.raises = raises
        self.calls = []
        self.by_status = {"RUNNING": list(running), "SHUTTING_DOWN": list(shutting)}
        self.raises_by_status = {
            "RUNNING": running_raises,
            "SHUTTING_DOWN": shutting_raises,
        }
        self.search_calls = []

    async def get_positions_summary(self, controller_id=None):
        self.calls.append(controller_id)
        if self.raises:
            raise self.raises
        return {"positions": self.positions}

    async def search_executors(self, **kwargs):
        self.search_calls.append(kwargs)
        status = kwargs.get("status")
        if self.raises_by_status.get(status):
            raise self.raises_by_status[status]
        rows = self.by_status.get(status, [])
        return {"data": rows, "pagination": {"has_more": False}}


class _Trading:
    def __init__(self, rows, raises=None):
        self.rows = rows
        self.raises = raises

    async def get_positions(self, **kwargs):
        if self.raises:
            raise self.raises
        return {"data": self.rows, "pagination": {"has_more": False}}


class _Client:
    def __init__(
        self,
        tracked=(),
        venue=(),
        tracked_raises=None,
        venue_raises=None,
        running=(),
        running_raises=None,
        shutting=(),
        shutting_raises=None,
    ):
        self.executors = _Executors(
            list(tracked),
            tracked_raises,
            running,
            running_raises,
            shutting,
            shutting_raises,
        )
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


def _running_grid(pair="SOL-PERP", base=0.51, price=100.0, controller="brigado.mm_1"):
    """A running LONG grid as ``search_executors`` returns it, with fills."""
    return {
        "executor_id": "j5B2VAi4",
        "executor_type": "grid_executor",
        "account_name": "master",
        "connector_name": "binance_perpetual",
        "trading_pair": pair,
        "controller_id": controller,
        "status": "RUNNING",
        "side": "BUY",
        "filled_amount_quote": 90.0,
        "custom_info": {
            "side": "TradeType.BUY",
            "position_size_quote": base * price,
            "current_position_average_price": price,
        },
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


def test_active_executors_are_fetched_account_wide_running_and_shutting_down():
    """The API filters on one status, so RUNNING and SHUTTING_DOWN are one read
    each (CORR-710) — and nothing else: a stopped executor's fills are a hold."""
    client = _Client(tracked=[_held()], venue=[_venue_row()])
    _run(client)
    statuses = sorted(c["status"] for c in client.executors.search_calls)
    assert statuses == ["RUNNING", "SHUTTING_DOWN"]
    for call in client.executors.search_calls:
        assert "controller_ids" not in call and "account_names" not in call


# ── CORR-708: a running executor's inventory is tracked, not an orphan ──


def test_a_running_grids_fills_agree_with_the_venue_and_are_yours():
    client = _Client(
        tracked=[], venue=[_venue_row(amount=0.51)], running=[_running_grid()]
    )
    result = _run(client)
    rows = result.data["report"]["rows"]
    assert [r["verdict"] for r in rows] == ["agreed"]
    assert rows[0]["controller_ids"] == ("brigado.mm_1",)
    assert result.data["mine"] == ["brigado.mm_1"]
    assert result.data["drifting"] == 0
    assert "ORPHAN" not in result.summary


def test_a_venue_position_no_executor_explains_is_still_an_orphan():
    client = _Client(
        tracked=[_held(pair="BTC-PERP", amount=1.0)],
        venue=[_venue_row(pair="BTC-PERP", amount=1.0), _venue_row(amount=0.51)],
        running=[_running_grid(pair="ETH-PERP")],
    )
    result = _run(client)
    verdicts = {r["pair"]: r["verdict"] for r in result.data["report"]["rows"]}
    assert verdicts == {"BTC-PERP": "agreed", "ETH-PERP": "ghost", "SOL-PERP": "orphan"}
    assert "ORPHAN" in result.summary


def test_a_failed_running_executors_read_fails_the_provider():
    """Never half a book: the fills would read as orphans, or worse, as agreed."""
    client = _Client(
        tracked=[_held()],
        venue=[_venue_row()],
        running_raises=RuntimeError("executors down"),
    )
    with pytest.raises(RuntimeError, match="executors down"):
        _run(client)

    results = asyncio.run(ProviderRegistry().run_core_providers(client, {}))
    assert results["drift"].summary == "(provider drift failed)"
    assert results["drift"].data == {}


def test_a_failed_shutting_down_read_fails_the_provider_not_agreed():
    """Its fills are on the venue: losing the read must not score them."""
    client = _Client(
        tracked=[],
        venue=[_venue_row(amount=0.51)],
        running=[_running_grid()],
        shutting_raises=RuntimeError("shutting read down"),
    )
    with pytest.raises(RuntimeError, match="shutting read down"):
        _run(client)

    results = asyncio.run(ProviderRegistry().run_core_providers(client, {}))
    assert results["drift"].summary == "(provider drift failed)"
    assert results["drift"].data == {}


def test_a_shutting_down_grids_fills_are_tracked_not_an_orphan():
    stopping = dict(_running_grid(), status="SHUTTING_DOWN")
    client = _Client(tracked=[], venue=[_venue_row(amount=0.51)], shutting=[stopping])
    result = _run(client)
    rows = result.data["report"]["rows"]
    assert [r["verdict"] for r in rows] == ["agreed"]
    assert rows[0]["controller_ids"] == ("brigado.mm_1",)
    assert "ORPHAN" not in result.summary


def test_a_spot_hold_is_not_reported_as_a_ghost():
    """The venue side is perps only, so a spot hold can never be verified (CORR-711)."""
    spot = dict(_held(pair="SOL-USDT", controller="brigado"), connector_name="binance")
    result = _run(_Client(tracked=[spot], venue=[]), agent_id="brigado")
    assert result.data["drifting"] == 0
    assert result.data["worst_quote"] is None
    assert "GHOST" not in result.summary


def test_an_unmeasurable_running_executor_is_named_and_leaves_the_orphan():
    dca = dict(_running_grid(), executor_type="dca_executor", executor_id="dca_1")
    client = _Client(tracked=[], venue=[_venue_row(amount=0.51)], running=[dca])
    result = _run(client)
    assert [r["verdict"] for r in result.data["report"]["rows"]] == ["orphan"]
    assert result.data["report"]["unmeasured"] == ("dca_1 (dca SOL-PERP)",)
    assert "dca_1 (dca SOL-PERP)" in result.summary


def test_a_mismatch_reaches_the_summary_and_the_worst_quote():
    result = _run(
        _Client(tracked=[_held(amount=100.0)], venue=[_venue_row(amount=50.0)])
    )
    assert result.data["drifting"] == 1
    assert result.data["worst_quote"] == 5000.0
    assert "MISMATCH" in result.summary
    assert "SOL-PERP" in result.summary


# ── A venue that does not answer ──


def _http_error(status=500, message="Internal Server Error"):
    """What the hummingbot client raises: ``str()`` carries the backend URL."""
    url = URL("http://10.0.0.5:8000/x")
    info = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.ClientResponseError(info, (), status=status, message=message)


def test_a_venue_exception_yields_unanswered_and_not_a_crash(caplog):
    client = _Client(tracked=[_held()], venue_raises=_http_error())
    with caplog.at_level(logging.WARNING, logger="condor.agents.providers.drift"):
        result = _run(client)
    assert result.data["trusted"] is False
    assert result.data["reason"] == "Internal Server Error"
    assert [r["verdict"] for r in result.data["report"]["rows"]] == ["unanswered"]
    assert "DID NOT ANSWER" in result.summary
    assert "10.0.0.5" not in result.summary
    # The URL is kept out of the prompt, not lost: it reaches the server log.
    records = [r for r in caplog.records if r.name == "condor.agents.providers.drift"]
    assert len(records) == 1
    assert "10.0.0.5" in logging.Formatter().formatException(records[0].exc_info)


def test_an_unreachable_venue_reason_is_the_generic_line():
    client = _Client(
        tracked=[_held()],
        venue_raises=aiohttp.ClientConnectionError("connection reset"),
    )
    result = _run(client)
    assert result.data["reason"] == "the trading API is unreachable"


def test_the_unanswered_reason_is_clipped():
    client = _Client(tracked=[], venue_raises=_http_error(422, "x" * 400))
    result = _run(client)
    assert result.data["reason"].startswith("x")
    assert len(result.data["reason"]) <= 120


def test_a_failing_tracked_fetch_degrades_through_the_registry():
    """``run_core_providers`` catches per provider — the tick still runs."""
    client = _Client(tracked_raises=RuntimeError("api down"), venue=[_venue_row()])
    with pytest.raises(RuntimeError):
        _run(client)

    results = asyncio.run(ProviderRegistry().run_core_providers(client, {}))
    assert results["drift"].summary == "(provider drift failed)"
    assert results["drift"].data == {}


def test_run_core_providers_is_the_registrys_only_runner():
    """READ-684: no by-name runner that would call ``execute`` without the agent scope."""
    runners = [n for n in vars(ProviderRegistry) if n.startswith("run_")]
    assert runners == ["run_core_providers"]


# ── PERF-640: independent reads go out together ──


class _GatedProvider:
    """A core provider that finishes only once all three have started."""

    is_core = True

    def __init__(self, name, started, all_started, raises=None):
        self.name = name
        self.started = started
        self.all_started = all_started
        self.raises = raises

    async def execute(self, client, config, **kwargs):
        from condor.agents.providers.base import ProviderResult

        self.started.append(self.name)
        if len(self.started) == 3:
            self.all_started.set()
        await self.all_started.wait()
        if self.raises:
            raise self.raises
        return ProviderResult(name=self.name, data={"ok": True}, summary=self.name)


def test_core_providers_run_concurrently(monkeypatch):
    """A serial loop would wait forever on the first provider's gate."""
    import condor.agents.providers as providers

    async def scenario():
        started: list[str] = []
        gate = asyncio.Event()
        fakes = [
            _GatedProvider("executors", started, gate),
            _GatedProvider("positions", started, gate, raises=RuntimeError("boom")),
            _GatedProvider("drift", started, gate),
        ]
        monkeypatch.setattr(providers, "list_core_providers", lambda: fakes)
        return await asyncio.wait_for(
            ProviderRegistry().run_core_providers(object(), {}), timeout=2
        )

    results = asyncio.run(scenario())
    # One entry per provider, in registration order, a failure isolated to its own.
    assert list(results) == ["executors", "positions", "drift"]
    assert results["executors"].data == {"ok": True}
    assert results["positions"].summary == "(provider positions failed)"
    assert results["positions"].data == {}
    assert results["drift"].data == {"ok": True}


class _GatedClient(_Client):
    """Both drift reads block until the other one has been sent."""

    def __init__(self):
        super().__init__(tracked=[_held()], venue=[_venue_row()])
        self.gate = asyncio.Event()
        self.entered = 0
        for api, method in (
            (self.executors, "get_positions_summary"),
            (self.trading, "get_positions"),
        ):
            setattr(api, method, self._gated(getattr(api, method)))

    def _gated(self, call):
        async def wrapper(*args, **kwargs):
            self.entered += 1
            if self.entered == 2:
                self.gate.set()
            await self.gate.wait()
            return await call(*args, **kwargs)

        return wrapper


def test_the_drift_reads_run_concurrently():
    async def scenario():
        client = _GatedClient()
        return await asyncio.wait_for(
            DriftProvider().execute(client, {}, agent_id="brigado.mm_1"), timeout=2
        )

    result = asyncio.run(scenario())
    assert result.data["trusted"] is True
    assert result.data["drifting"] == 0


def test_a_failing_tracked_fetch_raises_even_when_the_venue_also_fails():
    client = _Client(
        tracked_raises=RuntimeError("book down"), venue_raises=_http_error()
    )
    with pytest.raises(RuntimeError, match="book down"):
        _run(client)


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


def test_run_core_providers_narrows_to_the_named_providers(monkeypatch):
    """PERF-641: the winddown asks for ``executors`` alone, with the full scope."""
    import condor.agents.providers as providers
    from condor.agents.providers.base import ProviderResult

    seen: list[tuple[str, dict]] = []

    class _Recording:
        is_core = True

        def __init__(self, name):
            self.name = name

        async def execute(self, client, config, **kwargs):
            seen.append((self.name, kwargs))
            return ProviderResult(name=self.name, data={}, summary=self.name)

    fakes = [_Recording(n) for n in ("executors", "positions", "drift")]
    monkeypatch.setattr(providers, "list_core_providers", lambda: fakes)

    results = asyncio.run(
        ProviderRegistry().run_core_providers(
            object(),
            {},
            agent_id="acme.s_1",
            bot_names=["bot_a"],
            owned=["rec"],
            names=("executors",),
        )
    )
    assert list(results) == ["executors"]
    assert seen == [
        (
            "executors",
            {"agent_id": "acme.s_1", "bot_names": ["bot_a"], "owned": ["rec"]},
        )
    ]

    seen.clear()
    assert list(asyncio.run(ProviderRegistry().run_core_providers(object(), {}))) == [
        "executors",
        "positions",
        "drift",
    ]
