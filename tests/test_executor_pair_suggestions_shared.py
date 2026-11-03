"""Tests for READ-258: one pair-suggestions screen, two wizards.

The grid and position wizards each carried their own 57-line copy of
``_show_pair_suggestions``, identical apart from the step title and the two
callback-data strings, plus two parameters (``input_pair``, ``connector``)
that neither body ever read. The helper now lives once in ``_shared.py`` and
takes that variation as keyword arguments.

What must not regress is the wiring: each wizard has to keep sending the user
to *its own* pair-select and Back callbacks, because the executors callback
router dispatches on exactly those strings.
"""

import asyncio
from types import SimpleNamespace

import pytest

from handlers.executors import grid, position
from handlers.executors._shared import _show_pair_suggestions


class _Message:
    """Captures the single ``edit_text`` the helper performs."""

    def __init__(self):
        self.text = None
        self.reply_markup = None

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup


def _render(error_msg, suggestions, **wiring):
    message = _Message()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=1),
        callback_query=SimpleNamespace(message=message),
    )
    context = SimpleNamespace(user_data={})
    asyncio.run(
        _show_pair_suggestions(update, context, error_msg, suggestions, **wiring)
    )
    return message


def _callbacks(message):
    return [
        b.callback_data for row in message.reply_markup.inline_keyboard for b in row
    ]


GRID_WIRING = {
    "title": "📐 *Grid Executor \\- Step 1/2*",
    "select_prefix": "executors:grid_pair_select:",
    "back_callback": "executors:create_grid",
}
POSITION_WIRING = {
    "title": "🎯 *Position Executor \\- Step 1/2*",
    "select_prefix": "executors:pos_pair_select:",
    "back_callback": "executors:create_position",
}


def test_there_is_exactly_one_definition_left():
    """Both wizards must resolve to the same function object, not to copies."""
    assert grid._show_pair_suggestions is _show_pair_suggestions
    assert position._show_pair_suggestions is _show_pair_suggestions


@pytest.mark.parametrize(
    "wiring, prefix, back",
    [
        pytest.param(
            GRID_WIRING,
            "executors:grid_pair_select:",
            "executors:create_grid",
            id="grid",
        ),
        pytest.param(
            POSITION_WIRING,
            "executors:pos_pair_select:",
            "executors:create_position",
            id="position",
        ),
    ],
)
def test_each_wizard_keeps_its_own_callback_data(wiring, prefix, back):
    message = _render("BAD-PAIR not found", ["BTC-USDT", "ETH-USDT"], **wiring)

    assert _callbacks(message) == [
        f"{prefix}BTC-USDT",
        f"{prefix}ETH-USDT",
        back,
        "executors:menu",
    ]


def test_the_title_comes_from_the_caller():
    grid_msg = _render("nope", [], **GRID_WIRING)
    position_msg = _render("nope", [], **POSITION_WIRING)

    assert grid_msg.text.startswith("📐 *Grid Executor \\- Step 1/2*\n")
    assert position_msg.text.startswith("🎯 *Position Executor \\- Step 1/2*\n")


def test_the_error_message_is_escaped_for_markdown_v2():
    """The user's error text is interpolated into a MarkdownV2 message."""
    message = _render("BTC-USDT.PERP is not listed (yet)", [], **GRID_WIRING)

    assert "BTC\\-USDT\\.PERP is not listed \\(yet\\)" in message.text


def test_no_suggestions_still_offers_a_way_out():
    """Empty suggestions must not leave the user on a dead-end screen."""
    message = _render("nothing close", [], **POSITION_WIRING)

    assert "_No similar pairs found\\._" in message.text
    assert _callbacks(message) == ["executors:create_position", "executors:menu"]
