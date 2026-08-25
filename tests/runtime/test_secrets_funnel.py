"""A pasted key never reaches the transcript (FEAT-056) — the funnel half.

``tests/runtime/test_secrets.py`` pins the detector. This file pins the
property the detector exists for, and it pins it at
:func:`condor.runtime.client.prompt` rather than at a surface: one variable
there feeds both the model and the disk, so proving it once proves it for
Telegram, the dashboard, background wakes, delegations and MCP together.

The assertion that matters most is the one about ``prompt_stream``. A clean
transcript with a plaintext phrase already in the provider's request log is the
false assurance this design rejected (alternative A in the doc).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from condor import paths
from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.telemetry import taps

SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"
TX_HASH = "0x" + "9f" * 32


class _CapturingClient:
    """Client that records what the model was actually asked."""

    seen: list[str] = []

    def __init__(self, **kwargs):
        self.alive = True

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "summary"

    async def prompt_stream(self, text):
        type(self).seen.append(text)
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def registry(monkeypatch):
    _CapturingClient.seen = []
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _CapturingClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


def _turn(text: str, *, user_id: int = 77) -> str:
    """Run one turn through the funnel; return the conversation id."""
    key = SessionKey.telegram(user_id)

    async def scenario():
        info = await runtime.create_session(
            session_module.SessionSpec(
                key=str(key),
                agent_key="claude-code",
                chat_id=user_id,
                user_id=user_id,
            )
        )
        async for _ in runtime.prompt(key, PromptRequest(text=text)):
            pass
        await runtime.destroy(key)
        return info.conversation_id

    conv_id = asyncio.run(scenario())
    conversations.flush_all()
    return conv_id


def _files(user_id: int, conv_id: str) -> str:
    """Every byte this conversation left on disk."""
    conv_dir = paths.users_root() / str(user_id) / "conversations" / conv_id
    return "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(conv_dir.rglob("*"))
        if path.is_file()
    )


def test_a_mnemonic_reaches_neither_the_model_nor_the_disk(registry):
    conv_id = _turn(f"please import {SEED} for me")

    # The provider half. The transcript being clean is worth nothing if the
    # plaintext already went out over the wire.
    assert _CapturingClient.seen == ["please import [redacted: mnemonic] for me"]

    # The disk half — transcript, archive, meta title and snippet alike.
    written = _files(77, conv_id)
    assert "sausage" not in written
    assert "[redacted: mnemonic]" in written

    meta = conversations.get_conversation(77, conv_id)
    assert meta is not None
    assert "sausage" not in (meta.title or "")
    assert "sausage" not in (meta.last_snippet or "")


def test_the_recorded_turn_is_the_redacted_one(registry):
    conv_id = _turn(SEED)
    turns = conversations.read_transcript(77, conv_id, include_archive=True)
    user_turns = [turn for turn in turns if turn.role == "user"]
    assert user_turns and all("sausage" not in (turn.text or "") for turn in user_turns)


def test_a_transaction_hash_crosses_the_funnel_byte_identical(registry):
    """The accepted trade-off, asserted so nobody quietly "fixes" it into a
    redaction: checking a tx is the most routine thing anyone asks this bot."""
    text = f"did {TX_HASH} land?"
    conv_id = _turn(text)
    assert _CapturingClient.seen == [text]
    assert TX_HASH in _files(77, conv_id)


def test_ordinary_text_is_untouched(registry):
    text = "how is SOL-USDC doing today?"
    _turn(text)
    assert _CapturingClient.seen == [text]


def test_telemetry_counts_findings_and_carries_no_value(registry, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        taps, "emit", lambda name, **props: events.append({"name": name, **props})
    )

    _turn(f"{SEED} and {TX_HASH}")

    turn = next(event for event in events if event["name"] == "agent_turn")
    assert turn["secrets"] == {"mnemonic": 1, "evm-hex64": 1}
    assert "sausage" not in json.dumps(turn)
