"""The per-tick action log (FEAT-097).

What the agent *did*, as opposed to what it said. These pin the extraction from
the folded tool-call shape the tick already holds, the append/read round trip,
and the two properties the record is worth nothing without: it never costs a
tick, and it never deletes a row.
"""

import json

import pytest

from condor.agents.actions import (
    ACTIONS_ARCHIVE_FILENAME,
    ACTIONS_FILENAME,
    MAX_ACTION_LINES,
    TICK_JOIN_WINDOW,
    AgentAction,
    actions_from_tool_calls,
    append_actions,
    latest_action,
    read_actions,
    tick_for,
)


def folded(name, status="completed", **args):
    """A tool call in the shape ``fold_tool_call_event`` produces.

    Deliberately the *folded* shape and not the gate's: the two disagree about
    which key holds the tool name, and a test written in the gate's spelling
    would pass while the engine wrote an empty log.
    """
    call = {"id": f"tc_{name}", "name": name, "status": status, "kind": "mcp"}
    if args:
        call["input"] = args
    return call


# ── Extraction ──


def test_a_tick_records_its_writes_and_not_its_reads():
    calls = [
        folded("get_prices", trading_pair="SOL-USDC"),
        folded("create_grid_executor", trading_pair="SOL-USDC", total_amount_quote=100),
        folded("list_executors"),
        folded("stop_executor", executor_id="a1b2c3d4e5f6a7b8"),
    ]
    actions = actions_from_tool_calls(calls, tick=12, at=1_700_000_000.0)

    assert [a.tool for a in actions] == ["create_grid_executor", "stop_executor"]
    assert all(a.tick == 12 and a.at == 1_700_000_000.0 for a in actions)
    assert all(a.ok for a in actions)
    assert actions[0].summary == "Create grid executor on SOL-USDC for 100 quote"
    assert actions[1].summary.startswith("Stop executor a1b2c3d4e5f6")


def test_the_acp_wire_name_still_resolves():
    """ACP names a tool ``mcp__mcp-hummingbot__x``; the log stores the bare one."""
    calls = [folded("mcp__mcp-hummingbot__manage_bots", action="deploy", bot_name="b")]
    (action,) = actions_from_tool_calls(calls, tick=1, at=0.0)

    assert action.tool == "manage_bots"
    assert action.verb == "manage_bots:deploy"
    assert action.summary.startswith("Deploy bot 'b'")


def test_the_verb_carries_a_dispatch_tools_action():
    calls = [
        folded("manage_bots", action="stop_bot", bot_name="b"),
        folded("create_lp_executor", trading_pair="SOL-USDC"),
    ]
    actions = actions_from_tool_calls(calls, tick=1, at=0.0)
    assert [a.verb for a in actions] == ["manage_bots:stop_bot", "create_lp_executor"]


def test_a_failed_call_is_recorded_as_a_failure():
    call = folded("stop_executor", status="failed", executor_id="abc")
    call["output"] = "ToolError: executor abc is not running"
    (action,) = actions_from_tool_calls([call], tick=3, at=0.0)

    assert action.ok is False
    assert "not running" in action.error


def test_a_refused_call_says_so():
    """The pydantic-ai path marks a denied call ``blocked`` and gives no output."""
    call = folded("place_order", status="blocked", trading_pair="SOL-USDC", amount=1)
    (action,) = actions_from_tool_calls([call], tick=4, at=0.0)

    assert action.ok is False
    assert action.error == "refused"


def test_a_call_with_no_terminal_update_is_not_a_success():
    """An ACP call left ``in_progress`` is honest about not having completed."""
    (action,) = actions_from_tool_calls(
        [folded("execute_swap", status="in_progress", trading_pair="SOL-USDC")],
        tick=5,
        at=0.0,
    )
    assert action.ok is False
    assert action.error == "in_progress"


def test_unreadable_arguments_are_recorded_rather_than_dropped():
    call = {"id": "x", "name": "manage_bots", "status": "completed", "input": "{bad"}
    (action,) = actions_from_tool_calls([call], tick=6, at=0.0)

    assert action.verb == "manage_bots"
    assert action.summary == "manage_bots (arguments could not be read)"


def test_an_ungated_edit_and_an_ungated_brake_are_both_recorded():
    calls = [
        folded("manage_gateway_config", action="delete", resource_type="tokens"),
        folded("control_agent", action="stop", agent_id="a.b_1"),
        folded("manage_bots", action="status", bot_name="b"),
    ]
    actions = actions_from_tool_calls(calls, tick=7, at=0.0)
    assert [a.verb for a in actions] == [
        "manage_gateway_config:delete",
        "control_agent:stop",
    ]


def test_a_tick_that_only_read_records_nothing():
    assert actions_from_tool_calls([folded("get_prices")], tick=8, at=0.0) == []


# ── The file ──


def test_append_and_read_round_trip(tmp_path):
    actions = actions_from_tool_calls(
        [
            folded(
                "create_dca_executor", trading_pair="SOL-USDC", amounts_quote=[50, 50]
            )
        ],
        tick=9,
        at=1_700_000_001.5,
    )
    append_actions(tmp_path, actions)

    (row,) = read_actions(tmp_path)
    assert row == actions[0]
    assert latest_action(tmp_path) == actions[0]


def test_read_is_oldest_first_and_latest_is_the_last_one(tmp_path):
    for tick in (1, 2, 3):
        append_actions(
            tmp_path,
            actions_from_tool_calls(
                [folded("stop_executor", executor_id=f"e{tick}")], tick=tick, at=0.0
            ),
        )

    assert [a.tick for a in read_actions(tmp_path)] == [1, 2, 3]
    assert latest_action(tmp_path).tick == 3
    assert [a.tick for a in read_actions(tmp_path, limit=2)] == [2, 3]


def test_a_session_that_never_acted_reads_empty(tmp_path):
    assert read_actions(tmp_path) == []
    assert latest_action(tmp_path) is None


def test_an_experiment_writes_nothing(tmp_path):
    """``session_dir is None`` is how an experiment reaches here."""
    append_actions(
        None, actions_from_tool_calls([folded("place_order")], tick=1, at=0.0)
    )
    assert list(tmp_path.iterdir()) == []


def test_a_half_written_line_is_skipped_not_raised(tmp_path):
    path = tmp_path / ACTIONS_FILENAME
    good = AgentAction(tick=1, at=0.0, tool="t", verb="t", summary="s", ok=True)
    path.write_text(
        json.dumps(good.__dict__) + "\n" + '{"tick": 2, "tool": "cut off\n',
        encoding="utf-8",
    )

    assert read_actions(tmp_path) == [good]
    assert latest_action(tmp_path) == good


def test_the_trim_moves_the_head_to_the_archive_and_deletes_nothing(tmp_path):
    path = tmp_path / ACTIONS_FILENAME
    overflow = 5
    rows = [
        AgentAction(tick=i, at=0.0, tool="t", verb="t", summary=f"#{i}", ok=True)
        for i in range(MAX_ACTION_LINES + overflow)
    ]
    path.write_text(
        "".join(json.dumps(r.__dict__) + "\n" for r in rows[:-1]), encoding="utf-8"
    )
    append_actions(tmp_path, rows[-1:])

    live = path.read_text().splitlines()
    archived = (tmp_path / ACTIONS_ARCHIVE_FILENAME).read_text().splitlines()
    assert len(live) == MAX_ACTION_LINES
    assert len(archived) == overflow
    # Nothing lost: the archive's head plus the live file is the whole history.
    assert [json.loads(line)["tick"] for line in archived + live] == [
        r.tick for r in rows
    ]


def test_a_write_that_raises_does_not_reach_the_caller(tmp_path, monkeypatch):
    """A tick must survive a full disk, a read-only mount and a vanished dir."""
    missing = tmp_path / "gone"
    append_actions(
        missing,
        actions_from_tool_calls([folded("place_order")], tick=1, at=0.0),
    )
    assert not missing.exists()

    real_open = type(tmp_path).open

    def boom(self, *args, **kwargs):
        if self.name == ACTIONS_FILENAME:
            raise OSError("no space left on device")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "open", boom)
    append_actions(
        tmp_path, actions_from_tool_calls([folded("place_order")], tick=1, at=0.0)
    )


def test_an_unreadable_file_reads_empty_rather_than_raising(tmp_path):
    (tmp_path / ACTIONS_FILENAME).mkdir()
    assert read_actions(tmp_path) == []
    assert latest_action(tmp_path) is None


@pytest.mark.parametrize("bad", [None, [], [{}], [{"name": ""}]])
def test_junk_in_is_no_rows_out(bad):
    assert actions_from_tool_calls(bad or [], tick=1, at=0.0) == []


# ── The engine's seam ──
#
# No test drives ``TickEngine._tick`` end to end (it needs a model, a provider
# fan-out and a live API), so what is pinned here is the sequence the engine
# performs, in engine.py's order — the same idiom ``test_journal_retention``
# uses for the journal's three writes.


def _engine_tick(journal, session_dir, tool_calls):
    """The action-log calls of one tick, in engine.py's order."""
    from condor.agents.actions import actions_from_tool_calls as extract

    tick_actions = extract(tool_calls, tick=journal.tick_count + 1, at=1.0)
    with journal.batch():
        tick_num = journal.record_tick(
            response_summary="held the spread", actions=len(tick_actions)
        )
    # After ``save_full_snapshot`` in the engine: the snapshot is the record of
    # record and must land whatever this does.
    append_actions(session_dir, tick_actions)
    return tick_num, tick_actions


def test_a_tick_lands_its_actions_under_the_number_the_journal_assigned(tmp_path):
    from condor.agents.journal import JournalManager

    journal = JournalManager("test-agent", session_dir=tmp_path)
    calls = [
        folded("get_prices", trading_pair="SOL-USDC"),
        folded("create_grid_executor", trading_pair="SOL-USDC", total_amount_quote=100),
        folded("stop_executor", executor_id="a1b2c3d4e5f6"),
    ]

    tick_num, _ = _engine_tick(journal, tmp_path, calls)
    rows = read_actions(tmp_path)

    assert [r.tick for r in rows] == [tick_num, tick_num]
    assert [r.verb for r in rows] == ["create_grid_executor", "stop_executor"]


def test_the_tick_line_stops_saying_actions_zero(tmp_path):
    """``actions=N`` has read 0 on every tick ever written: the engine never
    passed the argument the journal has always accepted."""
    from condor.agents.journal import JournalManager

    journal = JournalManager("test-agent", session_dir=tmp_path)
    _engine_tick(journal, tmp_path, [folded("place_order", trading_pair="SOL-USDC")])
    _engine_tick(journal, tmp_path, [folded("get_prices")])

    ticks = [ln for ln in journal.read_full().splitlines() if "| actions=" in ln]
    assert "actions=1" in ticks[0]
    assert "actions=0" in ticks[1]


def test_the_predicted_tick_number_is_the_one_record_tick_assigns(tmp_path):
    """The engine stamps its rows with ``tick_count + 1`` because the same call
    that assigns the number also wants the count. If those ever diverge, every
    row points at the wrong snapshot."""
    from condor.agents.journal import JournalManager

    journal = JournalManager("test-agent", session_dir=tmp_path)
    for _ in range(3):
        predicted = journal.tick_count + 1
        assert journal.record_tick(response_summary="x") == predicted


def test_an_experiment_tick_writes_no_log(tmp_path):
    """Experiments keep no journal and have ``session_dir is None``; their tool
    calls live in their own snapshot and nowhere else."""
    from condor.agents.journal import JournalManager

    journal = JournalManager("test-agent", session_dir=tmp_path)
    _engine_tick(journal, None, [folded("place_order", trading_pair="SOL-USDC")])

    assert not (tmp_path / ACTIONS_FILENAME).exists()


# ── The route ──
#
# ``condor/web/routes/*.py`` is not in main.py's reload list, so a route change
# needs a full bot restart. It is verified offline here instead, by calling the
# endpoint function directly the way ``test_fleet_map`` does.


def _strategy_at(monkeypatch, tmp_path):
    """A strategy the route can resolve, with an empty session 7 on disk."""
    from types import SimpleNamespace

    import condor.web.routes.agents as routes

    session_dir = tmp_path / "sessions" / "session_7"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(
        routes, "_get_strategy", lambda slug, sslug: SimpleNamespace(home=tmp_path)
    )
    return session_dir


def _call_route(session_num=7, limit=100):
    import asyncio
    from types import SimpleNamespace

    from condor.web.routes.agents import list_session_actions

    return asyncio.run(
        list_session_actions(
            "brigado",
            "brl_mm",
            session_num,
            limit=limit,
            user=SimpleNamespace(id=1, is_admin=True),
        )
    )


def test_the_route_answers_empty_for_a_session_that_never_acted(monkeypatch, tmp_path):
    _strategy_at(monkeypatch, tmp_path)
    assert _call_route() == {"actions": []}


def test_the_route_returns_the_rows_oldest_last(monkeypatch, tmp_path):
    session_dir = _strategy_at(monkeypatch, tmp_path)
    for tick in (1, 2):
        append_actions(
            session_dir,
            actions_from_tool_calls(
                [folded("stop_executor", executor_id=f"e{tick}")], tick=tick, at=0.0
            ),
        )

    rows = _call_route()["actions"]
    assert [r["tick"] for r in rows] == [1, 2]
    assert rows[0]["verb"] == "stop_executor"
    assert rows[0]["ok"] is True


def test_the_route_makes_no_hummingbot_call(monkeypatch, tmp_path):
    session_dir = _strategy_at(monkeypatch, tmp_path)
    append_actions(
        session_dir,
        actions_from_tool_calls([folded("place_order")], tick=1, at=0.0),
    )

    import config_manager

    async def _boom(*args, **kwargs):  # pragma: no cover - it must never run
        raise AssertionError("the actions route reached for a Hummingbot client")

    monkeypatch.setattr(config_manager.ConfigManager, "get_client", _boom)
    assert len(_call_route()["actions"]) == 1


def test_a_missing_session_is_a_404(monkeypatch, tmp_path):
    from fastapi import HTTPException

    _strategy_at(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        _call_route(session_num=99)
    assert excinfo.value.status_code == 404


def test_the_actions_route_is_not_shadowed_by_the_slug_catch_all():
    """Only *first* segments are at risk, but the route order is worth pinning."""
    from starlette.routing import Match

    from condor.web.routes.agents import router

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/agents/brigado/strategies/brl_mm/sessions/7/actions",
        "path_params": {},
    }
    for route in router.routes:
        if route.matches(scope)[0] == Match.FULL:
            assert route.endpoint.__name__ == "list_session_actions"
            return
    raise AssertionError("no route matched the actions path")


# ── The tick a record came from (FEAT-100) ──


def _did(verb, tick, at, ok=True):
    return AgentAction(
        tick=tick, at=at, tool=verb.split(":")[0], verb=verb, summary="", ok=ok
    )


def test_a_create_explains_the_record_that_followed_it():
    rows = [
        _did("manage_bots:deploy", 3, 1000.0),
        _did("create_grid_executor", 5, 2000.0),
    ]
    assert tick_for(rows, "bot", 1060.0) == 3
    assert tick_for(rows, "executor", 2060.0) == 5


def test_the_nearest_preceding_create_wins():
    rows = [
        _did("manage_bots:deploy", 3, 1000.0),
        _did("manage_bots:deploy", 9, 1500.0),
    ]
    assert tick_for(rows, "bot", 1600.0) == 9


def test_a_create_after_the_record_never_explains_it():
    """A deploy on tick 9 cannot be why a bot started an hour earlier."""
    rows = [_did("manage_bots:deploy", 9, 5000.0)]
    assert tick_for(rows, "bot", 1000.0) is None


def test_outside_the_window_is_blank_rather_than_a_nearest_match():
    rows = [_did("manage_bots:deploy", 3, 1000.0)]
    assert tick_for(rows, "bot", 1000.0 + TICK_JOIN_WINDOW + 1) is None


def test_a_failed_call_created_nothing():
    rows = [_did("manage_bots:deploy", 3, 1000.0, ok=False)]
    assert tick_for(rows, "bot", 1060.0) is None


def test_a_run_with_no_log_at_all_has_no_ticks_to_credit():
    """Every session on disk when this shipped is this case."""
    assert tick_for([], "bot", 1060.0) is None


def test_a_create_of_another_kind_is_not_the_explanation():
    rows = [_did("create_grid_executor", 5, 1000.0)]
    assert tick_for(rows, "bot", 1060.0) is None


# ── The controller writes a fleet is built from (FEAT-102) ──


def test_a_failed_controller_write_is_recorded_with_its_error():
    """The twelve calls that build a fleet were invisible; six of them failed.

    ``manage_controllers`` is outside the confirmation gate on purpose, so the
    log's own predicate is what has to pick it up.
    """
    calls = [
        folded(
            "mcp__mcp-hummingbot__manage_controllers",
            status="failed",
            action="upsert",
            target="config",
            config_name="king-btcbrl-1",
        )
    ]
    calls[0]["output"] = "400: field required: total_amount_quote"

    (action,) = actions_from_tool_calls(calls, tick=1, at=1.0)

    assert action.verb == "manage_controllers:upsert"
    assert action.ok is False
    assert action.error == "400: field required: total_amount_quote"
    assert action.summary == "Controller config: upsert 'king-btcbrl-1'"


def test_a_controller_read_is_still_not_recorded():
    calls = [
        folded("manage_controllers", action="list"),
        folded("manage_controllers", action="describe", controller_name="pmm_simple"),
    ]
    assert actions_from_tool_calls(calls, tick=1, at=1.0) == []


def test_the_controller_predicate_fails_open_on_an_unreadable_action():
    """The log's rule everywhere: an action it cannot read is recorded."""
    from condor.runtime.danger import is_recordable_tool_call

    assert is_recordable_tool_call({"tool": "manage_controllers", "input": None})
    assert is_recordable_tool_call({"tool": "manage_controllers", "input": {}})
    assert is_recordable_tool_call(
        {"tool": "manage_controllers", "input": {"action": "something_new"}}
    )


def test_the_log_records_everything_the_gate_does_and_more():
    """The two predicates cannot drift: the gate's set is a strict subset."""
    from condor.runtime.danger import is_mutating_tool_call, is_recordable_tool_call

    for call in (
        {"tool": "place_order", "input": {"trading_pair": "SOL-USDC"}},
        {"tool": "manage_bots", "input": {"action": "deploy", "bot_name": "b"}},
        {"tool": "manage_bots", "input": {"action": "status"}},
        {"tool": "manage_controllers", "input": {"action": "upsert"}},
        {"tool": "manage_controllers", "input": {"action": "list"}},
        {"tool": "get_prices", "input": {"trading_pair": "SOL-USDC"}},
    ):
        if is_mutating_tool_call(call):
            assert is_recordable_tool_call(call), call


def test_the_confirmation_gate_is_untouched_by_the_log_growing():
    """Criterion: no new confirmation prompt reaches a running agent.

    ``manage_controllers`` becoming recordable must not make it gated — a
    widened gate would stop a live fleet on every config write.
    """
    from condor.runtime.danger import DANGEROUS_TOOLS, is_dangerous_tool_call

    assert "manage_controllers" not in DANGEROUS_TOOLS
    for action in ("list", "describe", "upsert", "delete", "something_new"):
        call = {"tool": "manage_controllers", "input": {"action": action}}
        assert is_dangerous_tool_call(call) is False, action
    assert (
        is_dangerous_tool_call({"tool": "manage_controllers", "input": None}) is False
    )


def test_the_controller_action_sets_match_the_registered_tool():
    """Every action literal the tool accepts is classified, or the fail-open
    rule would silently record a read."""
    from condor.runtime.danger import (
        MUTATING_CONTROLLER_ACTIONS,
        READ_ONLY_CONTROLLER_ACTIONS,
    )
    from mcp_servers.hummingbot_api.server import manage_controllers

    literals = set(manage_controllers.__annotations__["action"].__args__)
    assert MUTATING_CONTROLLER_ACTIONS | READ_ONLY_CONTROLLER_ACTIONS == literals
    assert not (MUTATING_CONTROLLER_ACTIONS & READ_ONLY_CONTROLLER_ACTIONS)


# ── The deploy a tick is owed (FEAT-102) ──


def test_a_completed_deploy_names_the_bot_it_deployed():
    from condor.agents.actions import deployed_bot_names

    calls = [
        folded("manage_bots", action="status"),
        folded(
            "mcp__mcp-hummingbot__manage_bots",
            action="deploy",
            bot_name="pmm-king-btcbrl-20260903-181000",
        ),
    ]
    assert deployed_bot_names(calls) == ["pmm-king-btcbrl-20260903-181000"]


def test_a_deploy_that_did_not_complete_is_not_owned():
    from condor.agents.actions import deployed_bot_names

    for status in ("failed", "pending", "cancelled"):
        calls = [folded("manage_bots", status=status, action="deploy", bot_name="b1")]
        assert deployed_bot_names(calls) == [], status


def test_nothing_but_a_deploy_claims_a_bot():
    from condor.agents.actions import deployed_bot_names

    calls = [
        folded("manage_bots", action="stop_bot", bot_name="b1"),
        folded("manage_controllers", action="upsert", config_name="c1"),
        folded("manage_bots", action="deploy"),  # no name to claim
    ]
    assert deployed_bot_names(calls) == []


def test_the_deploy_row_carries_the_bot_name_as_its_subject():
    calls = [
        folded("manage_bots", action="deploy", bot_name="pmm-king-btcbrl-1"),
        folded("stop_executor", executor_id="a1b2c3d4e5f6"),
    ]
    deploy, stop = actions_from_tool_calls(calls, tick=1, at=1.0)

    assert deploy.subject == "pmm-king-btcbrl-1"
    assert stop.subject == "", "only a deploy has a subject worth joining on"


def test_a_row_written_before_this_feature_still_parses(tmp_path):
    """Wire compatibility, both directions."""
    old_row = {
        "tick": 3,
        "at": 1.0,
        "tool": "manage_bots",
        "verb": "manage_bots:deploy",
        "summary": "Deploy bot 'b1'",
        "ok": True,
        "error": "",
    }
    (tmp_path / ACTIONS_FILENAME).write_text(json.dumps(old_row) + "\n")

    (row,) = read_actions(tmp_path)
    assert row.verb == "manage_bots:deploy"
    assert row.subject == "", "a missing subject defaults, it does not raise"


def test_a_tick_that_deploys_outside_its_namespace_still_records_an_owner(tmp_path):
    """The whole point: ``bot_name: ''`` means ``enforced=False``, and on a
    first session there is no lineage to inherit — so before this the ledger
    stayed empty and every money surface below it read $0.00."""
    from condor.agents.actions import deployed_bot_names
    from condor.agents.ownership import BotLedger

    ledger = BotLedger(
        "brigado-pmm_king_btc_brl_fleet_operator", tmp_path, enforced=False
    )
    calls = [
        folded(
            "mcp__mcp-hummingbot__manage_bots",
            action="deploy",
            bot_name="pmm-king-btcbrl-20260903-181000",
        )
    ]

    for name in deployed_bot_names(calls):
        ledger.note_deploy(name)

    # The ledger keys on the *base*: the deploy suffix is what distinguishes
    # one deployment of a fleet from the next, and attribution follows the name.
    assert ledger.bases() == ["pmm-king-btcbrl"]
    assert (tmp_path / "owned_bots.json").exists()
    assert ledger.owned()[0].origin == "deployed"


def test_a_second_claim_of_the_same_deploy_costs_nothing(tmp_path):
    """The risk gate claims on the way in, the stream on the way out."""
    from condor.agents.ownership import BotLedger

    ledger = BotLedger("ns", tmp_path, enforced=False)
    ledger.note_deploy("bot-1", now=100.0)
    ledger.note_deploy("bot-1", now=200.0)

    assert ledger.bases() == ["bot-1"]
    assert ledger.owned()[0].since == 100.0, "the earlier window is the right one"


# ── Recovering a session that already deployed (FEAT-102) ──


def test_a_session_recovers_the_deploys_its_own_log_records(tmp_path):
    from condor.agents.actions import recorded_deploy_names

    append_actions(
        tmp_path,
        [
            AgentAction(
                tick=1,
                at=1.0,
                tool="manage_bots",
                verb="manage_bots:deploy",
                summary="Deploy bot 'pmm-king-btcbrl-20260903-181000'",
                ok=True,
                subject="pmm-king-btcbrl-20260903-181000",
            ),
            AgentAction(
                tick=1,
                at=1.0,
                tool="manage_bots",
                verb="manage_bots:deploy",
                summary="Deploy bot 'failed-one'",
                ok=False,
                error="400",
                subject="failed-one",
            ),
            AgentAction(
                tick=1,
                at=1.0,
                tool="stop_executor",
                verb="stop_executor",
                summary="Stop executor a1",
                ok=True,
            ),
        ],
    )

    assert recorded_deploy_names(tmp_path) == ["pmm-king-btcbrl-20260903-181000"]


def test_a_session_with_no_log_recovers_nothing(tmp_path):
    from condor.agents.actions import recorded_deploy_names

    assert recorded_deploy_names(tmp_path) == []
    assert recorded_deploy_names(None) == []
