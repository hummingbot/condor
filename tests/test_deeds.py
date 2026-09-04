"""Every deed leaves a mark, whoever did it (FEAT-105).

Three claims, and they are the whole feature:

* a mutation recorded outside a loop lands in the **same two files** a tick's
  does, in the run's own directory — so ``read_actions``, ``read_owned`` and
  ``build_deployments`` work on a conversation with no changes;
* a run that mutated nothing writes nothing and creates no directory;
* a mutating route that records no deed **fails this module**, which is the
  answer to "somebody adds a sixth door and nobody wires it".

The first test is the feature's own precondition, pinned: FEAT-102's fix is what
makes a deed name a bot at all, and an opaque summary would make every row here
worthless without failing anything.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from condor import paths
from condor.acp.client import (
    ToolCallEvent,
    ToolCallUpdate,
    fold_tool_call_event,
)
from condor.agents import deeds
from condor.agents.actions import read_actions
from condor.agents.ownership import read_owned
from condor.agents.strategy import StrategyStore

USER = 4242


def _deploy_calls(bot_name: str = "pmm-king-btcbrl") -> list[dict]:
    """The adapter's real two-encounter sequence for one deploy, folded.

    Announced while the input JSON is still streaming (``rawInput: {}``), then
    completed with the full arguments on a ``tool_call_update``.
    """
    tc_map: dict[str, dict] = {}
    fold_tool_call_event(
        tc_map,
        ToolCallEvent(
            tool_call_id="1",
            title="mcp__mcp-hummingbot__manage_bots",
            status="pending",
            input={},
        ),
    )
    fold_tool_call_event(
        tc_map,
        ToolCallUpdate(
            tool_call_id="1",
            status="completed",
            input={
                "action": "deploy",
                "bot_name": bot_name,
                "controllers_config": ["king-btcbrl-1"],
            },
        ),
    )
    return list(tc_map.values())


# ── The precondition (Step 0): a deed can name what it did ──


def test_a_deploy_folded_off_the_wire_names_the_bot_in_its_row():
    """The gate FEAT-105 refused to be built over.

    Every row of the one ``actions.jsonl`` on the install that motivated this
    feature read "manage_bots (arguments could not be read)" — a deed that names
    no bot owns nothing, and every feature downstream of this one is worthless.
    This drives the fix's own wire sequence all the way to a written row.
    """
    owner = deeds.for_conversation(USER, "conv1")

    deeds.record_deeds(owner, _deploy_calls())

    (row,) = read_actions(deeds.deed_dir(owner))
    assert row.verb == "manage_bots:deploy"
    assert "pmm-king-btcbrl" in row.summary
    assert "could not be read" not in row.summary
    assert row.subject == "pmm-king-btcbrl"
    assert row.ok is True


# ── The run key and its pseudo-strategies ──


def test_an_unbound_chat_belongs_to_condor_and_a_bound_one_to_its_specialist():
    """``binding.py``'s settled rule: an empty slug is the default agent."""
    assert deeds.run_key_for(deeds.for_conversation(USER, "c")) == "condor.chat"
    assert (
        deeds.run_key_for(deeds.for_conversation(USER, "c", "brigado"))
        == "brigado.chat"
    )


def test_each_door_gets_its_own_pseudo_strategy():
    assert deeds.run_key_for(deeds.for_ui(USER)) == "condor.ui"
    assert (
        deeds.run_key_for(deeds.for_delegation(USER, "t1", "brigado"))
        == "brigado.delegation"
    )


def test_a_strategy_may_not_be_named_after_a_pseudo_one():
    """Or ``brigado.chat`` would mean two runs and join as one."""
    store = StrategyStore()
    for name in ("chat", "Chat", "UI", "delegation"):
        with pytest.raises(ValueError, match="reserved"):
            store.create(agent_slug="brigado", name=name)

    # And an ordinary name is untouched.
    assert store.create(agent_slug="brigado", name="EMA Trend").slug == "ema_trend"


# ── Where the record lands ──


def test_a_chat_deed_lands_beside_its_conversation():
    owner = deeds.for_conversation(USER, "conv1")
    deeds.record_deeds(owner, _deploy_calls())
    assert deeds.deed_dir(owner) == paths.conversation_dir(USER, "conv1")
    assert (paths.conversation_dir(USER, "conv1") / "actions.jsonl").exists()


def test_a_delegation_deed_lands_beside_its_record():
    owner = deeds.for_delegation(USER, "task-9", "brigado")
    deeds.record_deeds(owner, _deploy_calls())
    assert deeds.deed_dir(owner) == paths.delegation_dir(USER, "task-9")
    assert (paths.delegation_dir(USER, "task-9") / "actions.jsonl").exists()


def test_a_dashboard_deed_lands_under_the_person_who_pressed_the_button():
    """The acting user is the path, not a field a route could forget."""
    owner = deeds.for_ui(USER)
    deeds.record_direct(
        owner,
        verb="manage_bots:stop_bot",
        summary="Bot 'pmm-king-btcbrl': stop_bot",
    )
    assert deeds.deed_dir(owner) == paths.ui_dir(USER)
    (row,) = read_actions(paths.ui_dir(USER))
    assert row.verb == "manage_bots:stop_bot"
    assert not (paths.ui_dir(9999)).exists(), "another user's directory is untouched"


def test_a_deed_with_nowhere_to_go_is_dropped_rather_than_guessed():
    """No user, no conversation, or an id that is not a path segment."""
    assert deeds.deed_dir(deeds.for_conversation(None, "c")) is None
    assert deeds.deed_dir(deeds.for_conversation(USER, "")) is None
    assert deeds.deed_dir(deeds.for_delegation(USER, "")) is None
    assert deeds.deed_dir(deeds.for_conversation(USER, "../escape")) is None
    assert deeds.deed_dir(deeds.for_ui(None)) is None

    # And recording against one is a no-op, not an exception.
    deeds.record_deeds(deeds.for_conversation(None, "c"), _deploy_calls())


# ── Nothing happened, nothing written ──


def test_a_run_that_mutates_nothing_creates_no_file_and_no_directory():
    owner = deeds.for_conversation(USER, "quiet")
    tc_map: dict[str, dict] = {}
    fold_tool_call_event(
        tc_map,
        ToolCallEvent(
            tool_call_id="1",
            title="get_portfolio_overview",
            status="completed",
            input={},
        ),
    )

    deeds.record_deeds(owner, list(tc_map.values()))

    assert not paths.conversation_dir(USER, "quiet").exists()


def test_an_empty_call_list_writes_nothing():
    owner = deeds.for_conversation(USER, "quiet")
    deeds.record_deeds(owner, [])
    assert not paths.conversation_dir(USER, "quiet").exists()


# ── Ownership, from the same folded list ──


def test_a_chat_that_deploys_owns_the_bot_it_deployed():
    owner = deeds.for_conversation(USER, "conv1", "brigado")

    deeds.record_deeds(owner, _deploy_calls("brigado-manual-btc"))

    (owned,) = read_owned(paths.conversation_dir(USER, "conv1"))
    assert owned.base == "brigado-manual-btc"
    assert owned.origin == "deployed"
    assert owned.since > 0


def test_the_chat_ledger_is_never_namespace_enforcing():
    """A chat may deploy under any name the user chose; the ledger only records."""
    import json

    owner = deeds.for_conversation(USER, "conv1")
    deeds.record_deeds(owner, _deploy_calls("a-name-nobody-namespaced"))

    data = json.loads(
        (paths.conversation_dir(USER, "conv1") / "owned_bots.json").read_text()
    )
    assert data["enforced"] is False
    assert data["namespace"] == "condor-chat"


def test_a_dashboard_deploy_owns_its_bot_too():
    deeds.record_direct(
        deeds.for_ui(USER),
        verb="manage_bots:deploy",
        summary="Deploy bot 'ui-deployed' with controllers ['c1']",
        subject="ui-deployed",
    )
    assert [b.base for b in read_owned(paths.ui_dir(USER))] == ["ui-deployed"]


def test_a_non_deploy_direct_deed_owns_nothing():
    deeds.record_direct(
        deeds.for_ui(USER),
        verb="stop_executor",
        summary="Stop executor abc...",
        subject="abc",
    )
    (row,) = read_actions(paths.ui_dir(USER))
    assert row.subject == "", "only a deploy has a bot for a subject"
    assert read_owned(paths.ui_dir(USER)) == []


def test_a_failed_direct_deed_is_recorded_but_owns_nothing():
    deeds.record_direct(
        deeds.for_ui(USER),
        verb="manage_bots:deploy",
        summary="Deploy bot 'never-happened' with controllers []",
        subject="never-happened",
        ok=False,
        error="upstream refused",
    )
    (row,) = read_actions(paths.ui_dir(USER))
    assert row.ok is False
    assert row.error == "upstream refused"
    assert read_owned(paths.ui_dir(USER)) == []


def test_rows_from_two_doors_are_the_same_shape():
    """The whole reason nothing downstream needs teaching."""
    chat = deeds.for_conversation(USER, "conv1")
    deeds.record_deeds(chat, _deploy_calls("from-chat"))
    deeds.record_direct(
        deeds.for_ui(USER),
        verb="manage_bots:deploy",
        summary="Deploy bot 'from-ui' with controllers ['c1']",
        subject="from-ui",
    )

    (a,) = read_actions(paths.conversation_dir(USER, "conv1"))
    (b,) = read_actions(paths.ui_dir(USER))
    assert vars(a).keys() == vars(b).keys()
    assert a.verb == b.verb == "manage_bots:deploy"
    assert a.tick == b.tick == 0, "outside a loop there is no tick, and 0 says so"


# ── The sixth door, answered by a test rather than by architecture ──

#: The route modules whose mutations reach the world. A new one belongs here.
_ROUTE_MODULES = ("condor.web.routes.bots", "condor.web.routes.executors")

#: The HTTP methods that change something. ``GET`` never does.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _mutating_route_sources() -> list[tuple[str, str, str]]:
    """``(module, function name, source)`` for every mutating route."""
    import importlib

    found: list[tuple[str, str, str]] = []
    for module_name in _ROUTE_MODULES:
        module = importlib.import_module(module_name)
        for route in module.router.routes:
            if not (set(getattr(route, "methods", ()) or ()) & _MUTATING_METHODS):
                continue
            endpoint = route.endpoint
            found.append((module_name, endpoint.__name__, inspect.getsource(endpoint)))
    return found


def test_there_are_mutating_routes_to_check():
    """A guard on the guard: an enumeration that finds nothing proves nothing."""
    assert len(_mutating_route_sources()) >= 13


def test_every_mutating_route_records_a_deed():
    """Add a route to ``bots.py`` or ``executors.py`` and wire it, or fail here.

    The one structural risk the design accepted was five call sites for one
    fact. This is what bounds it: a central seam could not have replaced it —
    the permission callback fires *before* a call runs, so it cannot know
    whether the deed succeeded, and these routes pass through no callback at all.
    """
    missing = [
        f"{module}.{name}"
        for module, name, source in _mutating_route_sources()
        if "record_ui_deed(" not in source
    ]
    assert not missing, (
        "these mutating routes change the world and record nothing: "
        + ", ".join(missing)
        + " — call record_ui_deed() after the upstream call succeeds."
    )


def test_every_route_deed_uses_a_verb_the_log_already_speaks():
    """A verb nobody else emits joins with nothing (FEAT-100's ``CREATE_VERBS``)."""
    from condor.runtime.danger import CREATE_EXECUTOR_TOOLS

    known = {
        *(f"manage_bots:{a}" for a in ("deploy", "stop_bot", "update_config")),
        "manage_bots:stop_controllers",
        "manage_bots:start_controllers",
        "manage_controllers:upsert",
        "manage_controllers:delete",
        "stop_executor",
        "clear_position_held",
        *CREATE_EXECUTOR_TOOLS,
    }
    for _module, name, source in _mutating_route_sources():
        for node in ast.walk(ast.parse(inspect.cleandoc(source))):
            if not (
                isinstance(node, ast.keyword)
                and node.arg == "verb"
                and isinstance(node.value, ast.Constant)
            ):
                continue
            assert node.value.value in known, f"{name} invents the verb {node.value!r}"


def test_the_ui_helper_is_the_only_deeds_entry_point_the_routes_use():
    """One helper, so the enumeration above cannot be sidestepped by accident."""
    for module_name in _ROUTE_MODULES:
        source = Path(__import__(module_name, fromlist=["_"]).__file__).read_text()
        assert "record_direct(" not in source


# ── The readers, unchanged ──


def test_the_existing_readers_work_on_a_conversation_with_no_changes():
    """``build_deployments`` renders a chat's deeds because they *are* actions.

    The acceptance criterion the whole design rests on: the files are the same
    files, so nothing downstream had to be taught a second shape.
    """
    from types import SimpleNamespace

    from condor.web.routes.agents import build_deployments

    owner = deeds.for_conversation(USER, "conv1")
    deeds.record_deeds(owner, _deploy_calls("pmm-king-btcbrl"))
    directory = deeds.deed_dir(owner)

    rows = build_deployments(
        owned=read_owned(directory),
        bot_bases=["pmm-king-btcbrl"],
        perf=SimpleNamespace(
            bot_names=["pmm-king-btcbrl-20260903-181000"],
            bot_instances=["pmm-king-btcbrl-20260903-181000"],
            controllers=[
                {
                    "bot_name": "pmm-king-btcbrl-20260903-181000",
                    "controller_id": "king-btcbrl-1",
                    "volume_traded": 1000.0,
                }
            ],
            executors=[],
        ),
        actions=read_actions(directory),
        agent_id="whatever",
    )

    bot = next(r for r in rows if r.kind == "bot")
    assert bot.label == "pmm-king-btcbrl"
    assert bot.live is True
    assert bot.volume == 1000.0
    # The chat's rows carry tick 0, and the join credits them honestly.
    assert bot.created_tick == 0
    assert any(r.kind == "controller" and r.label == "king-btcbrl-1" for r in rows)
