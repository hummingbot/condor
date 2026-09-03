"""A call's arguments survive arriving late, on a ``tool_call_update`` (FEAT-102).

``claude-agent-acp`` (0.21+) announces a tool call **twice**. The first
encounter is emitted at ``content_block_start``, while the tool's input JSON is
still streaming — its own source carries the comment "sometimes input is empty
object" — so the ``tool_call`` notification usually carries ``rawInput: {}``.
The second, once the full assistant message has arrived, is a
``tool_call_update`` carrying the *complete* ``rawInput``.

Condor's ``ToolCallUpdate`` had no field for it, so those arguments were dropped
permanently: ``actions.jsonl`` recorded "(arguments could not be read)" for a
tick that deployed a live six-controller fleet, and nothing downstream could
tell a read from a deploy. These tests pin the wire shape that actually reaches
us, so a regression here shows up as a failing test rather than as an agent
surface silently reporting $0.
"""

from __future__ import annotations

from condor.acp.client import (
    ToolCallEvent,
    ToolCallUpdate,
    fold_tool_call_event,
    normalize_tool_call,
)


def _client():
    """An ``ACPClient`` with a usable event queue and no subprocess."""
    import asyncio

    from condor.acp.client import ACPClient

    client = ACPClient.__new__(ACPClient)
    client._event_queue = asyncio.Queue()
    return client


def _drain(client) -> list:
    events = []
    while not client._event_queue.empty():
        events.append(client._event_queue.get_nowait())
    return events


# ── The wire ──


def test_an_update_carries_raw_input_off_the_wire():
    """``rawInput`` on a ``tool_call_update`` reaches the event as ``input``."""
    client = _client()

    client._on_session_update(
        "s",
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "1",
            "status": "completed",
            "rawInput": {"action": "upsert", "target": "config"},
        },
    )

    (event,) = _drain(client)
    assert isinstance(event, ToolCallUpdate)
    assert event.input == {"action": "upsert", "target": "config"}


def test_an_update_without_arguments_reports_none_not_an_empty_dict():
    """SEC-093's contract: unreadable stays ``None``, so the gate fails closed."""
    client = _client()

    client._on_session_update(
        "s",
        {"sessionUpdate": "tool_call_update", "toolCallId": "1", "status": "completed"},
    )

    (event,) = _drain(client)
    assert event.input is None


# ── The fold ──


def test_arguments_that_arrive_late_still_land_on_the_folded_call():
    """The real adapter's two-encounter sequence, end to end.

    A ``tool_call`` announced while the input is still streaming, then the
    ``tool_call_update`` that carries the complete arguments.
    """
    tc_map: dict[str, dict] = {}

    fold_tool_call_event(
        tc_map,
        ToolCallEvent(
            tool_call_id="1",
            title="mcp__mcp-hummingbot__manage_bots",
            status="pending",
            input={},  # streaming has not delivered the arguments yet
        ),
    )
    assert tc_map["1"].get("input") is None, "an empty input must not be stored"

    fold_tool_call_event(
        tc_map,
        ToolCallUpdate(
            tool_call_id="1",
            status="completed",
            input={"action": "deploy", "bot_name": "pmm-king-btcbrl-20260903-181000"},
        ),
    )

    assert tc_map["1"]["input"] == {
        "action": "deploy",
        "bot_name": "pmm-king-btcbrl-20260903-181000",
    }
    assert tc_map["1"]["status"] == "completed"


def test_a_later_empty_update_does_not_erase_arguments_already_read():
    """The non-erasing guard: a terminal status update carries no arguments."""
    tc_map: dict[str, dict] = {}

    fold_tool_call_event(
        tc_map,
        ToolCallEvent(
            tool_call_id="1",
            title="manage_controllers",
            status="pending",
            input={"action": "upsert", "config_name": "king-btcbrl-1"},
        ),
    )
    fold_tool_call_event(tc_map, ToolCallUpdate(tool_call_id="1", status="failed"))

    assert tc_map["1"]["input"] == {"action": "upsert", "config_name": "king-btcbrl-1"}
    assert tc_map["1"]["status"] == "failed"


def test_normalize_reads_the_same_key_on_an_update_as_on_a_create():
    """One translation seam, not two spellings (SEC-093)."""
    payload = {"toolCallId": "1", "rawInput": {"action": "delete"}}
    assert normalize_tool_call(payload)["input"] == {"action": "delete"}
