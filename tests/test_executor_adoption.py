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
