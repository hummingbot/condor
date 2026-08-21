"""A routine's own text can never break the message that carries it.

The result string is uncontrolled — arbitrary routine output, or the text of the
exception that killed the run, and those quote values in backticks. Interpolated
raw into a MarkdownV2 ``` fence it closes the entity early, Telegram rejects the
whole message, and the send sits inside a bare `except` that only logs: the
scheduled routine goes quiet forever while the job keeps firing.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import handlers.routines as hr
from utils.telegram_formatters import escape_markdown_v2_code

# Backtick (closes the fence), backslash (eats the next char), and a few chars
# that are reserved outside a fence but literal inside one.
HOSTILE = "Error: field `size` must be > 0 (path C:\\tmp\\out.json)"

RESERVED = "[]()~>#+-=|{}.!"


def render_markdown_v2(text: str) -> str:
    """Minimal stand-in for Telegram's MarkdownV2 parser.

    Raises like the Bot API does on an unparseable message, and otherwise returns
    the text the user actually sees, so a test can assert both.
    """
    out: list[str] = []
    stack: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if text.startswith("```", i):
            if stack and stack[-1] == "pre":
                stack.pop()
            else:
                stack.append("pre")
            i += 3
            continue
        c = text[i]
        if stack and stack[-1] in ("pre", "code"):
            # Only ` and \ are special in here; everything else is literal.
            if c == "`":
                if stack[-1] == "code":
                    stack.pop()
                    i += 1
                    continue
                raise ValueError("Can't parse entities: can't find end of Pre entity")
            out.append(c)
            i += 1
            continue
        if c in "*_":
            if stack and stack[-1] == c:
                stack.pop()
            else:
                stack.append(c)
        elif c == "`":
            stack.append("code")
        elif c in RESERVED:
            raise ValueError(
                f"Can't parse entities: character '{c}' is reserved "
                f"and must be escaped at byte offset {i}"
            )
        else:
            out.append(c)
        i += 1
    if stack:
        raise ValueError("Can't parse entities: can't find end of the entity")
    return "".join(out)


class FakeBot:
    """Rejects an unparseable message the way Telegram does, instead of logging it."""

    def __init__(self):
        self.rendered: list[str] = []

    async def _accept(self, text, parse_mode=None, **kwargs):
        assert parse_mode == "MarkdownV2"
        self.rendered.append(render_markdown_v2(text))

    async def send_message(self, chat_id=None, text="", parse_mode=None, **kwargs):
        await self._accept(text, parse_mode)

    async def edit_message_text(self, chat_id=None, text="", parse_mode=None, **kwargs):
        await self._accept(text, parse_mode)


class Config(BaseModel):
    pass


def _routine():
    return SimpleNamespace(
        name="probe",
        description="Probe routine",
        is_continuous=False,
        get_fields=lambda: {},
        get_default_config=lambda: Config(),
    )


@pytest.fixture
def ctx(monkeypatch):
    """A job context whose routine returns HOSTILE, with the store stubbed out."""

    async def _execute(*args, **kwargs):
        return HOSTILE, 1.5, None

    monkeypatch.setattr(hr, "_execute_routine", _execute)
    monkeypatch.setattr(hr, "get_routine_store", lambda: SimpleNamespace())
    monkeypatch.setattr(hr, "get_routine", lambda name: _routine())

    chat_id = 42
    instances = {
        "i1": {
            "routine_name": "probe",
            "status": "running",
            "schedule": {"type": "interval", "interval_sec": 60},
        }
    }
    buckets = {chat_id: {"routine_instances": instances}}
    return SimpleNamespace(
        bot=FakeBot(),
        # ``condor.scheduler.JobContext.owner_data``: buckets keyed by owner,
        # falling back to the chat for a payload that carries no ``user_id``.
        owner_data=lambda owner_id, cid: buckets.get(
            owner_id if owner_id is not None else cid, {}
        ),
        job=SimpleNamespace(
            data={
                "instance_id": "i1",
                "routine_name": "probe",
                "config_dict": {},
                "chat_id": chat_id,
                "background": True,
            }
        ),
    )


def _delivered(ctx) -> str:
    assert len(ctx.bot.rendered) == 1, "the result message was never delivered"
    return ctx.bot.rendered[0]


def test_interval_run_delivers_the_result(ctx):
    asyncio.run(hr._interval_job_callback(ctx))
    assert HOSTILE in _delivered(ctx)


def test_background_one_shot_delivers_the_result(ctx):
    asyncio.run(hr._oneshot_job_callback(ctx))
    assert HOSTILE in _delivered(ctx)


def test_daily_run_delivers_the_result(ctx):
    asyncio.run(hr._daily_job_callback(ctx))
    assert HOSTILE in _delivered(ctx)


def test_detail_view_refresh_delivers_the_result(ctx):
    asyncio.run(hr._refresh_detail_msg(ctx, 42, 7, "probe", HOSTILE, 1.5))
    assert HOSTILE in _delivered(ctx)


def test_the_user_sees_the_original_characters(ctx):
    """Escaping is a wire detail — it must not leak into what is rendered."""
    asyncio.run(hr._daily_job_callback(ctx))
    assert "\\`" not in _delivered(ctx)


def test_truncation_happens_before_escaping():
    """A cut after escaping could split a `\\` pair and orphan the escape."""
    escaped = escape_markdown_v2_code("\\" * 400)
    assert len(escaped) == 800
    assert render_markdown_v2(f"```\n{escaped}\n```") == "\n" + "\\" * 400 + "\n"
