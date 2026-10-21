"""A new session adopts the executors its predecessor left running (CORR-726).

The most expensive defect of the Agent Builder Cup: an LP executor opened by a
*previous* session of the same agent kept polling every second and consumed the
host's whole RPC budget, and the live session could not stop it. An executor's
``controller_id`` is the session tag ``"{agent}.{strategy}_{N}"``, the stop gate
allows only executors in this tick's own snapshot, and that snapshot is fetched
under this session's own tag — so a predecessor's executors can never be in it.
A graceful stop leaves them running by design, so the ordinary restart produces
the orphan.

What is pinned here: lineage parsing (another agent, another strategy, an
experiment and the backend's ``"main"`` default are never this strategy's), the
tick's adoption pass with its dead-owner conjunction, the stop gate answering
``""`` for an adopted orphan and still refusing a live sibling's executor, and
boot reconciliation reporting a RUNNING-with-no-owner executor as its own kind
of orphan — not the terminated-with-position case ``list_orphaned_positions``
already covers.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.executor_adoption import (
    lineage_session_num,
    orphaned_running_rows,
    session_lineage,
)
from condor.agents.risk import _stop_refusal

AGENT = "solana_dex_lp_expert"
STRATEGY = "lp_slot_operator"
DEAD_TAG = f"{AGENT}.{STRATEGY}_1"
LIVE_TAG = f"{AGENT}.{STRATEGY}_2"


def _row(executor_id: str, controller_id: str, status: str = "RUNNING") -> dict:
    return {
        "id": executor_id,
        "status": status,
        "controller_id": controller_id,
        "type": "lp_executor",
        "trading_pair": "SOL-USDC",
        "config": {"controller_id": "main", "trading_pair": "SOL-USDC"},
    }


# ── The lineage helpers ────────────────────────────────────────────────


def test_session_lineage_is_the_tag_prefix():
    assert session_lineage(AGENT, STRATEGY) == f"{AGENT}.{STRATEGY}_"
    assert session_lineage("", STRATEGY) == ""


@pytest.mark.parametrize(
    "tag",
    [
        "main",  # the backend's default, left inside config
        "",
        f"{AGENT}.{STRATEGY}_e3",  # an experiment never trades for real
        f"{AGENT}.{STRATEGY}_",
        f"{AGENT}.other_loop_1",  # same agent, different strategy
        f"other_agent.{STRATEGY}_1",  # different agent
        f"{AGENT}.{STRATEGY}x_1",  # a strategy whose slug merely starts with ours
    ],
)
def test_foreign_tags_have_no_session_number(tag):
    assert lineage_session_num(tag, AGENT, STRATEGY) is None


def test_own_lineage_yields_the_session_number():
    assert lineage_session_num(DEAD_TAG, AGENT, STRATEGY) == 1
    assert lineage_session_num(f"{AGENT}.{STRATEGY}_42", AGENT, STRATEGY) == 42


def test_orphans_are_this_lineage_running_and_unowned():
    rows = [
        _row("dead-1", DEAD_TAG),
        _row("live-1", LIVE_TAG),
        _row("other-agent", f"other_agent.{STRATEGY}_1"),
        _row("other-strategy", f"{AGENT}.other_loop_1"),
        _row("terminated", f"{AGENT}.{STRATEGY}_3", status="TERMINATED"),
        _row("", DEAD_TAG),
        "not a row",
    ]

    found = orphaned_running_rows(rows, AGENT, STRATEGY, live_sessions={2})

    assert [r["id"] for r in found] == ["dead-1"]


def test_a_live_owner_keeps_its_executors():
    rows = [_row("dead-1", DEAD_TAG)]
    assert orphaned_running_rows(rows, AGENT, STRATEGY, live_sessions={1, 2}) == []


# ── The tick's adoption pass ───────────────────────────────────────────


class _Client:
    """Stands in for the API client, serving one page of running executors."""

    def __init__(self, rows):
        self._rows = rows
        self.executors = self
        self.searches: list[dict] = []

    async def search_executors(self, **kwargs):
        self.searches.append(kwargs)
        return list(self._rows)

    async def get_executor(self, executor_id: str, **kwargs):  # pragma: no cover
        raise AssertionError("an adopted executor must not need a round trip")


def _engine(tmp_path, monkeypatch, rows, live_nums=(), *, registered=True):
    """A session-2 engine of ``AGENT.STRATEGY`` with its own fake supervisor."""
    from condor.agents import engine as engine_mod
    from condor.agents.agent import Agent
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug=AGENT, name="LP Slot Operator")
    strategy.home.mkdir(parents=True, exist_ok=True)
    # The predecessor's session dir, so this engine mints session 2 and
    # ``DEAD_TAG`` really is somebody else's.
    (strategy.home / "sessions" / "session_1").mkdir(parents=True, exist_ok=True)
    engine = engine_mod.TickEngine(
        agent=Agent(slug=AGENT, name="LP Expert"),
        strategy=strategy,
        config={"execution_mode": "loop"},
        chat_id=1,
        user_id=1,
    )
    assert engine.strategy.slug == STRATEGY
    assert engine.agent_id == LIVE_TAG

    live = [
        SimpleNamespace(
            agent=engine.agent, strategy=engine.strategy, session_num=int(n)
        )
        for n in live_nums
    ]
    if registered:
        live.append(engine)
    monkeypatch.setattr(
        engine_mod,
        "_supervisor",
        lambda: SimpleNamespace(for_strategy=lambda a, s: list(live)),
    )
    return engine, _Client(rows)


def test_an_orphan_is_adopted_and_the_gate_then_allows_the_stop(tmp_path, monkeypatch):
    engine, client = _engine(tmp_path, monkeypatch, [_row("dead-1", DEAD_TAG)])

    asyncio.run(engine._adopt_running_executors(client))

    assert client.searches and client.searches[0]["status"] == "RUNNING"
    assert [r["id"] for r in engine._adopted_executors] == ["dead-1"]
    owners = engine._executor_owners()
    assert owners["dead-1"] == DEAD_TAG
    # The gate's own membership test, unchanged, now answers "yes" — and
    # without a round trip (``_Client.get_executor`` would fail the test).
    assert asyncio.run(_stop_refusal("dead-1", engine.agent_id, owners, client)) == ""
    # And the agent is told the id exists, or it could never pass it.
    summary = engine._adopted_executors_summary()
    assert "dead-1" in summary and "ADOPTED" in summary


def test_a_live_siblings_executor_is_not_adopted_and_stays_refused(
    tmp_path, monkeypatch
):
    engine, client = _engine(
        tmp_path, monkeypatch, [_row("dead-1", DEAD_TAG)], live_nums=(1,)
    )

    asyncio.run(engine._adopt_running_executors(client))

    assert engine._adopted_executors == []
    owners = engine._executor_owners()
    assert "dead-1" not in owners
    refusal = asyncio.run(
        _stop_refusal("dead-1", engine.agent_id, owners, _ApiSaying(DEAD_TAG))
    )
    assert "belongs to controller" in refusal and DEAD_TAG in refusal
    assert engine._adopted_executors_summary() == ""


class _ApiSaying:
    """The API's answer for the gate's fallback single-executor lookup."""

    def __init__(self, controller_id: str):
        self._controller_id = controller_id
        self.executors = self

    async def get_executor(self, executor_id: str, **kwargs):
        return {"id": executor_id, "controller_id": self._controller_id}


def test_another_agent_or_strategy_is_never_adopted(tmp_path, monkeypatch):
    engine, client = _engine(
        tmp_path,
        monkeypatch,
        [
            _row("other-agent", f"other_agent.{STRATEGY}_1"),
            _row("other-strategy", f"{AGENT}.other_loop_1"),
            _row("experiment", f"{AGENT}.{STRATEGY}_e1"),
            _row("backend-default", "main"),
        ],
    )

    asyncio.run(engine._adopt_running_executors(client))

    assert engine._adopted_executors == []


def test_adoption_is_skipped_while_the_registry_cannot_answer(tmp_path, monkeypatch):
    """Not registered means the liveness authority is unreadable — adopt nothing."""
    engine, client = _engine(
        tmp_path, monkeypatch, [_row("dead-1", DEAD_TAG)], registered=False
    )

    asyncio.run(engine._adopt_running_executors(client))

    assert engine._adopted_executors == []
    assert client.searches == [], "no API read when the answer cannot be trusted"


def test_a_failed_read_adopts_nothing_and_does_not_raise(tmp_path, monkeypatch):
    engine, _ = _engine(tmp_path, monkeypatch, [])

    class _Broken:
        executors = property(lambda self: self)

        async def search_executors(self, **kwargs):
            raise RuntimeError("API unreachable")

    asyncio.run(engine._adopt_running_executors(_Broken()))

    assert engine._adopted_executors == []


def test_an_adopted_orphan_is_dropped_once_its_owner_is_back(tmp_path, monkeypatch):
    engine, client = _engine(tmp_path, monkeypatch, [_row("dead-1", DEAD_TAG)])
    asyncio.run(engine._adopt_running_executors(client))
    assert engine._adopted_executors

    # Session 1 is running again (a restart brought it back): re-derived, not
    # latched, so the stop gate stops allowing it on the very next tick.
    from condor.agents import engine as engine_mod

    monkeypatch.setattr(
        engine_mod,
        "_supervisor",
        lambda: SimpleNamespace(
            for_strategy=lambda a, s: [
                engine,
                SimpleNamespace(
                    agent=engine.agent, strategy=engine.strategy, session_num=1
                ),
            ]
        ),
    )
    asyncio.run(engine._adopt_running_executors(client))

    assert engine._adopted_executors == []
    assert "dead-1" not in engine._executor_owners()


# ── Boot reconciliation ────────────────────────────────────────────────


def test_boot_reports_a_running_executor_whose_owner_is_gone():
    from condor.runtime.loops import LoopSupervisor

    supervisor = LoopSupervisor()
    rows = [
        _row("dead-1", DEAD_TAG),
        # The *other* orphan: terminated, position stranded on-chain. That one
        # is list_orphaned_positions' case and must not show up here.
        {
            **_row("terminated-1", DEAD_TAG, status="TERMINATED"),
            "custom_info": {"position_address": "pos-abc"},
        },
    ]
    client = _Client(rows)

    async def factory(status):
        assert status["chat_id"] == 77
        return client

    found = asyncio.run(
        supervisor._scan_orphaned_executors(
            {(AGENT, STRATEGY): {"chat_id": 77, "user_id": 5}},
            client_factory=factory,
        )
    )

    assert [o.executor_id for o in found] == ["dead-1"]
    orphan = found[0]
    assert orphan.session_num == 1
    assert orphan.controller_id == DEAD_TAG
    assert orphan.chat_id == 77
    assert "still" not in orphan.label  # the wording lives on the report
    assert "lp_executor" in orphan.label and "SOL-USDC" in orphan.label


def test_boot_does_not_report_an_executor_whose_session_is_live():
    from condor.runtime.loops import LoopSupervisor

    supervisor = LoopSupervisor()
    supervisor._engines[DEAD_TAG] = SimpleNamespace(
        agent=SimpleNamespace(slug=AGENT),
        strategy=SimpleNamespace(slug=STRATEGY),
        session_num=1,
    )

    async def factory(status):
        return _Client([_row("dead-1", DEAD_TAG)])

    found = asyncio.run(
        supervisor._scan_orphaned_executors(
            {(AGENT, STRATEGY): {"chat_id": 77}}, client_factory=factory
        )
    )

    assert found == []


def test_boot_scan_survives_an_unreachable_server():
    from condor.runtime.loops import LoopSupervisor

    async def factory(status):
        raise RuntimeError("no client for this server")

    found = asyncio.run(
        LoopSupervisor()._scan_orphaned_executors(
            {(AGENT, STRATEGY): {"chat_id": 77}}, client_factory=factory
        )
    )

    assert found == []


def test_the_report_summary_names_the_orphans():
    from condor.runtime.loops import OrphanedExecutor, ReconcileReport

    report = ReconcileReport()
    assert report.summary() == "", "a quiet boot still says nothing"

    report.orphaned_executors.append(
        OrphanedExecutor(
            executor_id="dead-1",
            controller_id=DEAD_TAG,
            agent_slug=AGENT,
            strategy_slug=STRATEGY,
            session_num=1,
            pair="SOL-USDC",
            kind="lp_executor",
        )
    )

    summary = report.summary()
    assert "dead-1" in summary
    assert "still RUNNING with no live session" in summary


# ── The book and the winddown, not only the prompt ──


def _book_engine(
    adopted, own_rows, own_exposure, *, adopted_unpriced=(), own_unpriced=()
):
    """A hand-built engine at the seam where the tick assembles its risk book.

    Same construction as ``tests/test_journal_read_cache.py``: the assembly is
    inside ``_gather_tick_context``, and what is under test is the arithmetic it
    does, not a tick.
    """
    from condor.agents import engine as engine_mod
    from condor.agents.risk import RiskEngine, RiskLimits

    engine = engine_mod.TickEngine.__new__(engine_mod.TickEngine)
    engine.agent_id = LIVE_TAG
    engine.config = {}
    engine.journal = None
    engine.ledger = None
    engine.risk = RiskEngine(RiskLimits(max_open_executors=5))
    engine.is_experiment = False
    engine._last_block_reason = ""
    engine._adopted_executors = list(adopted)
    engine._adopted_unpriced = list(adopted_unpriced)
    engine._last_skill_data = {
        "executors": list(own_rows),
        "total_exposure": own_exposure,
        "unpriced_exposure": list(own_unpriced),
    }

    async def _no_adopt(client=None):
        return None

    class _NoProviders:
        async def run_core_providers(self, *args, **kwargs):
            return {}

    captured = {}

    def _capture_prompt(core_data_summaries, risk_state, live_open_count):
        captured["state"] = risk_state
        captured["summaries"] = core_data_summaries
        return "prompt"

    engine._adopt_running_bots = _no_adopt
    engine._adopt_running_executors = _no_adopt
    engine.provider_registry = _NoProviders()
    engine._build_prompt = _capture_prompt
    return engine, captured


def test_an_adopted_orphan_counts_against_the_limits():
    """Inherited exposure is exposure.

    The adopted rows reached the prompt and the stop allowlist but not the
    book: the count and the total came from the provider alone, which reads
    only this session's own ``controller_id``. A restarted session holding
    three inherited executors could therefore open its whole
    ``max_open_executors`` on top of them and never be refused.
    """
    engine, captured = _book_engine(
        adopted=[{"id": "dead-1", "controller_id": DEAD_TAG, "amount": 400.0}],
        own_rows=[{"id": "live-1"}],
        own_exposure=100.0,
    )

    ctx = asyncio.run(engine._gather_tick_context(client=None))

    assert ctx is not None
    assert captured["state"].executor_count == 2
    assert captured["state"].total_exposure == pytest.approx(500.0)
    # And it is still named in the prompt, which is how the model can close it.
    assert "dead-1" in captured["summaries"]["adopted_executors"]


def test_a_session_with_nothing_adopted_reads_exactly_the_provider():
    engine, captured = _book_engine(
        adopted=[], own_rows=[{"id": "live-1"}], own_exposure=100.0
    )

    asyncio.run(engine._gather_tick_context(client=None))

    assert captured["state"].executor_count == 1
    assert captured["state"].total_exposure == pytest.approx(100.0)
    assert "adopted_executors" not in captured["summaries"]


def test_an_inherited_position_fills_the_whole_cap_on_its_own():
    """The failure CORR-735 names, at the two numbers it names it with.

    One inherited $5,000 position, a $6,000 position cap and room for three
    executors: read from the provider alone the book says zero on both, so a
    fresh $6,000 allowance is approved on top of the $5,000 and real exposure
    reaches $11,000 against a $6,000 cap.
    """
    engine, captured = _book_engine(
        adopted=[{"id": "dead-1", "controller_id": DEAD_TAG, "amount": 5000.0}],
        own_rows=[],
        own_exposure=0.0,
    )

    asyncio.run(engine._gather_tick_context(client=None))
    state = captured["state"]

    assert state.executor_count == 1
    assert state.total_exposure == pytest.approx(5000.0)

    from condor.agents.risk import RiskEngine, RiskLimits

    allowed, reason = RiskEngine(
        RiskLimits(max_open_executors=3, max_position_size_quote=6000.0)
    ).check_executor_action(
        {"tool": "create_position_executor", "input": {"amount": 0.02}}, state, 2000.0
    )

    assert not allowed
    assert "position limit" in reason


def test_the_inherited_and_the_own_halves_are_summed_in_one_unit():
    engine, captured = _book_engine(
        adopted=[{"id": "dead-1", "controller_id": DEAD_TAG, "amount": 5000.0}],
        own_rows=[{"id": "live-1", "amount": 1000.0}],
        own_exposure=1000.0,
    )

    asyncio.run(engine._gather_tick_context(client=None))

    assert captured["state"].executor_count == 2
    assert captured["state"].total_exposure == pytest.approx(6000.0)


def test_an_inherited_row_nothing_could_price_refuses_creates():
    """An unvalued inherited row is exposure of unknown size, not $0.

    Same answer the provider's own pending rows already get: the book cannot
    size a create against an unknown, so it refuses them by name instead of
    sizing the next one against a zero.
    """
    engine, captured = _book_engine(
        adopted=[{"id": "dead-1", "controller_id": DEAD_TAG, "amount": 0.0}],
        own_rows=[],
        own_exposure=0.0,
        adopted_unpriced=["dead-1"],
    )

    asyncio.run(engine._gather_tick_context(client=None))
    state = captured["state"]

    assert not state.book_trusted
    assert "dead-1" in state.drift_reason

    from condor.agents.risk import RiskEngine, RiskLimits

    allowed, reason = RiskEngine(
        RiskLimits(max_open_executors=3, max_position_size_quote=6000.0)
    ).check_executor_action(
        {"tool": "create_position_executor", "input": {"amount": 0.02}}, state, 2000.0
    )

    assert not allowed
    assert "Book untrusted" in reason and "dead-1" in reason


def test_the_two_unpriced_halves_are_unioned_once_each():
    engine, captured = _book_engine(
        adopted=[{"id": "dead-1", "controller_id": DEAD_TAG, "amount": 0.0}],
        own_rows=[{"id": "hype-1"}],
        own_exposure=0.0,
        adopted_unpriced=["dead-1"],
        own_unpriced=["hype-1"],
    )

    asyncio.run(engine._gather_tick_context(client=None))
    reason = captured["state"].drift_reason

    assert "dead-1" in reason and "hype-1" in reason
    assert reason.count("dead-1") == 1


def test_a_pending_inherited_order_is_priced_during_adoption(tmp_path, monkeypatch):
    """The rows the provider never sees are priced the way it prices its own.

    ``build_executor_row`` is pure, so a market order still pending reports no
    entry, no limit price and no fill and is sized at 0. The provider resolves
    that for its own rows; an adopted orphan is under a dead session's tag and
    reaches the book from here instead.
    """
    row = _row("dead-1", DEAD_TAG)
    row["config"].update({"amount": 2.0, "connector_name": "hyperliquid_perpetual"})
    row["config"]["trading_pair"] = "HYPE-USD"
    engine, client = _engine(tmp_path, monkeypatch, [row])

    async def _price(client, connector_name="", trading_pair="", **_kw):
        assert (connector_name, trading_pair) == ("hyperliquid_perpetual", "HYPE-USD")
        return 50.0

    monkeypatch.setattr("condor.fetchers.market_data.fetch_current_price", _price)

    asyncio.run(engine._adopt_running_executors(client))

    assert [r["amount"] for r in engine._adopted_executors] == [pytest.approx(100.0)]
    assert engine._adopted_unpriced == []


def test_an_inherited_order_with_no_price_is_adopted_and_reported_unvalued(
    tmp_path, monkeypatch
):
    row = _row("dead-1", DEAD_TAG)
    row["config"].update({"amount": 2.0, "connector_name": "hyperliquid_perpetual"})
    row["config"]["trading_pair"] = "HYPE-USD"
    engine, client = _engine(tmp_path, monkeypatch, [row])

    async def _no_price(client, **_kw):
        return None

    monkeypatch.setattr("condor.fetchers.market_data.fetch_current_price", _no_price)

    asyncio.run(engine._adopt_running_executors(client))

    assert [r["id"] for r in engine._adopted_executors] == ["dead-1"]
    assert engine._adopted_unpriced == ["dead-1"]


def test_a_failed_price_read_still_adopts_and_does_not_raise(tmp_path, monkeypatch):
    """Best-effort, like the rest of the pass — and conservative on the way out.

    Owning the orphan is the point of the pass; pricing it is a refinement.
    A pricing pass that blew up leaves every still-unvalued row named as
    unpriced, which refuses creates rather than sizing them against a zero.
    """
    row = _row("dead-1", DEAD_TAG)
    row["config"].update({"amount": 2.0, "connector_name": "hyperliquid_perpetual"})
    engine, client = _engine(tmp_path, monkeypatch, [row])

    async def _boom(client, rows):
        raise RuntimeError("the price service is down")

    monkeypatch.setattr("condor.fetchers.executors.price_pending_rows", _boom)

    asyncio.run(engine._adopt_running_executors(client))

    assert [r["id"] for r in engine._adopted_executors] == ["dead-1"]
    assert engine._adopted_unpriced == ["dead-1"]


def test_an_inherited_lp_sized_by_nothing_is_adopted_unvalued(tmp_path, monkeypatch):
    """The Agent Builder Cup orphan itself: an lp with no size to read (CORR-740).

    ``_row`` is the shape this file was written for — an ``lp_executor`` whose
    config carries no ``amount`` and which has no ``custom_info`` at all. No
    price lookup can size it (its value is the venue's valuation of two token
    balances, not a pair's price), so it was skipped by the pricing pass and
    adopted at ``amount: 0.0`` *and* reported priced, which left the risk book
    trusting a total that excluded an open position of unknown size.
    """
    engine, client = _engine(tmp_path, monkeypatch, [_row("dead-1", DEAD_TAG)])
    asked: list = []

    async def _price(client, **_kw):
        asked.append(_kw)
        return 150.0

    monkeypatch.setattr("condor.fetchers.market_data.fetch_current_price", _price)

    asyncio.run(engine._adopt_running_executors(client))

    assert [r["amount"] for r in engine._adopted_executors] == [0.0]
    assert engine._adopted_unpriced == ["dead-1"]
    # Reported, not priced: a live feed answering $150 cannot value an lp
    # position, so the row is never even looked up.
    assert asked == []

    # ...and the book says so, rather than counting the row as a valued $0.
    book, captured = _book_engine(
        adopted=engine._adopted_executors,
        own_rows=[],
        own_exposure=0.0,
        adopted_unpriced=engine._adopted_unpriced,
    )
    asyncio.run(book._gather_tick_context(client=None))

    assert captured["state"].executor_count == 1
    assert not captured["state"].book_trusted
    assert "dead-1" in captured["state"].drift_reason


def test_the_pricing_pass_names_the_same_set_whether_it_raised_or_not(
    tmp_path, monkeypatch
):
    """The failure arm and the success arm agreed on nothing (CORR-740).

    Nine lines apart, the ``except`` arm reported *every* unvalued row while the
    pricing call reported only the base-denominated ones. Over three rows — one
    unsized, one base-denominated with no price, one genuinely worth zero — the
    two must name the same two ids.
    """
    unsized = _row("e-unsized", DEAD_TAG)
    pending = _row("e-pending", DEAD_TAG)
    pending["config"].update({"amount": 2.0, "connector_name": "hyperliquid_perpetual"})
    flat = _row("e-flat", DEAD_TAG)
    flat["custom_info"] = {"total_value_quote": 0}
    rows = [unsized, pending, flat]

    async def _no_price(client, **_kw):
        return None

    monkeypatch.setattr("condor.fetchers.market_data.fetch_current_price", _no_price)
    # A fresh home per engine, or the second one mints session 3.
    engine, client = _engine(tmp_path / "ok", monkeypatch, rows)
    asyncio.run(engine._adopt_running_executors(client))
    on_success = engine._adopted_unpriced

    async def _boom(client, rows):
        raise RuntimeError("the price service is down")

    monkeypatch.setattr("condor.fetchers.executors.price_pending_rows", _boom)
    engine, client = _engine(tmp_path / "raised", monkeypatch, rows)
    asyncio.run(engine._adopt_running_executors(client))

    assert on_success == engine._adopted_unpriced == ["e-unsized", "e-pending"]


def test_this_sessions_own_executors_are_never_adopted(tmp_path, monkeypatch):
    """Why the two halves of the sum cannot double-count.

    ``risk_state.total_exposure`` adds the adopted rows to the provider's own
    total without deduping by id, and that is safe only because the two sets
    are disjoint by construction: ``orphaned_running_rows`` keeps a row only
    when its lineage session number is *outside* the live set, and the caller's
    own number is always inside it (``_live_session_nums`` returns ``None``
    otherwise, which adopts nothing at all).
    """
    engine, client = _engine(
        tmp_path, monkeypatch, [_row("live-1", LIVE_TAG), _row("dead-1", DEAD_TAG)]
    )

    asyncio.run(engine._adopt_running_executors(client))

    assert [r["id"] for r in engine._adopted_executors] == ["dead-1"]
    assert engine.session_num == 2
    assert (
        orphaned_running_rows(
            [_row("live-1", LIVE_TAG)], AGENT, STRATEGY, live_sessions={2}
        )
        == []
    )


def test_the_winddown_stops_an_adopted_orphan_too():
    """The guaranteed floor has to reach inherited positions.

    ``_get_running_executors`` is scoped to this session's own tag, so an
    emergency winddown of a restarted session left every inherited position
    open — on the one path whose whole promise is that nothing is stranded.
    """
    from condor.agents.shutdown import _get_running_executors

    class _Providers:
        async def run_core_providers(self, *args, **kwargs):
            return {
                "executors": SimpleNamespace(data={"executors": [{"id": "live-1"}]})
            }

    engine = SimpleNamespace(
        agent_id=LIVE_TAG,
        config={},
        ledger=None,
        provider_registry=_Providers(),
        _last_skill_data={},
        _adopted_executors=[{"id": "dead-1", "controller_id": DEAD_TAG}],
    )

    rows = asyncio.run(_get_running_executors(engine, client=None))

    assert [r["id"] for r in rows] == ["live-1", "dead-1"]


def test_the_orphan_scan_asks_the_server_the_session_was_pinned_to(monkeypatch):
    """The scan has to look where the executors are.

    ``_client_for_status`` resolved the chat's *current* client and ignored the
    session's recorded pin, so a session pinned to server A was scanned on
    whichever server the chat defaults to today — and its executors still
    running on A were reported as nothing at all. The pin only narrows: it is
    still resolved through the owner's accessible servers, the route a restart
    takes.
    """
    import config_manager as cm_mod
    from condor.runtime import loops as loops_mod

    asked: dict = {}

    class _CM:
        @staticmethod
        def get_server(name):
            return {"name": name} if name == "pinned_box" else None

        @staticmethod
        async def get_client_for_chat(chat_id, user_id=None, preferred_server=None):
            asked.update(
                chat_id=chat_id, user_id=user_id, preferred_server=preferred_server
            )
            return "a client for " + str(preferred_server)

    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: _CM())

    client = asyncio.run(
        loops_mod._client_for_status(
            {"chat_id": 5, "user_id": 7, "pins": {"server_name": "pinned_box"}}
        )
    )

    assert client == "a client for pinned_box"
    assert asked == {"chat_id": 5, "user_id": 7, "preferred_server": "pinned_box"}


def test_a_pin_naming_a_deleted_server_falls_back_instead_of_failing(monkeypatch):
    """Best-effort: a stale pin must not cost the boot its orphan report."""
    import config_manager as cm_mod
    from condor.runtime import loops as loops_mod

    asked: dict = {}

    class _CM:
        @staticmethod
        def get_server(name):
            return None  # every pinned name is gone

        @staticmethod
        async def get_client_for_chat(chat_id, user_id=None, preferred_server=None):
            asked["preferred_server"] = preferred_server
            return "the accessible one"

    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: _CM())

    client = asyncio.run(
        loops_mod._client_for_status(
            {"chat_id": 5, "user_id": 7, "pins": {"server_name": "deleted_box"}}
        )
    )

    assert client == "the accessible one"
    assert asked["preferred_server"] is None


# ── One tick asks the venue once (PERF-736) ────────────────────────────


class _IdentifiedClient(_Client):
    """``_Client`` with a ``base_url``, plus the surface the drift provider reads.

    The adoption pass above and ``DriftProvider`` both walk the RUNNING set, and
    adoption runs immediately before ``run_core_providers`` — so a real tick
    asked the same question twice, microseconds apart. A ``base_url`` is what
    tells the fetcher's cache which server it is talking to, so this is the only
    client in this module that goes through it; every fake above is deliberately
    unidentifiable and keeps its original call counts.
    """

    base_url = "http://perf736-host:8000"

    def __init__(self, rows):
        super().__init__(rows)
        self.trading = self

    async def get_positions_summary(self, **kwargs):
        return {"positions": []}

    async def get_positions(self, **kwargs):
        return {"data": [], "pagination": {"has_more": False}}


def test_one_tick_walks_the_running_set_once(tmp_path, monkeypatch):
    """Adoption and the drift provider share one RUNNING walk, not two."""
    from condor.agents.providers.drift import DriftProvider
    from condor.fetchers.executors import clear_executor_cache

    clear_executor_cache()
    engine, _ = _engine(tmp_path, monkeypatch, [])
    client = _IdentifiedClient([_row("dead-1", DEAD_TAG)])

    async def _tick():
        # The tick's own order (``TickEngine._run_tick``): adopt, then providers.
        await engine._adopt_running_executors(client)
        await DriftProvider().execute(client, {}, agent_id=engine.agent_id)

    asyncio.run(_tick())
    clear_executor_cache()

    statuses = [call.get("status") for call in client.searches]
    assert statuses == ["RUNNING", "SHUTTING_DOWN"], (
        "one tick must ask for the running executors once, not twice: " f"{statuses}"
    )
    # And the shared answer is still the right one for the consumer that
    # started the walk.
    assert [r["id"] for r in engine._adopted_executors] == ["dead-1"]
