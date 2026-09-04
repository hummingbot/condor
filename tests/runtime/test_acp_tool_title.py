"""A malformed ACP tool title never reaches disk (CORR-327).

A real transcript carries five ``kind: "fetch"`` calls titled ``'"undefined"'``
— a JavaScript ``undefined`` that was JSON-encoded on its way to the wire,
quote characters and all. Condor did not create that value, but it wrote it
down verbatim, and a transcript is read forever: the chat strip, the share view
and the journal all print it back at the user for the life of the record.

So these pin the *persistence* fix rather than the display one. Two seams, one
rule (:func:`normalize_tool_title`): the ACP client, where the wire is
translated for every consumer, and the recorder, the last gate before disk for
the producers that never touch the wire.
"""

import pytest

from condor.acp.client import (
    ACPClient,
    ToolCallEvent,
    ToolCallUpdate,
    normalize_tool_call,
    normalize_tool_title,
)
from condor.runtime.conversations import (
    Recorder,
    new_conversation,
    read_transcript,
)
from condor.runtime.events import RuntimeEvent

USER = 481175164
WEB = "web"

#: Every shape the adapter has been seen to send, or could send, in place of a
#: name. The quoted one is the shape from the transcript above.
MALFORMED = ['"undefined"', "undefined", "null", "None", "", "   ", None, 42, {}]


@pytest.fixture
def conv_root(isolated_conversation_root):
    """The throwaway store root (see ``conftest.py``)."""
    return isolated_conversation_root


# ── The rule ──


@pytest.mark.parametrize("title", MALFORMED)
def test_a_title_that_says_nothing_normalizes_to_nothing(title):
    assert normalize_tool_title(title) == ""


@pytest.mark.parametrize(
    "title", ["ToolSearch", "mcp__condor__run_code", "manage_bots", "Read"]
)
def test_a_real_title_is_passed_through_byte_identical(title):
    """The seam repairs; it must never rewrite a name that was fine."""
    assert normalize_tool_title(title) == title


def test_a_json_quoted_name_is_unwrapped_not_discarded():
    """The quotes are an encoding artefact — the name inside them is real."""
    assert normalize_tool_title('"WebSearch"') == "WebSearch"


# ── The wire seam ──


def _drive(update: dict):
    client = ACPClient(command="true")
    client._on_session_update("s", update)
    return client._event_queue.get_nowait()


@pytest.mark.parametrize("title", MALFORMED)
def test_an_announced_call_reads_as_its_kind_rather_than_as_a_lie(title):
    """A broken fetch should read as ``fetch``, never as ``"undefined"``."""
    event = _drive(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "1",
            "title": title,
            "kind": "fetch",
            "status": "completed",
        }
    )

    assert isinstance(event, ToolCallEvent)
    assert event.title == "fetch"


def test_an_announced_call_prefers_a_tool_name_to_a_kind():
    event = _drive(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "1",
            "title": '"undefined"',
            "tool": "WebSearch",
            "kind": "fetch",
        }
    )

    assert event.title == "WebSearch"


@pytest.mark.parametrize("title", MALFORMED)
def test_an_update_that_names_nothing_stays_empty(title):
    """No ``kind`` fallback on an update.

    The shared fold patches the name whenever the update carries one, so a
    title derived from a category would overwrite the real name the
    announcement already supplied.
    """
    event = _drive(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "1",
            "title": title,
            "kind": "fetch",
            "status": "completed",
        }
    )

    assert isinstance(event, ToolCallUpdate)
    assert event.title == ""


def test_the_gate_still_dispatches_on_the_wire_name():
    """``tool`` takes no ``kind`` fallback: a category is not a tool name."""
    normalized = normalize_tool_call(
        {"toolCallId": "1", "title": "mcp__mcp-hummingbot__manage_bots"}
    )

    assert normalized["tool"] == "mcp__mcp-hummingbot__manage_bots"
    assert normalize_tool_call({"toolCallId": "1", "kind": "fetch"})["tool"] == ""


# ── Nothing malformed reaches disk ──


@pytest.mark.parametrize("title", MALFORMED)
def test_no_malformed_title_is_ever_written_to_a_transcript(conv_root, title):
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "what is the news?")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(
                tool_call_id="w", title=title, status="completed", kind="fetch"
            )
        )
    )
    rec.flush()

    recorded = read_transcript(USER, meta.id)[-1].tool_calls[0]["title"]
    assert recorded == "fetch"
    assert "undefined" not in recorded


@pytest.mark.parametrize("title", MALFORMED)
def test_a_malformed_late_title_cannot_overwrite_a_real_one(conv_root, title):
    """The path CORR-326 opened: an update's title now reaches disk."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "what is the news?")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(
                tool_call_id="w", title="WebSearch", status="pending", kind="fetch"
            )
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(tool_call_id="w", status="completed", title=title)
        )
    )
    rec.flush()

    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["title"] == "WebSearch"
    assert call["status"] == "completed"


def test_a_real_late_title_still_names_the_call(conv_root):
    """The repair must not cost the recorder the titles it does accept."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "what moved?")

    rec.observe(
        RuntimeEvent.from_acp(ToolCallEvent(tool_call_id="t", title="", status="run"))
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(tool_call_id="t", status="completed", title="get_prices")
        )
    )
    rec.flush()

    assert read_transcript(USER, meta.id)[-1].tool_calls[0]["title"] == "get_prices"
