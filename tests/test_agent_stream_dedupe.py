"""The streamer only edits what actually changed (PERF-092).

``_flush`` rebuilds the whole answer and rewrites every chunk twice a second.
Once an answer passes 4096 chars the split point stops moving, so chunk 0 and
every continuation but the last are byte-identical on each tick — edits that
Telegram rejects as "not modified" while still spending the per-chat rate
limit the *changed* chunk is waiting for.
"""

import asyncio
from types import SimpleNamespace

from telegram.error import BadRequest

from condor.runtime.events import EventType, RuntimeEvent
from handlers.agents.stream import (
    MAX_MESSAGE_LEN,
    TelegramStreamer,
    _mdv2_to_plain,
    _to_telegram_markdown,
)

CHAT_ID = 4242
MAIN_ID = 1


class _RecordingBot:
    """Records every call that actually reached the Bot API."""

    def __init__(self, fail_parse_mode: bool = False):
        self.edits: list[tuple[int, str, str | None]] = []
        self.sends: list[str] = []
        self._fail_parse_mode = fail_parse_mode
        self._next_id = 100

    async def edit_message_text(self, **kw):
        if self._fail_parse_mode and kw.get("parse_mode"):
            raise BadRequest("Can't parse entities: unmatched '_'")
        self.edits.append((kw["message_id"], kw["text"], kw.get("parse_mode")))
        return None

    async def send_message(self, **kw):
        self.sends.append(kw["text"])
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)


def _streamer(**kw):
    bot = _RecordingBot(**kw)
    return bot, TelegramStreamer(bot=bot, chat_id=CHAT_ID, message_id=MAIN_ID)


def _paragraphs(n: int) -> str:
    """Text that splits at stable paragraph boundaries."""
    return "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(n))


async def _feed(streamer: TelegramStreamer, text: str) -> None:
    await streamer.process_event(RuntimeEvent(type=EventType.TEXT, data={"text": text}))


def test_a_long_answer_only_re_edits_the_chunk_that_grew():
    """The whole point: 3 messages, 1 edit per tick — not 3."""
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, _paragraphs(45))  # > 8000 chars -> 3 messages
        await streamer._flush(final=False)
        opening = len(bot.edits)
        for _ in range(4):
            await _feed(streamer, "\n\nand a little more")
            await streamer._flush(final=False)
        return opening

    opening = asyncio.run(drive())

    assert len(streamer._continuation_ids) == 2, "expected a 3-message answer"
    # First flush edits the placeholder and sends the two continuations.
    assert opening == 1
    tail_id = streamer._continuation_ids[-1]
    # Four more ticks, four edits — all of them to the message that grew.
    assert [mid for mid, _, _ in bot.edits[opening:]] == [tail_id] * 4


def test_an_unchanged_screen_is_not_re_edited():
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, "the whole answer")
        for _ in range(5):
            await streamer._flush(final=False)

    asyncio.run(drive())

    assert [text for _, text, _ in bot.edits] == ["the whole answer"]


def test_finalize_re_sends_identical_text_as_markdown():
    """The formatting pass must land even when the characters don't change."""
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, "plain answer with no markup")
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    assert bot.edits == [
        (MAIN_ID, "plain answer with no markup", None),
        (MAIN_ID, "plain answer with no markup", "MarkdownV2"),
    ]


def test_finalize_reformats_every_chunk_of_a_long_answer():
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, _paragraphs(45))
        await streamer._flush(final=False)
        opening = len(bot.edits)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()
        return opening

    opening = asyncio.run(drive())

    ids = [MAIN_ID] + streamer._continuation_ids
    final_edits = bot.edits[opening:]
    assert [mid for mid, _, _ in final_edits] == ids
    assert all(mode == "MarkdownV2" for _, _, mode in final_edits)


def test_the_thinking_pulse_still_animates_every_tick():
    bot, streamer = _streamer()

    async def drive():
        for tick in range(3):
            streamer._tick = tick
            await streamer._flush(final=False)

    asyncio.run(drive())

    texts = [text for _, text, _ in bot.edits]
    assert texts == ["Thinking.", "Thinking..", "Thinking..."]


def test_a_parse_failure_still_retries_without_parse_mode():
    """The fallback must send readable text, not the MarkdownV2 escapes."""
    bot, streamer = _streamer(fail_parse_mode=True)

    async def drive():
        await _feed(streamer, "answer with a stray _ underscore")
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    # Streaming edit, then the MarkdownV2 attempt fails and comes back as plain.
    # The retry is unescaped: sending the raw `\_` would show a backslash.
    plain = "answer with a stray _ underscore"
    assert bot.edits == [(MAIN_ID, plain, None), (MAIN_ID, plain, None)]


def test_a_not_modified_rejection_is_remembered():
    """Telegram saying "already there" is the strongest possible cache fill."""
    bot, streamer = _streamer()
    calls = []

    async def edit(**kw):
        calls.append(kw["text"])
        raise BadRequest("Message is not modified")

    bot.edit_message_text = edit

    async def drive():
        await _feed(streamer, "same")
        await streamer._flush(final=False)
        await streamer._flush(final=False)

    asyncio.run(drive())

    assert calls == ["same"]


def test_a_failed_edit_is_retried_on_the_next_tick():
    """Nothing reached the screen, so nothing should be cached."""
    bot, streamer = _streamer()
    attempts = []

    async def edit(**kw):
        attempts.append(kw["text"])
        raise BadRequest("Bad Request: message to edit not found")

    bot.edit_message_text = edit

    async def drive():
        await _feed(streamer, "same")
        await streamer._flush(final=False)
        await streamer._flush(final=False)

    asyncio.run(drive())

    assert attempts == ["same", "same"]


def test_final_tool_block_is_escaped_for_markdown_v2():
    """The finished-tool line rides in the final MarkdownV2 message, so its
    parens must be escaped or the whole message fails to parse and falls back
    to plain text, showing raw markdown (the link bug)."""
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(
                type=EventType.TOOL_CALL,
                data={
                    "tool_call_id": "tc1",
                    "title": "Routine solana_dex_lp_expert/solana_pool_scanner_adaptive_band done",
                    "status": "running",
                    "kind": "routine",
                    "input": "",
                },
            )
        )
        await streamer.process_event(
            RuntimeEvent(
                type=EventType.TOOL_UPDATE,
                data={
                    "tool_call_id": "tc1",
                    "status": "completed",
                    "title": "Routine solana_dex_lp_expert/solana_pool_scanner_adaptive_band done",
                    "output": "",
                },
            )
        )
        await _feed(
            streamer,
            "Scan terminé — **1 match** :\n\n1. [META / SOL](https://x.com/a) (Raydium Clmm)",
        )
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    final_text = bot.edits[-1][1]
    assert final_text.startswith(
        "✅ Routine solana dex lp expert/solana pool scanner adaptive band done \\("
    )
    assert "\\(" in final_text.split("\n\n")[0]
    assert "Scan terminé — *1 match*" in final_text


def test_chunking_assumption_holds():
    """Guards the premise: the split point does not move as the answer grows."""
    from handlers.agents.stream import _split_text

    base = _paragraphs(45)
    first = _split_text(base, MAX_MESSAGE_LEN)
    grown = _split_text(base + "\n\nand a little more", MAX_MESSAGE_LEN)

    assert len(first) == 3
    assert grown[:-1] == first[:-1]


def test_plain_fallback_strips_escapes_and_entity_markers():
    """A rejected answer must not reach the user as a wall of backslashes."""
    source = "**Rapport LP** — SOL à $103.95 (~$188) · uPnL **+4.16 %**"
    escaped = _to_telegram_markdown(source)

    plain = _mdv2_to_plain(escaped)

    assert "\\" not in plain
    assert plain == "Rapport LP — SOL à $103.95 (~$188) · uPnL +4.16 %"


def test_plain_fallback_keeps_code_and_backslashes_verbatim():
    """Stripping markers is only safe outside code: a `*` the user typed in a
    span is content, and a literal backslash must survive the round trip."""
    source = "Use `a*b_c` and C:\\tmp\\out"
    assert _mdv2_to_plain(_to_telegram_markdown(source)) == source


def test_a_heading_is_escaped_once():
    """The header pass produced `*...*`, then the emphasis pass escaped the
    backslashes it had just added a second time."""
    assert _to_telegram_markdown("# Report (v2)") == r"*Report \(v2\)*"


def test_a_link_label_is_escaped():
    assert (
        _to_telegram_markdown("[SOL_USDC pool](https://x.com/a)")
        == r"[SOL\_USDC pool](https://x.com/a)"
    )


def test_code_keeps_its_backslashes():
    assert _to_telegram_markdown("`C:\\tmp`") == "`C:\\\\tmp`"


def test_a_rate_limited_send_retries_with_the_parse_mode():
    """Dropping parse_mode on retry would show `*bold*` and `\\.` literally."""
    from telegram.error import RetryAfter

    calls = []

    class Bot:
        async def send_message(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise RetryAfter(0)
            return SimpleNamespace(message_id=7)

    streamer = TelegramStreamer(bot=Bot(), chat_id=CHAT_ID, message_id=MAIN_ID)
    asyncio.run(streamer._send("*hi*", "MarkdownV2"))

    assert calls[-1]["parse_mode"] == "MarkdownV2"
    assert calls[-1]["text"] == "*hi*"


def test_an_empty_answer_is_sent_with_markdown():
    """`_\\(no response\\)_` sent as plain text shows its escapes."""
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    assert bot.edits[-1] == (MAIN_ID, r"_\(no response\)_", "MarkdownV2")


def test_a_split_never_severs_an_escape():
    from handlers.agents.stream import _split_text

    text = "a" * 10 + "\\" + "b" * 20
    chunks = _split_text(text, 11)
    assert chunks[0] == "a" * 10
    assert chunks[1].startswith("\\b")

    # The pair is intact, so the cut is fine where it was.
    text2 = "a" * 10 + "\\\\" + "b" * 20
    assert _split_text(text2, 12)[0] == "a" * 10 + "\\\\"
