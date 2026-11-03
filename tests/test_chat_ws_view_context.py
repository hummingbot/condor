"""Page context rides a side channel, never the transcript (FEAT-059).

The dashboard tells the agent what the user is looking at by sending a
``view_context`` string beside the message. The funnel prepends it to that one
prompt and records only the user's words — the block is true of a moment, not
of the conversation, so replaying it on resume (or showing it in a shared
conversation) would state a page the user left long ago. That is exactly what
the old hack did: it stapled a ``[System: …viewing the report file…]`` sentence
onto ``text``, and the Recorder wrote the whole thing as the user's words.

The cap is load-bearing: ``view_context`` is a client-supplied string on a
prompt, so an unbounded one is an unbounded turn.
"""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.conversations import read_transcript
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import (
    VIEW_CONTEXT_MAX_CHARS,
    _handle_send_message,
    _handle_start_session,
)

USER = 913
VIEW = (
    "[What the user is looking at right now, in the Condor dashboard. True of "
    "this moment only — do not treat it as something the user said.]\n"
    "Screen: Bot detail\nAbout: bot id 42\nURL: /bots/42"
)
SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]


class _Client:
    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text):
        self.prompts.append(text)
        yield TextChunk(text="the answer")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(monkeypatch):
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _Client)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    # This file asserts the opening turn verbatim, so the conversation's
    # attribution block is blanked alongside the context builder above — it
    # is covered on its own in tests/runtime/test_conversation_attribution.py
    monkeypatch.setattr(session_module, "conversation_attribution", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws, "_secret_notices_sent", {})
    return chat_ws


def _run(*frames: dict) -> tuple[_FakeWS, str]:
    async def scenario():
        ws = _FakeWS()
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        for frame in frames:
            await _handle_send_message(ws, USER, {"slot_id": slot_id, **frame})
        return ws, slot_id

    return asyncio.run(scenario())


def test_the_model_hears_the_page_and_the_transcript_keeps_the_words(ws_env):
    _, slot_id = _run({"text": "why is this losing money", "view_context": VIEW})

    # The model got the block above the question, on this one turn.
    assert _Client.last.prompts == [f"{VIEW}\n\nwhy is this losing money"]

    # The stored turn is only what the user said — this is what a resume
    # replays and what ShareConversation shows.
    turns = read_transcript(USER, slot_id)
    assert [t.text for t in turns] == ["why is this losing money", "the answer"]


def test_a_turn_without_context_is_byte_for_byte_unchanged(ws_env):
    _, slot_id = _run({"text": "how is SOL-USDC doing?"})
    assert _Client.last.prompts == ["how is SOL-USDC doing?"]
    turns = read_transcript(USER, slot_id)
    assert [t.text for t in turns] == ["how is SOL-USDC doing?", "the answer"]


def test_an_oversized_block_is_truncated_not_forwarded_whole(ws_env):
    _run({"text": "hi", "view_context": "x" * 5000})
    (prompt,) = _Client.last.prompts
    view_part = prompt.rsplit("\n\nhi", 1)[0]
    assert len(view_part) == VIEW_CONTEXT_MAX_CHARS
    assert view_part == "x" * VIEW_CONTEXT_MAX_CHARS


def test_a_key_rendered_by_a_page_is_redacted_like_typed_text(ws_env):
    # A page that renders a credential must not be the one hole in FEAT-056.
    ws, _ = _run({"text": "hi", "view_context": f"Screen: my {SEED}"})
    (prompt,) = _Client.last.prompts
    assert "sausage" not in prompt
    assert "[redacted: mnemonic]" in prompt
    # But the notice is about what the user *typed*, and they typed nothing
    # key-shaped — warning them about the dashboard's own render would be noise.
    assert ws.events("secret_notice") == []


def test_a_non_string_view_context_degrades_to_none(ws_env):
    _run({"text": "hi", "view_context": None})
    assert _Client.last.prompts == ["hi"]
