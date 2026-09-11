"""What a bot-mode session shows about itself in the dashboard.

A session that trades through bots earns nothing under its own ``agent_id``: the
bot's controllers tag their executors with their own config ids, and those rows
live inside the bot instance's own database — they never reach the executor table
Condor searches. The only executor-like rows it can reconstruct are the positions
open *right now*, so a session that opened and closed three positions and is
currently flat produced zero rows and rendered as a session that never traded.

The aggregate always knew better. These tests pin the facts it knows — which
instances ran, which controllers, how each position closed, and whether the fee
figure means anything — and that they survive the trip to the web wire, where
they used to be dropped by a response model that had no fields for them.
"""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from condor.agents.performance import fetch_agent_performance
from condor.fetchers.bot_performance import (
    _aggregate_by_bot,
    clear_archived_cache,
    clear_snapshot_cache,
    resolve_bots,
)
from condor.web.routes import agents as agents_route
from condor.web.routes.agents import AgentPerformanceModel


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    clear_snapshot_cache()
    clear_archived_cache()
    yield
    clear_snapshot_cache()
    clear_archived_cache()


# ── Payload builders ──


def _snap(bot_name, controller_id, *, realized=0.0, volume=0.0, closes=None, ts=""):
    return {
        "bot_name": bot_name,
        "controller_id": controller_id,
        "timestamp": ts,
        "status": "running",
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": 0.0,
            "volume_traded": volume,
            "positions_summary": [],
            "close_type_counts": closes or {},
        },
    }


class _FakeClient:
    """Snapshots, archived-instance listing and cumulative history in one object."""

    def __init__(self, snapshots, history=None, archived=()):
        self._snapshots = snapshots
        self._history = history or {}
        self._archived = list(archived)
        self.bot_orchestration = self
        self.archived_bots = self

    async def get_latest_controller_performance(self, bot_name=None):
        return {"data": self._snapshots}

    async def get_controller_performance_history(self, bot_name, interval, limit):
        return {"data": self._history.get(bot_name, [])}

    async def list_databases(self):
        return [f"/data/archived/{name}.sqlite" for name in self._archived]


# ── The aggregate carries the close-type breakdown ──


def test_close_type_counts_ride_along_per_controller_and_per_bot():
    # trade_count counts only round-trip closes, so a directional controller
    # stopped by its own risk rule reports zero trades on real volume. The raw
    # breakdown is what makes that explicable instead of merely wrong.
    agg = _aggregate_by_bot(
        [
            _snap(
                "b-1",
                "btc",
                realized=-0.3,
                volume=386.0,
                closes={"CloseType.EARLY_STOP": 1},
            ),
            _snap(
                "b-1",
                "eth",
                realized=-0.7,
                volume=399.0,
                closes={"CloseType.EARLY_STOP": 1},
            ),
            _snap("b-1", "sol", realized=0.0, volume=0.0),
        ]
    )
    bot = agg["b-1"]
    assert bot["close_type_counts"] == {"CloseType.EARLY_STOP": 2}
    assert bot["closed_trades"] == 0  # strict round-trip filter, unchanged
    by_id = {c["controller_id"]: c for c in bot["controllers"]}
    assert by_id["btc"]["close_type_counts"] == {"CloseType.EARLY_STOP": 1}
    assert by_id["sol"]["close_type_counts"] == {}


def test_every_controller_names_the_deploy_it_ran_under():
    # Instance aggregates are concatenated when a base is redeployed, so without
    # this the controllers of three deploys read as one undifferentiated set.
    agg = _aggregate_by_bot(
        [
            _snap(
                "b-20260806-213931",
                "btc",
                realized=-0.3,
                ts="2026-08-06T21:47:00+00:00",
            ),
            _snap("b-20260807-045821", "sol", ts="2026-08-07T09:57:00+00:00"),
        ]
    )
    resolved = resolve_bots(agg, ["b"])["b"]
    assert {c["bot_name"] for c in resolved["controllers"]} == {
        "b-20260806-213931",
        "b-20260807-045821",
    }


def test_redeploys_pool_their_close_types_under_the_base():
    agg = _aggregate_by_bot(
        [
            _snap(
                "b-1",
                "btc",
                closes={"CloseType.EARLY_STOP": 1},
                ts="2026-08-06T00:00:00+00:00",
            ),
            _snap(
                "b-2",
                "btc",
                closes={"CloseType.EARLY_STOP": 1, "CloseType.TAKE_PROFIT": 2},
                ts="2026-08-07T00:00:00+00:00",
            ),
        ]
    )
    resolved = resolve_bots(agg, ["b"])["b"]
    assert resolved["close_type_counts"] == {
        "CloseType.EARLY_STOP": 2,
        "CloseType.TAKE_PROFIT": 2,
    }


# ── What the session-level aggregate reports ──


def _perf_for_three_deploys():
    """Two stopped deploys that traded, one live deploy that is flat."""
    live, old1, old2 = "b-20260807-045821", "b-20260806-213931", "b-20260807-022130"
    client = _FakeClient(
        snapshots=[
            _snap(
                old1,
                "btc",
                realized=-0.3,
                volume=386.0,
                closes={"CloseType.EARLY_STOP": 1},
                ts="2026-08-06T21:47:00+00:00",
            ),
            _snap(
                old2,
                "btc",
                realized=-0.46,
                volume=386.0,
                closes={"CloseType.EARLY_STOP": 1},
                ts="2026-08-07T04:00:00+00:00",
            ),
            _snap(live, "sol", ts="2026-08-07T09:57:00+00:00"),
        ],
        archived=[old1, old2],
    )
    return client, asyncio.run(
        fetch_agent_performance(client, "agent_1", bot_names=["b"])
    )


def test_stopped_deploys_are_named_even_though_only_the_live_one_resolves():
    _client, perf = _perf_for_three_deploys()
    # `bot_names` is what is running now — the label the old code showed alone,
    # hiding the two deploys whose losses it was reporting.
    assert perf.bot_names == ["b-20260807-045821"]
    assert perf.bot_instances == [
        "b-20260806-213931",
        "b-20260807-022130",
        "b-20260807-045821",
    ]


def test_a_deploy_known_only_from_the_archive_is_still_listed():
    # Once the snapshot endpoint drops a stopped instance, the archived-database
    # listing is the only evidence it ran. It has no controller rows left, which
    # the session view renders as "no performance snapshot retained" — a deploy
    # with unknown detail, not a deploy that never happened.
    client = _FakeClient(
        snapshots=[_snap("b-20260807-045821", "sol", ts="2026-08-07T09:57:00+00:00")],
        archived=["b-20260101-000000"],
    )
    perf = asyncio.run(fetch_agent_performance(client, "agent_1", bot_names=["b"]))
    assert perf.bot_instances == ["b-20260101-000000", "b-20260807-045821"]
    assert {c["bot_name"] for c in perf.controllers} == {"b-20260807-045821"}


def test_flat_bot_reports_no_executor_rows_but_a_full_controller_breakdown():
    _client, perf = _perf_for_three_deploys()
    # This is the exact shape that used to render as "No executors for this
    # session": nothing open, everything closed, and real money moved.
    assert perf.executors == []
    assert perf.volume == 772.0
    assert round(perf.realized_pnl, 2) == -0.76
    assert len(perf.controllers) == 3
    assert perf.close_type_counts == {"CloseType.EARLY_STOP": 2}


def test_volume_without_a_fee_column_reads_as_unknown_not_free():
    _client, perf = _perf_for_three_deploys()
    assert perf.fees == 0.0
    assert perf.fees_known is False


def test_a_lifetime_fee_column_keeps_its_figure_but_not_its_certainty():
    # An un-sliced bot-mode agent has no fee *column*: ``cum_fees_quote`` is summed
    # off ``positions_summary``, so it covers the positions open at this instant and
    # omits every fee already paid on every closed position. That is a floor, which
    # is exactly what ``fees_known=False`` means — the figure is kept unchanged, the
    # certainty is not ([[CORR-219]]). This used to assert ``True`` over the same
    # 0.42 and overstated net PnL by the closed-position fees.
    client = _FakeClient(
        snapshots=[
            {
                "bot_name": "b-1",
                "controller_id": "btc",
                "timestamp": "2026-08-07T00:00:00+00:00",
                "status": "running",
                "performance": {
                    "realized_pnl_quote": 5.0,
                    "unrealized_pnl_quote": 0.0,
                    "volume_traded": 100.0,
                    "positions_summary": [{"cum_fees_quote": 0.42}],
                },
            }
        ],
    )
    perf = asyncio.run(fetch_agent_performance(client, "agent_1", bot_names=["b"]))
    # Only the flag moves: the number, and everything folded beside it, is identical.
    assert perf.fees == 0.42
    assert perf.fees_known is False
    assert perf.realized_pnl == 5.0
    assert perf.volume == 100.0


def test_executor_mode_sessions_are_untouched():
    # A session with its own agent_id-tagged executors owns no bot: none of the
    # bot-mode fields should acquire content, and fees stay known.
    class _NoBots:
        class executors:
            @staticmethod
            async def search_executors(controller_ids, limit, cursor=None):
                return {"executors": []}

    perf = asyncio.run(fetch_agent_performance(_NoBots(), "agent_1"))
    assert perf.bot_instances == []
    assert perf.controllers == []
    assert perf.close_type_counts == {}
    assert perf.fees_known is True


# ── The web wire ──


def test_response_model_carries_the_bot_mode_fields():
    # The regression this whole file exists for: the aggregate knew all of this
    # and the response model had nowhere to put it, so the UI could not have
    # rendered it however it was written.
    _client, perf = _perf_for_three_deploys()
    payload = AgentPerformanceModel(
        agent_id="agent_1",
        session_num=1,
        bot_names=perf.bot_names,
        bot_instances=perf.bot_instances,
        unresolved_bases=perf.unresolved_bases,
        controllers=perf.controllers,
        close_type_counts=perf.close_type_counts,
        fees_known=perf.fees_known,
    ).model_dump()
    assert payload["bot_instances"] == perf.bot_instances
    assert payload["close_type_counts"] == {"CloseType.EARLY_STOP": 2}
    assert payload["fees_known"] is False
    assert {c["bot_name"] for c in payload["controllers"]} == {
        "b-20260806-213931",
        "b-20260807-022130",
        "b-20260807-045821",
    }


def test_rollup_leaves_the_per_session_fields_empty_rather_than_ambiguous():
    # The strategy rollup distributes a bot's history across sessions instead of
    # resolving instances per session, so it cannot fill these honestly. Empty is
    # the contract; filling `bot_names` with base names there would have made one
    # field mean two different things on two routes.
    assert AgentPerformanceModel(agent_id="a", session_num=1).bot_names == []
    assert AgentPerformanceModel(agent_id="a", session_num=1).fees_known is True


# ── Session artifacts the reviewer can now reach ──


class _User:
    id = 1
    role = "admin"


def _strategy_on_disk(tmp_path: Path):
    """Minimal agent/strategy tree with one session, wired into the stores."""
    sdir = tmp_path / "ag" / "strategies" / "st"
    (sdir / "sessions" / "session_1").mkdir(parents=True)
    (sdir / "strategy.md").write_text("# playbook\n")
    (sdir / "sessions" / "session_1" / "config.yml").write_text(yaml.safe_dump({}))
    return sdir


def test_canvas_endpoint_serves_sections_and_revisions(tmp_path, monkeypatch):
    from condor.agents import canvas as canvas_mod

    sdir = _strategy_on_disk(tmp_path)
    session_dir = sdir / "sessions" / "session_1"
    canvas_mod.write_section(
        session_dir, "thesis", "Trend following on 1h EMAs.", tick=3
    )

    monkeypatch.setattr(
        agents_route,
        "_get_strategy",
        lambda slug, sslug: type("S", (), {"home": sdir})(),
    )
    out = asyncio.run(agents_route.get_session_canvas("ag", "st", 1, user=_User()))
    assert out["sections"]["thesis"].startswith("Trend following")
    assert out["last_revised_tick"] == 3
    assert out["revisions"][0]["tick"] == 3
    assert out["section_order"][0] == "thesis"


def test_report_endpoint_returns_null_for_a_session_that_kept_none(
    tmp_path, monkeypatch
):
    # A missing report is a normal state — a loop that predates the live report,
    # or one that never ticked — so callers must not have to tell it from a 404.
    sdir = _strategy_on_disk(tmp_path)
    monkeypatch.setattr(
        agents_route,
        "_get_strategy",
        lambda slug, sslug: type("S", (), {"home": sdir})(),
    )
    monkeypatch.setattr(
        "condor.reports.list_reports", lambda **kw: ([], 0), raising=False
    )
    out = asyncio.run(agents_route.get_session_report("ag", "st", 1, user=_User()))
    assert out == {"report": None}


def test_report_endpoint_matches_the_session_it_was_written_for(tmp_path, monkeypatch):
    sdir = _strategy_on_disk(tmp_path)
    monkeypatch.setattr(
        agents_route,
        "_get_strategy",
        lambda slug, sslug: type("S", (), {"home": sdir})(),
    )
    rows = [
        {
            "id": "aaa",
            "title": "ag · st · session 1",
            "filename": "f1.html",
            "created_at": "2026-08-06T21:39:57+00:00",
            "source_type": "routine",
            "source_name": "ag.st/session_1",
            "tags": [],
        },
        {
            "id": "bbb",
            "title": "ag · st · session 2",
            "filename": "f2.html",
            "created_at": "2026-08-07T21:39:57+00:00",
            "source_type": "routine",
            "source_name": "ag.st/session_2",
            "tags": [],
        },
    ]
    import condor.reports as reports_mod

    monkeypatch.setattr(reports_mod, "list_reports", lambda **kw: (rows, len(rows)))
    out = asyncio.run(agents_route.get_session_report("ag", "st", 1, user=_User()))
    assert out["report"]["id"] == "aaa"


def test_the_live_report_breaks_the_kpis_down_by_deploy_and_controller():
    # "Total PnL -$1.46" over three redeploys says nothing about which leg lost
    # it, and the report showed exactly that and nothing more.
    from condor.agents.session_report import SessionReport

    class _B:
        def __init__(self):
            self.rows = None

        def section(self, title, body=""):
            self.title = title

        def table(self, rows):
            self.rows = rows

    b = _B()
    SessionReport._controllers(
        b,
        {
            "bot_names": ["b-2"],
            "controllers": [
                {
                    "bot_name": "b-1",
                    "controller_id": "btc",
                    "realized_pnl_quote": -0.31,
                    "volume_traded": 386.0,
                    "close_type_counts": {"CloseType.EARLY_STOP": 1},
                    # The backend reports this for stopped instances too, which is
                    # why the row's State must not come from it.
                    "status": "running",
                },
                {"bot_name": "b-2", "controller_id": "sol"},
            ],
        },
    )
    assert [r["State"] for r in b.rows] == ["stopped", "running"]
    assert b.rows[0]["Closes"] == "early stop ×1"
    assert b.rows[1]["Closes"] == "—"


def test_the_report_skips_the_block_for_an_executor_only_session():
    from condor.agents.session_report import SessionReport

    class _B:
        called = False

        def section(self, *a, **k):
            self.called = True

        def table(self, *a, **k):
            self.called = True

    b = _B()
    SessionReport._controllers(b, {"bot_names": [], "controllers": []})
    assert b.called is False


def test_session_ledger_survives_the_json_round_trip(tmp_path):
    # The ledger is what tells the detail route which bases a session owned; a
    # session whose ledger cannot be read falls back to reporting nothing.
    from condor.agents.ownership import read_owned

    sd = tmp_path / "session_1"
    sd.mkdir()
    (sd / "owned_bots.json").write_text(
        json.dumps(
            {
                "namespace": "ag-st",
                "bots": {
                    "b": {
                        "base": "b",
                        "origin": "deployed",
                        "since": 1.0,
                        "last_seen": 2.0,
                    }
                },
            }
        )
    )
    owned = read_owned(sd)
    assert [o.base for o in owned] == ["b"]
    assert owned[0].until == 0.0
