"""Tests for the durable conversation store (FEAT-015).

The store is the half of the feature that has to be boring: a transcript that
loses a turn, or a replay that overruns the context window, is worse than no
persistence at all. So these pin the two properties everything else leans on —
an append never loses a line, and ``replay_context`` is bounded while keeping
the *newest* turns.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk, ToolCallEvent, ToolCallUpdate
from condor.runtime import PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import (
    REDACTED,
    REPLAY_HEADER,
    REPLAY_MAX_CHARS,
    REPLAY_OMITTED,
    TOOL_INPUT_MAX_CHARS,
    TOOL_OUTPUT_MAX_CHARS,
    ConversationIdError,
    Recorder,
    TurnEntry,
    _conv_dir,
    _iter_lines_reverse,
    _render_turn,
    append_turn,
    delete_conversation,
    flush_all,
    get_conversation,
    list_conversations,
    new_conversation,
    read_transcript,
    record_system,
    rename,
    replay_context,
    update_meta,
)
from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import WEB

USER = 42
OTHER_USER = 43


@pytest.fixture
def conv_root(isolated_conversation_root):
    """The throwaway store root (see ``conftest.py``)."""
    return isolated_conversation_root


def test_a_conversation_lives_under_its_owner(conv_root):
    """The user is the first path segment — see ``tests/runtime/test_paths.py``."""
    meta = new_conversation(USER, WEB)

    assert _conv_dir(USER, meta.id) == (
        conv_root / str(USER) / "conversations" / meta.id
    )


# ── Lifecycle ──


def test_create_read_roundtrip(conv_root):
    meta = new_conversation(USER, WEB, agent_key="claude-code", server_name="prod")

    assert meta.user_id == USER
    assert meta.surface == WEB
    assert meta.turn_count == 0
    assert (conv_root / str(USER) / "conversations" / meta.id / "meta.json").is_file()

    loaded = get_conversation(USER, meta.id)
    assert loaded is not None
    assert loaded.id == meta.id
    assert loaded.agent_key == "claude-code"
    assert loaded.server_name == "prod"


def test_missing_conversation_is_none_not_an_error(conv_root):
    assert get_conversation(USER, "nope") is None
    assert list_conversations(USER) == []
    assert read_transcript(USER, "nope") == []
    assert delete_conversation(USER, "nope") is False
    assert rename(USER, "nope", "title") is False
    assert update_meta(USER, "nope", title="x") is False
    assert replay_context(USER, "nope") == ""


def test_conversations_are_scoped_per_user(conv_root):
    mine = new_conversation(USER, WEB)
    theirs = new_conversation(OTHER_USER, WEB)

    assert [m.id for m in list_conversations(USER)] == [mine.id]
    assert [m.id for m in list_conversations(OTHER_USER)] == [theirs.id]
    # A conversation is not readable under the wrong owner's directory.
    assert get_conversation(USER, theirs.id) is None


def test_list_is_newest_first_and_limited(conv_root):
    ids = []
    for i in range(4):
        meta = new_conversation(USER, WEB)
        append_turn(USER, meta.id, TurnEntry(role="user", text=f"turn {i}"))
        ids.append(meta.id)

    listed = [m.id for m in list_conversations(USER)]
    assert listed[0] == ids[-1], "most recently updated must sort first"
    assert set(listed) == set(ids)
    assert len(list_conversations(USER, limit=2)) == 2


def test_list_parses_only_the_metas_it_returns(conv_root, monkeypatch):
    """A limited listing costs a stat per conversation, not a parse (PERF-328).

    The store is never pruned, so N grows for the life of the install; the
    dashboard rail asks for 100 rows and its prewarm for 1, and both used to
    read and validate every meta on disk to get them.
    """
    ids = [new_conversation(USER, WEB).id for _ in range(50)]

    real_read_status = conversations.read_status
    parsed: list[str] = []

    def counting(session_dir, filename=conversations.META_FILENAME):
        parsed.append(session_dir.name)
        return real_read_status(session_dir, filename)

    monkeypatch.setattr(conversations, "read_status", counting)

    parsed.clear()
    newest = list_conversations(USER, limit=1)
    assert [m.id for m in newest] == [ids[-1]], "still the newest conversation"
    assert parsed == [ids[-1]], "exactly one meta.json opened, not fifty"

    parsed.clear()
    assert len(list_conversations(USER, limit=5)) == 5
    assert len(parsed) == 5

    parsed.clear()
    assert len(list_conversations(USER, limit=0)) == 50, "limit=0 still walks all"
    assert len(parsed) == 50


def test_an_unreadable_meta_is_skipped_by_a_limited_listing(conv_root):
    """The newest conversation being half written must not eat a returned row."""
    ids = [new_conversation(USER, WEB).id for _ in range(4)]
    (_conv_dir(USER, ids[-1]) / conversations.META_FILENAME).write_text("{not json")

    listed = [m.id for m in list_conversations(USER, limit=2)]

    assert listed == [ids[-2], ids[-3]], "skipped, and the limit is still filled"


def test_delete_removes_the_transcript(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="hi"))

    assert delete_conversation(USER, meta.id) is True
    assert get_conversation(USER, meta.id) is None
    assert list_conversations(USER) == []


# ── Path safety ──


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "with space", "x:y"])
def test_ids_that_would_escape_the_root_are_rejected(conv_root, bad):
    with pytest.raises(ConversationIdError):
        get_conversation(USER, bad)


# ── Transcript ──


def test_append_and_read_preserve_order_and_shape(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="what is my pnl?"))
    append_turn(
        USER,
        meta.id,
        TurnEntry(
            role="assistant",
            text="Up $120.",
            thought="checking",
            tool_calls=[
                {"id": "t1", "title": "get_portfolio_overview", "status": "completed"}
            ],
        ),
    )

    turns = read_transcript(USER, meta.id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[1].tool_calls[0]["title"] == "get_portfolio_overview"
    assert turns[1].thought == "checking"


def test_meta_tracks_title_count_and_snippet(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="  first   question  "))
    append_turn(USER, meta.id, TurnEntry(role="assistant", text="an answer"))
    append_turn(USER, meta.id, TurnEntry(role="user", text="second question"))

    loaded = get_conversation(USER, meta.id)
    assert loaded.turn_count == 3
    assert loaded.title == "first question", "title is the first user message only"
    assert loaded.last_snippet == "an answer"


def test_long_title_is_truncated(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="x" * 500))
    assert len(get_conversation(USER, meta.id).title) <= 80


def test_a_corrupt_line_is_skipped_not_fatal(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="one"))
    path = conv_root / str(USER) / "conversations" / meta.id / "transcript.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
        fh.write(json.dumps({"role": "assistant", "text": "two"}) + "\n")

    turns = read_transcript(USER, meta.id)
    assert [t.text for t in turns] == ["one", "two"]


def test_a_line_from_another_version_of_the_shape_still_parses(conv_root):
    """The transcript is append-only on disk, so the shape must age in both
    directions: a line written before the attribution fields existed reads back
    unattributed, and a line carrying a key this build does not know is kept
    rather than dropped."""
    meta = new_conversation(USER, WEB)
    path = conv_root / str(USER) / "conversations" / meta.id / "transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "assistant", "text": "older build"}) + "\n")
        fh.write(
            json.dumps(
                {"role": "assistant", "text": "newer build", "not_a_field_yet": "x"}
            )
            + "\n"
        )

    turns = read_transcript(USER, meta.id)
    assert [t.text for t in turns] == ["older build", "newer build"]
    old = turns[0]
    assert (old.agent_key, old.agent_slug) == (
        "",
        "",
    ), "an unattributed turn stays empty rather than being backfilled with a guess"
    assert (
        old.stop_reason == ""
    ), "a line written before stop_reason existed still reads"


def test_read_transcript_returns_the_tail(conv_root):
    meta = new_conversation(USER, WEB)
    for i in range(10):
        append_turn(USER, meta.id, TurnEntry(role="user", text=f"m{i}"))

    tail = read_transcript(USER, meta.id, limit=3)
    assert [t.text for t in tail] == ["m7", "m8", "m9"]
    assert len(read_transcript(USER, meta.id, limit=0)) == 10


def test_rename_and_update_meta(conv_root):
    meta = new_conversation(USER, WEB)
    assert rename(USER, meta.id, "Funding arb") is True
    assert update_meta(USER, meta.id, agent_key="gemini", agent_slug="brigado") is True

    loaded = get_conversation(USER, meta.id)
    assert loaded.title == "Funding arb"
    assert loaded.agent_key == "gemini"
    assert loaded.agent_slug == "brigado"


# ── Replay ──


def test_replay_frames_the_transcript(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="what is my pnl?"))
    append_turn(
        USER,
        meta.id,
        TurnEntry(
            role="assistant",
            text="Up $120.",
            tool_calls=[
                {"id": "t1", "title": "get_portfolio_overview", "status": "completed"}
            ],
        ),
    )

    replay = replay_context(USER, meta.id)
    assert replay.startswith(REPLAY_HEADER)
    assert "[user] what is my pnl?" in replay
    assert "[assistant] Up $120." in replay
    assert "→ used get_portfolio_overview" in replay
    assert REPLAY_OMITTED not in replay


def test_replay_renders_tools_as_names_not_results(conv_root):
    """A resumed agent must not read a stale result as if it were current."""
    meta = new_conversation(USER, WEB)
    append_turn(
        USER,
        meta.id,
        TurnEntry(
            role="assistant",
            text="",
            tool_calls=[
                {
                    "id": "t1",
                    "title": "get_market_data",
                    "status": "completed",
                    "output": "BTC 91000",
                }
            ],
        ),
    )

    replay = replay_context(USER, meta.id)
    assert "→ used get_market_data" in replay
    assert "91000" not in replay


def test_replay_respects_its_bound_and_keeps_the_newest_turns(conv_root):
    meta = new_conversation(USER, WEB)
    for i in range(300):
        append_turn(
            USER,
            meta.id,
            TurnEntry(role="user", text=f"message number {i} " + "x" * 100),
        )

    replay = replay_context(USER, meta.id, max_chars=1000)
    assert len(replay) <= 1000
    assert "message number 299" in replay, "the newest turn must survive the bound"
    assert "message number 0 " not in replay
    assert REPLAY_OMITTED in replay


def test_replay_keeps_something_when_one_turn_overruns_the_bound(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="y" * 5000))

    replay = replay_context(USER, meta.id, max_chars=200)
    assert 0 < len(replay) <= 200
    assert "yyy" in replay


def test_replay_skips_empty_turns(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="assistant", text="", tool_calls=[]))
    append_turn(USER, meta.id, TurnEntry(role="user", text="hello"))

    replay = replay_context(USER, meta.id)
    assert replay == f"{REPLAY_HEADER}\n[user] hello"


def test_system_entries_render_as_notes(conv_root):
    meta = new_conversation(USER, WEB)
    append_turn(USER, meta.id, TurnEntry(role="user", text="hi"))
    record_system(USER, meta.id, "Switched to brigado", kind="switch")

    replay = replay_context(USER, meta.id)
    assert "(Switched to brigado)" in replay
    assert read_transcript(USER, meta.id)[-1].kind == "switch"


# ── Recorder ──


def _events(*pairs) -> list[RuntimeEvent]:
    return list(pairs)


def test_recorder_writes_two_turns_per_prompt(conv_root):
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "how much cash?")

    for chunk in ("You ", "have ", "$50."):
        rec.observe(RuntimeEvent(type="text", data={"text": chunk}))
    rec.observe(
        RuntimeEvent(
            type="tool_call",
            data={
                "tool_call_id": "t1",
                "title": "get_portfolio_overview",
                "status": "pending",
                "kind": "mcp",
                "input": {"account": "master"},
            },
        )
    )
    rec.observe(
        RuntimeEvent(
            type="tool_update",
            data={
                "tool_call_id": "t1",
                "status": "completed",
                "output": "total: $50.00",
            },
        )
    )
    rec.observe(RuntimeEvent(type="done", data={"stop_reason": "end_turn"}))
    rec.flush()

    turns = read_transcript(USER, meta.id)
    assert len(turns) == 2, "exactly one user turn and one assistant turn"
    assert turns[0].role == "user" and turns[0].text == "how much cash?"
    assert turns[1].text == "You have $50.", "chunks are joined, not written per chunk"
    assert turns[1].tool_calls == [
        {
            "id": "t1",
            "title": "get_portfolio_overview",
            "status": "completed",
            "kind": "mcp",
            "input": {"account": "master"},
            "output": "total: $50.00",
        }
    ], "the trajectory is what the tool was asked and what it answered"


def test_recorder_keeps_tool_io_on_the_acp_path(conv_root):
    """ACP: the call carries the input, a later update carries the output.

    Mirrors ``RuntimeEvent.from_acp`` — ``tool_call`` has input/kind,
    ``tool_call_update`` has the output — so this pins the shape the real
    bridge produces, not an invented one.
    """
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "read the file")

    rec.observe(
        RuntimeEvent(
            type="tool_call",
            data={
                "tool_call_id": "call_9",
                "title": "Read (config.py)",
                "status": "in_progress",
                "kind": "read",
                "input": {"path": "config.py"},
            },
        )
    )
    rec.observe(
        RuntimeEvent(
            type="tool_update",
            data={
                "tool_call_id": "call_9",
                "status": "completed",
                "output": "PORT = 8080",
            },
        )
    )
    rec.flush()

    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["kind"] == "read"
    assert call["input"] == {"path": "config.py"}
    assert call["output"] == "PORT = 8080"


def test_recorder_persists_arguments_that_arrive_late_on_an_acp_update(conv_root):
    """The real ACP sequence, through both links of the chat chain (CORR-326).

    ``claude-agent-acp`` announces a call at ``content_block_start`` while the
    input JSON is still streaming — ``rawInput`` is routinely ``{}`` — and
    carries the complete arguments on the following ``tool_call_update``
    (FEAT-102). Built from the dataclasses through ``RuntimeEvent.from_acp``
    rather than from hand-written payloads, because the projection is the link
    that used to drop them: a transcript that records that the agent called
    ``create_position_executor`` but not on what is not a trajectory.
    """
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "open a position")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(
                tool_call_id="call_1",
                title="create_position_executor",
                status="pending",
                kind="other",
                input={},  # streaming has not delivered the arguments yet
            )
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(
                tool_call_id="call_1",
                status="completed",
                input={"connector_name": "binance", "trading_pair": "SOL-USDC"},
                output="executor started",
            )
        )
    )
    rec.flush()

    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["input"] == {"connector_name": "binance", "trading_pair": "SOL-USDC"}
    assert call["status"] == "completed"
    assert call["output"] == "executor started"


def test_recorder_redacts_arguments_that_arrive_late(conv_root):
    """Landing late buys no exemption from the redactor."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "connect the server")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(
                tool_call_id="cfg", title="configure_server", status="pending", input={}
            )
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(
                tool_call_id="cfg",
                status="completed",
                input={"host": "localhost", "password": "barabit"},
            )
        )
    )
    rec.flush()

    raw = (
        conv_root / str(USER) / "conversations" / meta.id / "transcript.jsonl"
    ).read_text()
    assert "barabit" not in raw
    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["input"]["host"] == "localhost"
    assert call["input"]["password"] == REDACTED


def test_recorder_lets_a_late_update_name_a_call_the_announcement_did_not(conv_root):
    """A title supplied on the update reaches disk under the on-disk spelling."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "what moved?")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(tool_call_id="t", title="", status="pending")
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(tool_call_id="t", status="completed", title="get_prices")
        )
    )
    rec.flush()

    assert read_transcript(USER, meta.id)[-1].tool_calls[0]["title"] == "get_prices"


def test_recorder_does_not_let_a_later_empty_update_erase_what_arrived(conv_root):
    """The non-erasing guard, on every field a later update can carry."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "deploy it")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(
                tool_call_id="d",
                title="manage_bots",
                status="pending",
                input={"action": "deploy"},
            )
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(tool_call_id="d", status="completed", output="deployed")
        )
    )
    rec.observe(RuntimeEvent.from_acp(ToolCallUpdate(tool_call_id="d")))
    rec.flush()

    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["input"] == {"action": "deploy"}
    assert call["title"] == "manage_bots"
    assert call["output"] == "deployed"
    assert call["status"] == "completed"


def test_recorder_patches_a_repeated_announcement_instead_of_replacing_it(conv_root):
    """One id is one call: a second announcement must not erase its result.

    The recorder used to overwrite ``self._tools[call_id]`` wholesale, so a
    call announced twice — which is exactly what the ACP adapter does — lost
    whatever status and output had already been recorded for it.
    """
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "read it")

    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(tool_call_id="r", title="Read", status="pending", kind="read")
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallUpdate(tool_call_id="r", status="completed", output="PORT = 8080")
        )
    )
    rec.observe(
        RuntimeEvent.from_acp(
            ToolCallEvent(
                tool_call_id="r",
                title="Read",
                status="in_progress",
                kind="read",
                input={"path": "config.py"},
            )
        )
    )
    rec.flush()

    calls = read_transcript(USER, meta.id)[-1].tool_calls
    assert len(calls) == 1, "one tool_call_id is one recorded call"
    assert calls[0]["output"] == "PORT = 8080"
    assert calls[0]["input"] == {"path": "config.py"}


def test_recorder_keeps_tool_io_on_the_pydantic_ai_path(conv_root):
    """pydantic-ai emits a bare "completed" first and the result after it.

    ``prompt_stream`` yields ``ToolCallUpdate(status="completed")`` the moment
    the call is issued and only learns the result on the next
    ``ModelRequestNode``. The empty first update must not win.
    """
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "what moved?")

    rec.observe(
        RuntimeEvent(
            type="tool_call",
            data={
                "tool_call_id": "toolu_1",
                "title": "get_market_data",
                "status": "in_progress",
                "kind": "mcp",
                "input": {"pair": "BTC-USDT"},
            },
        )
    )
    rec.observe(
        RuntimeEvent(
            type="tool_update", data={"tool_call_id": "toolu_1", "status": "completed"}
        )
    )
    rec.observe(
        RuntimeEvent(
            type="tool_update",
            data={
                "tool_call_id": "toolu_1",
                "status": "completed",
                "output": "BTC-USDT +2.4%",
            },
        )
    )
    rec.flush()

    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["output"] == "BTC-USDT +2.4%", "an empty update must not erase a result"


def test_recorder_caps_tool_io(conv_root):
    """A tool that returns a megabyte does not write a megabyte."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "dump everything")

    rec.observe(
        RuntimeEvent(
            type="tool_call",
            data={
                "tool_call_id": "big",
                "title": "get_market_data",
                "status": "in_progress",
                "input": {"candles": ["x" * 200 for _ in range(500)]},
            },
        )
    )
    rec.observe(
        RuntimeEvent(
            type="tool_update",
            data={
                "tool_call_id": "big",
                "status": "completed",
                "output": "y" * 1_000_000,
            },
        )
    )
    rec.flush()

    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert len(call["output"]) <= TOOL_OUTPUT_MAX_CHARS
    assert "_clipped" in call["input"], "an oversized argument set stays a dict"
    assert len(call["input"]["_clipped"]) <= TOOL_INPUT_MAX_CHARS

    written = (
        conv_root / str(USER) / "conversations" / meta.id / "transcript.jsonl"
    ).read_text()
    assert len(written) < 10_000, "the whole transcript stays small"


def test_recorder_does_not_persist_credentials_from_tool_arguments(conv_root):
    """``configure_server(password=…)`` is a real tool in the agents' toolset."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "connect the server")

    rec.observe(
        RuntimeEvent(
            type="tool_call",
            data={
                "tool_call_id": "cfg",
                "title": "configure_server",
                "status": "in_progress",
                "input": {
                    "host": "localhost",
                    "username": "admin",
                    "password": "barabit",
                    "nested": {"API_KEY": "sk-live-123"},
                },
            },
        )
    )
    rec.flush()

    raw = (
        conv_root / str(USER) / "conversations" / meta.id / "transcript.jsonl"
    ).read_text()
    assert "barabit" not in raw and "sk-live-123" not in raw
    call = read_transcript(USER, meta.id)[-1].tool_calls[0]
    assert call["input"]["password"] == REDACTED
    assert call["input"]["nested"]["API_KEY"] == REDACTED
    assert call["input"]["host"] == "localhost", "only the secrets go"


def test_recorder_stamps_the_assistant_turn_with_who_answered(conv_root):
    meta = new_conversation(USER, WEB)
    rec = Recorder(
        USER,
        meta.id,
        "hi",
        agent_key="gemini",
        agent_slug="brigado",
    )
    rec.observe(RuntimeEvent(type="text", data={"text": "hello"}))
    rec.flush()

    turns = read_transcript(USER, meta.id)
    assert (turns[1].agent_key, turns[1].agent_slug) == ("gemini", "brigado")


def test_recorder_without_attribution_records_an_unattributed_turn(conv_root):
    """The positional call still works, and records rather than guessing."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "hi")
    rec.observe(RuntimeEvent(type="text", data={"text": "hello"}))
    rec.flush()

    turns = read_transcript(USER, meta.id)
    assert turns[1].text == "hello"
    assert (turns[1].agent_key, turns[1].agent_slug) == ("", "")


def test_recorder_flush_is_idempotent(conv_root):
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "hi")
    rec.observe(RuntimeEvent(type="text", data={"text": "hello"}))
    rec.flush()
    rec.flush()

    assert len(read_transcript(USER, meta.id)) == 2


def test_recorder_keeps_a_partial_reply(conv_root):
    """The reload-mid-answer case: whatever streamed must survive."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "explain the strategy")
    rec.observe(RuntimeEvent(type="text", data={"text": "It works by "}))
    rec.flush()  # no DONE ever arrives

    turns = read_transcript(USER, meta.id)
    assert turns[1].text == "It works by "
    assert (
        turns[1].stop_reason == ""
    ), "an abandoned stream reports no ending, and the turn says so"


@pytest.mark.parametrize("reason", ["end_turn", "cancelled", "timeout", "error"])
def test_recorder_records_how_the_stream_ended(conv_root, reason):
    """A truncated answer has to be tellable from a finished one on disk."""
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "explain the strategy")
    rec.observe(RuntimeEvent(type="text", data={"text": "It works by "}))
    rec.observe(RuntimeEvent(type="done", data={"stop_reason": reason}))
    rec.flush()

    turns = read_transcript(USER, meta.id)
    assert turns[1].stop_reason == reason


def test_recorder_records_the_user_turn_even_with_no_reply(conv_root):
    meta = new_conversation(USER, WEB)
    Recorder(USER, meta.id, "silence").flush()

    turns = read_transcript(USER, meta.id)
    assert [t.role for t in turns] == ["user"]


def test_recorder_records_an_error_as_a_system_note(conv_root):
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "boom")
    rec.observe(RuntimeEvent(type="error", data={"message": "Agent is busy"}))
    rec.flush()

    turns = read_transcript(USER, meta.id)
    assert turns[1].role == "system" and turns[1].kind == "error"


def test_recorder_without_a_conversation_is_a_no_op(conv_root):
    """Any caller not yet threaded degrades to today's behavior, not a crash."""
    rec = Recorder(USER, "", "hi")
    rec.observe(RuntimeEvent(type="text", data={"text": "hello"}))
    rec.flush()
    assert list_conversations(USER) == []


def test_flush_all_writes_recorders_still_in_flight(conv_root):
    meta = new_conversation(USER, WEB)
    rec = Recorder(USER, meta.id, "interrupted by shutdown")
    rec.observe(RuntimeEvent(type="text", data={"text": "partial"}))

    flush_all()

    turns = read_transcript(USER, meta.id)
    assert [t.text for t in turns] == ["interrupted by shutdown", "partial"]
    flush_all()  # already drained
    assert len(read_transcript(USER, meta.id)) == 2


# ── Through the runtime ──
#
# The point of the feature is not that the store works, it is that a session
# writes into it and a new session reads back out. These drive the real facade.


class _ScriptedClient:
    """Client that replays a fixed list of ACP events."""

    script: list = []

    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        self.prompts.append(text)
        return "ok"

    async def prompt_stream(self, text):
        self.prompts.append(text)
        for event in type(self).script:
            yield event


@pytest.fixture
def registry(monkeypatch, conv_root):
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _ScriptedClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    _ScriptedClient.script = [
        TextChunk(text="the answer"),
        PromptDone(stop_reason="end_turn"),
    ]
    return session_module


async def _chat(key: SessionKey, text: str) -> list:
    return [e async for e in runtime.prompt(key, PromptRequest(text=text))]


def test_a_session_mints_and_records_a_conversation(registry):
    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        await _chat(key, "what is my pnl?")
        return info

    info = asyncio.run(scenario())

    assert info.conversation_id, "every owned session gets a conversation"
    turns = read_transcript(USER, info.conversation_id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].text == "what is my pnl?"
    assert turns[1].text == "the answer"
    assert get_conversation(USER, info.conversation_id).surface == "tg"


def test_destroying_a_session_keeps_the_conversation(registry):
    """The whole point: the transcript is not collateral of a dead subprocess."""
    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        await _chat(key, "remember this")
        await runtime.destroy(key)
        return info

    info = asyncio.run(scenario())

    assert asyncio.run(runtime.get_info(key)) is None
    assert get_conversation(USER, info.conversation_id) is not None
    assert len(read_transcript(USER, info.conversation_id)) == 2


def test_resuming_replays_the_transcript_into_the_new_session(registry):
    """Continue a conversation the previous subprocess took to the grave."""
    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        await _chat(key, "my favourite pair is SOL-USDC")
        await runtime.destroy(key)

        resumed = await runtime.create_session(
            SessionSpec(
                key=str(key),
                agent_key="claude-code",
                chat_id=USER,
                user_id=USER,
                lazy_context=True,
                conversation_id=info.conversation_id,
            )
        )
        await _chat(key, "what was it again?")
        return info, resumed, _ScriptedClient.last.prompts

    info, resumed, prompts = asyncio.run(scenario())

    assert resumed.conversation_id == info.conversation_id, "same conversation"
    assert REPLAY_HEADER in prompts[0]
    assert "SOL-USDC" in prompts[0], "the new brain is handed the old transcript"
    # And the continuation lands in the same file, not a second one.
    assert len(list_conversations(USER)) == 1
    assert len(read_transcript(USER, info.conversation_id)) == 4


def test_resuming_a_different_conversation_replaces_the_session(registry):
    """A Telegram key is stable, so the conversation is what distinguishes chats."""
    key = SessionKey.telegram(USER)
    other = new_conversation(USER, "tg")
    append_turn(USER, other.id, TurnEntry(role="user", text="an older chat"))

    async def scenario():
        first = await runtime.create_session(
            SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        second = await runtime.create_session(
            SessionSpec(
                key=str(key),
                agent_key="claude-code",
                chat_id=USER,
                user_id=USER,
                lazy_context=True,
                conversation_id=other.id,
            )
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first.conversation_id != second.conversation_id
    assert second.conversation_id == other.id


def test_a_session_without_an_owner_records_nothing(registry):
    """No owner means no keyspace to file it under — and no crash either."""
    key = SessionKey.telegram(7)

    async def scenario():
        info = await runtime.create_session(
            SessionSpec(key=str(key), agent_key="claude-code", chat_id=7)
        )
        await _chat(key, "hello")
        return info

    assert asyncio.run(scenario()).conversation_id == ""


def test_meta_follows_the_model_that_answered_last(registry):
    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        await runtime.create_session(
            SessionSpec(
                key=str(key),
                agent_key="gemini",
                chat_id=USER,
                user_id=USER,
                lazy_context=True,
                conversation_id=info.conversation_id,
            )
        )
        return info

    info = asyncio.run(scenario())
    assert get_conversation(USER, info.conversation_id).agent_key == "gemini"


@pytest.fixture
def bound_agent(tmp_path, monkeypatch):
    """An agents/ tree with one serverless Agent answering over ACP."""
    from condor.agents import agent as agent_module
    from condor.agents.agent import AgentStore

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    return AgentStore().create(
        name="Brigado",
        description="Domain agent",
        instructions="You are Brigado.",
        agent_key="claude-code",
        server_required=False,
    )


def test_each_turn_keeps_the_agent_that_answered_it(registry, bound_agent):
    """The reason attribution lives on the turn: the meta cannot hold it.

    Same conversation, assistant first and a bound Agent after. The meta only
    remembers the last one; the transcript remembers both, in order.
    """
    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        await _chat(key, "assistant, hello")
        # The real switch path (routes/sessions.py _respawn): same key, same
        # conversation, new binding.
        await runtime.create_session(
            SessionSpec(
                key=str(key),
                agent_key="",
                agent_slug=bound_agent.slug,
                chat_id=USER,
                user_id=USER,
                lazy_context=True,
                conversation_id=info.conversation_id,
            )
        )
        await _chat(key, "brigado, hello")
        return info

    info = asyncio.run(scenario())

    replies = [
        t for t in read_transcript(USER, info.conversation_id) if t.role == "assistant"
    ]
    assert [t.agent_slug for t in replies] == ["", bound_agent.slug]
    assert [t.agent_key for t in replies] == ["claude-code", "claude-code"]
    assert (
        get_conversation(USER, info.conversation_id).agent_slug == bound_agent.slug
    ), "the meta is still last-write-wins; only the turns hold the history"


# ── Session budget as an LRU ──


def test_budget_detaches_the_least_recently_used_idle_session(registry):
    """A 6th chat is no longer a wall: the coldest idle session is detached."""
    cap = session_module.MAX_SESSIONS_PER_USER

    async def scenario():
        infos = []
        for i in range(cap):
            infos.append(
                await runtime.create_session(
                    SessionSpec(
                        key=str(SessionKey.web(USER, f"slot{i}")),
                        agent_key="claude-code",
                        user_id=USER,
                    )
                )
            )
        # slot0 is the coldest; give the others a more recent prompt.
        for i in range(1, cap):
            await _chat(SessionKey.web(USER, f"slot{i}"), "ping")

        extra = await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(USER, "slot-new")),
                agent_key="claude-code",
                user_id=USER,
            )
        )
        return infos, extra

    infos, extra = asyncio.run(scenario())

    live = {i.key for i in asyncio.run(runtime.list_sessions(USER))}
    assert len(live) == cap, "still at the cap, not over it"
    assert str(SessionKey.web(USER, "slot-new")) in live
    assert str(SessionKey.web(USER, "slot0")) not in live, "the LRU was detached"
    # Detaching cost nothing: the detached chat is still there to come back to.
    assert get_conversation(USER, infos[0].conversation_id) is not None
    assert len(list_conversations(USER)) == cap + 1
    assert extra.conversation_id


def test_budget_still_refuses_when_every_session_is_busy(registry):
    cap = session_module.MAX_SESSIONS_PER_USER

    async def scenario():
        for i in range(cap):
            await runtime.create_session(
                SessionSpec(
                    key=str(SessionKey.web(USER, f"slot{i}")),
                    agent_key="claude-code",
                    user_id=USER,
                )
            )
        for session in session_module._sessions.values():
            session.is_busy = True

        with pytest.raises(session_module.SessionLimitReached) as exc:
            await runtime.create_session(
                SessionSpec(
                    key=str(SessionKey.web(USER, "slot-new")),
                    agent_key="claude-code",
                    user_id=USER,
                )
            )
        return str(exc.value)

    assert "busy" in asyncio.run(scenario())


# ── The LRU does not cross surfaces by surprise (CORR-227) ──


class _RecordingBot:
    """Stands in for the health monitor's Bot; records what it was told."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


async def _fill_with_one_telegram_session(web_slots: int) -> None:
    """One (coldest) Telegram session plus ``web_slots`` warmer web ones."""
    await runtime.create_session(
        SessionSpec(
            key=str(SessionKey.telegram(USER)),
            agent_key="claude-code",
            chat_id=USER,
            user_id=USER,
        )
    )
    for i in range(web_slots):
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(USER, f"slot{i}")),
                agent_key="claude-code",
                user_id=USER,
            )
        )
        await _chat(SessionKey.web(USER, f"slot{i}"), "ping")


def test_budget_prefers_a_victim_on_the_incoming_surface(registry):
    """A new web tab detaches a web session, not the older Telegram chat.

    The Telegram session here is the coldest of all — a global LRU would take
    it, and the user would never see why: the tab bar shows only web.
    """
    cap = session_module.MAX_SESSIONS_PER_USER

    async def scenario():
        await _fill_with_one_telegram_session(cap - 1)
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(USER, "slot-new")),
                agent_key="claude-code",
                user_id=USER,
            )
        )

    asyncio.run(scenario())

    live = {i.key for i in asyncio.run(runtime.list_sessions(USER))}
    assert len(live) == cap, "still at the cap, not over it"
    assert str(SessionKey.web(USER, "slot-new")) in live
    assert str(SessionKey.telegram(USER)) in live, "the unseen chat survived"
    assert str(SessionKey.web(USER, "slot0")) not in live, "the coldest web tab went"


def test_budget_crosses_surfaces_only_when_this_one_has_nothing_idle(
    registry, monkeypatch
):
    """The cap is still a cap: with every web session busy, Telegram pays."""
    cap = session_module.MAX_SESSIONS_PER_USER
    bot = _RecordingBot()
    monkeypatch.setattr(session_module, "_health_bot", bot)

    async def scenario():
        await _fill_with_one_telegram_session(cap - 1)
        for session in session_module._sessions.values():
            session.is_busy = session.key.surface == WEB

        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(USER, "slot-new")),
                agent_key="claude-code",
                user_id=USER,
            )
        )

    asyncio.run(scenario())

    live = {i.key for i in asyncio.run(runtime.list_sessions(USER))}
    assert len(live) == cap, "the cap is never breached to spare a surface"
    assert str(SessionKey.web(USER, "slot-new")) in live
    assert str(SessionKey.telegram(USER)) not in live, "the only idle one was taken"

    assert len(bot.sent) == 1, "the chat that lost its session was told"
    chat_id, text = bot.sent[0]
    assert chat_id == USER
    assert "detached" in text.lower()
    assert "unexpectedly" not in text.lower(), "a detach is not the death notice"


def test_a_same_surface_detach_stays_silent(registry, monkeypatch):
    """No cross-surface surprise, no notice — the tab bar already showed it."""
    bot = _RecordingBot()
    monkeypatch.setattr(session_module, "_health_bot", bot)
    cap = session_module.MAX_SESSIONS_PER_USER

    async def scenario():
        await _fill_with_one_telegram_session(cap - 1)
        await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(USER, "slot-new")),
                agent_key="claude-code",
                user_id=USER,
            )
        )

    asyncio.run(scenario())

    assert bot.sent == []


# ── Bounded tail reads (PERF-138) ──


def _replay_by_full_parse(user_id, conv_id, *, max_chars=None):
    """The pre-PERF-138 replay: parse the whole file, then walk it backwards.

    Kept verbatim as the oracle for the bounded reader — the point of the
    change was cost, not output, so the two must agree line for line.
    """
    max_chars = REPLAY_MAX_CHARS if max_chars is None else max_chars
    turns = read_transcript(user_id, conv_id, limit=0)
    if not turns:
        return ""
    overhead = len(REPLAY_HEADER) + 1 + len(REPLAY_OMITTED) + 1
    budget = max_chars - overhead
    if budget <= 0:
        return REPLAY_HEADER[:max_chars]

    lines, used, omitted = [], 0, False
    for turn in reversed(turns):
        rendered = _render_turn(turn)
        if not rendered:
            continue
        if used + len(rendered) + 1 > budget:
            omitted = True
            break
        lines.append(rendered)
        used += len(rendered) + 1

    if not lines:
        newest = next((r for r in (_render_turn(t) for t in reversed(turns)) if r), "")
        if not newest:
            return ""
        lines = [newest[:budget]]
        omitted = True

    lines.reverse()
    parts = [REPLAY_HEADER, *lines]
    if omitted:
        parts.append(REPLAY_OMITTED)
    return "\n".join(parts)[:max_chars]


def _write_raw_transcript(conv_root, conv_id, lines):
    """Write transcript lines straight to disk, bypassing ``append_turn``."""
    path = conv_root / str(USER) / "conversations" / conv_id / "transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _turn_line(**fields):
    return json.dumps(TurnEntry(**fields).model_dump(mode="json")) + "\n"


def test_reverse_line_reader_matches_a_forward_read(conv_root):
    """Backwards blocks yield exactly the forward lines, reversed.

    Tiny blocks on purpose: every line here straddles a block boundary, which
    is the only way the reader can lose or splice one.
    """
    meta = new_conversation(USER, WEB)
    raw = ["one\n", "two\n", "\n", "three " + "x" * 500 + "\n", "four"]
    path = _write_raw_transcript(conv_root, meta.id, raw)

    forward = [line.strip() for line in path.read_bytes().split(b"\n") if line.strip()]
    for block in (1, 7, 64, 4096):
        assert (
            list(_iter_lines_reverse(path, block=block)) == forward[::-1]
        ), f"block={block} changed the line sequence"


def test_replay_matches_the_full_parse_on_every_transcript_shape(conv_root):
    """Same records, same order, same truncation boundary as the old path."""
    shapes = {
        "empty file": [],
        "one turn": [_turn_line(role="user", text="hi")],
        "short": [
            _turn_line(role="user", text="what is my pnl?"),
            _turn_line(role="assistant", text="Up $120."),
        ],
        "empty renders interleaved": [
            _turn_line(role="assistant", text="", tool_calls=[]),
            _turn_line(role="user", text="hello"),
            _turn_line(role="system", text=""),
            _turn_line(role="assistant", text="", tool_calls=[]),
        ],
        "malformed lines inside the tail": [
            _turn_line(role="user", text="kept"),
            "{not json at all\n",
            _turn_line(role="assistant", text="also kept"),
            '{"role": 12345}\n',
        ],
        "no trailing newline": [_turn_line(role="user", text="torn").rstrip("\n")],
        "torn last line": [
            _turn_line(role="user", text="whole"),
            '{"role": "assistant", "text": "half a li',
        ],
        "long": [
            _turn_line(role="user", text=f"message number {i} " + "x" * 100)
            for i in range(300)
        ],
        "long with tool calls": [
            _turn_line(
                role="assistant",
                text=f"turn {i}",
                tool_calls=[{"id": f"t{i}", "title": "get_market_data"}],
            )
            for i in range(300)
        ],
        "one turn far over the bound": [_turn_line(role="user", text="y" * 5000)],
    }

    for name, raw in shapes.items():
        meta = new_conversation(USER, WEB)
        _write_raw_transcript(conv_root, meta.id, raw)
        for max_chars in (10, 40, 200, 1000, REPLAY_MAX_CHARS):
            assert replay_context(USER, meta.id, max_chars=max_chars) == (
                _replay_by_full_parse(USER, meta.id, max_chars=max_chars)
            ), f"{name} diverged from the full parse at max_chars={max_chars}"


def _count_parsed_turns(monkeypatch):
    """Count every ``TurnEntry`` the store builds while reading."""
    real = conversations.TurnEntry
    counter = {"n": 0}

    def counting(**fields):
        counter["n"] += 1
        return real(**fields)

    monkeypatch.setattr(conversations, "TurnEntry", counting)
    return counter


def test_replay_parses_a_bounded_tail_not_the_whole_transcript(conv_root, monkeypatch):
    """The complexity pin: replay cost follows its char budget, not the file.

    10k turns, ~200 chars each — the old path built 10k ``TurnEntry`` objects
    to render a 1000-char tail. The bound here is the budget itself: no more
    turns can be parsed than can fit in it, plus the one that overruns it.
    """
    meta = new_conversation(USER, WEB)
    _write_raw_transcript(
        conv_root,
        meta.id,
        [
            _turn_line(role="user", text=f"message number {i} " + "x" * 200)
            for i in range(10_000)
        ],
    )

    counter = _count_parsed_turns(monkeypatch)
    replay = replay_context(USER, meta.id, max_chars=1000)

    assert "message number 9999" in replay
    assert REPLAY_OMITTED in replay
    assert counter["n"] <= 1000 // 200 + 2, (
        "replay parsed more turns than its char budget can hold: "
        f"{counter['n']} for a 1000-char tail of a 10k-turn transcript"
    )


def test_read_transcript_tail_parses_only_the_tail(conv_root, monkeypatch):
    """The web route's ``limit=200`` over 10k turns parses 200, not 10k."""
    meta = new_conversation(USER, WEB)
    _write_raw_transcript(
        conv_root,
        meta.id,
        [_turn_line(role="user", text=f"m{i}") for i in range(10_000)],
    )

    counter = _count_parsed_turns(monkeypatch)
    tail = read_transcript(USER, meta.id, limit=200)

    assert [t.text for t in tail] == [f"m{i}" for i in range(9800, 10_000)]
    assert counter["n"] == 200, "a bounded read must not touch the older turns"


def test_read_transcript_tail_skips_malformed_lines_like_the_full_read(conv_root):
    """Tolerance is unchanged: a bad line costs its own slot, not the read."""
    meta = new_conversation(USER, WEB)
    _write_raw_transcript(
        conv_root,
        meta.id,
        [
            _turn_line(role="user", text="oldest"),
            "not json\n",
            _turn_line(role="assistant", text="middle"),
            '{"role": "user", "text": {"bad": "type"}}\n',
            _turn_line(role="user", text="newest"),
        ],
    )

    assert [t.text for t in read_transcript(USER, meta.id, limit=2)] == [
        "middle",
        "newest",
    ]
    assert [t.text for t in read_transcript(USER, meta.id, limit=0)] == [
        "oldest",
        "middle",
        "newest",
    ]
