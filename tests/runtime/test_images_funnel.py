"""A picture reaches the model as a picture (FEAT-098) — the funnel half.

``tests/test_attachments_store.py`` pins where the bytes live. This pins what
the *model* is handed, at :func:`condor.runtime.client.prompt`, because that one
funnel is what every surface crosses: proving the content blocks here proves them
for the dashboard, for MCP, and for whichever surface learns to attach next.

Three properties, in the order they matter:

* a text-only turn is byte-for-byte the call it always was — no empty list, no
  keyword, nothing for a client double to have to learn;
* the image goes *beside* the text and ahead of it, never inside it;
* an agent that says it cannot see is told so before the prompt is built,
  rather than by a protocol rejection nobody can read.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from condor import paths
from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import sessions as session_module
from condor.runtime.models import PromptImage

USER = 77
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _CapturingClient:
    """Records the exact call the funnel made, keyword and all."""

    calls: list[dict] = []
    accepts_images = True

    def __init__(self, **kwargs):
        self.alive = True

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text, **kwargs):
        type(self).calls.append({"text": text, **kwargs})
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def registry(monkeypatch):
    _CapturingClient.calls = []
    _CapturingClient.accepts_images = True
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _CapturingClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


def _turn(req: PromptRequest) -> list:
    key = SessionKey.telegram(USER)

    async def scenario():
        await runtime.create_session(
            session_module.SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        events = [event async for event in runtime.prompt(key, req)]
        await runtime.destroy(key)
        return events

    return asyncio.run(scenario())


def test_a_text_only_turn_calls_the_client_exactly_as_before(registry):
    """The regression that would be invisible: an extra keyword on every turn."""
    _turn(PromptRequest(text="how is SOL-USDC doing?"))
    assert _CapturingClient.calls == [{"text": "how is SOL-USDC doing?"}]


def test_an_image_travels_beside_the_text_not_inside_it(registry):
    _turn(
        PromptRequest(
            text="what is wrong with this chart?",
            images=[PromptImage(data=PNG, mime="image/png", id="a.png")],
        )
    )
    (call,) = _CapturingClient.calls
    assert call["text"] == "what is wrong with this chart?", "text stays a plain str"
    (image,) = call["images"]
    assert (image.data, image.mime) == (PNG, "image/png")


def test_a_turn_with_no_words_is_a_complete_message(registry):
    _turn(PromptRequest(text="", images=[PromptImage(data=PNG, mime="image/png")]))
    (call,) = _CapturingClient.calls
    assert call["text"] == ""
    assert len(call["images"]) == 1


def test_an_agent_that_cannot_see_is_told_so_before_the_prompt(registry):
    _CapturingClient.accepts_images = False
    events = _turn(
        PromptRequest(
            text="read this", images=[PromptImage(data=PNG, mime="image/png")]
        )
    )

    assert _CapturingClient.calls == [], "the turn must not reach the agent at all"
    error = next(e for e in events if e.type.value == "error")
    assert "cannot read images" in error.field("message")
    assert events[-1].stop_reason == "error"


def test_a_text_turn_is_unaffected_by_an_agent_that_cannot_see(registry):
    _CapturingClient.accepts_images = False
    _turn(PromptRequest(text="hello"))
    assert len(_CapturingClient.calls) == 1


# ── The two clients' own content blocks ──


def test_the_acp_prompt_array_puts_the_image_before_the_text():
    """Built here rather than driven through a subprocess: the shape *is* the
    contract, and a wire format is worth pinning as a literal."""
    from condor.acp import client as acp

    sent: list[dict] = []

    class _Stdin:
        def write(self, raw):
            sent.append(raw)

        async def drain(self):
            pass

    client = acp.ACPClient.__new__(acp.ACPClient)
    client._session_id = "s1"
    client._process = type("P", (), {"stdin": _Stdin()})()
    client._peer = acp.JSONRPCPeer()
    client._event_queue = asyncio.Queue()
    client._current_req_id = None

    async def scenario():
        async def _noop():
            return None

        client._settle_previous_turn = _noop
        client._drain_events = lambda: None
        agen = client.prompt_stream(
            "what is this?", images=[PromptImage(data=PNG, mime="image/png")]
        )
        # One step is enough: the request is written before the first await on
        # the queue, and pulling further would block on an answer nobody sends.
        task = asyncio.get_event_loop().create_task(agen.__anext__())
        await asyncio.sleep(0)
        task.cancel()

    asyncio.run(scenario())

    import json

    prompt = json.loads(sent[0])["params"]["prompt"]
    assert prompt == [
        {
            "type": "image",
            "data": base64.b64encode(PNG).decode("ascii"),
            "mimeType": "image/png",
        },
        {"type": "text", "text": "what is this?"},
    ]


def test_a_client_that_never_finished_its_handshake_is_never_offered_a_picture():
    """The default is the safe one: ``start()`` is the only thing that sets it."""
    from condor.acp import client as acp

    client = acp.ACPClient(command="true")
    assert client.accepts_images is False


def test_the_pydantic_ai_prompt_is_a_list_with_the_image_first():
    """Same ordering as ACP, in that library's own vocabulary.

    Driven against a stand-in agent rather than a provider: what is being pinned
    is the shape handed to ``agent.iter``, which is the whole of this client's
    half of the contract.
    """
    import contextlib

    from pydantic_ai.messages import BinaryContent

    from condor.acp import pydantic_ai_client as pac

    seen: list = []

    class _Run:
        def __aiter__(self):
            async def _empty():
                return
                yield  # pragma: no cover - an empty async iterator

            return _empty()

    class _Agent:
        @contextlib.asynccontextmanager
        async def iter(self, prompt, **kwargs):
            seen.append(prompt)
            yield _Run()

    client = pac.PydanticAIClient.__new__(pac.PydanticAIClient)
    client._agent = _Agent()
    client._request_semaphore = None
    client._abort_requested = False
    client._message_history = []
    client._permission_gate = type("G", (), {"reset": lambda self: None})()

    async def scenario():
        async for _ in client.prompt_stream(
            "read this", images=[PromptImage(data=PNG, mime="image/png")]
        ):
            pass
        async for _ in client.prompt_stream("and this?"):
            pass

    asyncio.run(scenario())

    assert seen[0] == [BinaryContent(data=PNG, media_type="image/png"), "read this"]
    assert seen[1] == "and this?", "a text-only turn stays a bare string"


def test_the_user_turn_records_the_reference_and_never_the_payload(registry):
    """What a reload reads back: an id, a type and a size. No bytes, no name."""
    from condor.runtime import conversations

    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            session_module.SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        async for _ in runtime.prompt(
            key,
            PromptRequest(
                text="what is wrong here?",
                images=[PromptImage(data=PNG, mime="image/png", id="9f8e7d.png")],
            ),
        ):
            pass
        await runtime.destroy(key)
        return info.conversation_id

    conv_id = asyncio.run(scenario())
    conversations.flush_all()

    turns = conversations.read_transcript(USER, conv_id)
    (user_turn,) = [turn for turn in turns if turn.role == "user"]
    assert user_turn.attachments == [
        {"id": "9f8e7d.png", "mime": "image/png", "bytes": len(PNG)}
    ]

    written = (paths.conversation_dir(USER, conv_id) / "transcript.jsonl").read_bytes()
    assert PNG not in written, "the transcript records the reference, not the picture"


def test_a_text_only_turn_records_no_attachments(registry):
    """The default reads as "not recorded", which is the growth contract."""
    from condor.runtime import conversations

    key = SessionKey.telegram(USER)

    async def scenario():
        info = await runtime.create_session(
            session_module.SessionSpec(
                key=str(key), agent_key="claude-code", chat_id=USER, user_id=USER
            )
        )
        async for _ in runtime.prompt(key, PromptRequest(text="hello")):
            pass
        await runtime.destroy(key)
        return info.conversation_id

    conv_id = asyncio.run(scenario())
    conversations.flush_all()
    (user_turn,) = [
        turn
        for turn in conversations.read_transcript(USER, conv_id)
        if turn.role == "user"
    ]
    assert user_turn.attachments == []
