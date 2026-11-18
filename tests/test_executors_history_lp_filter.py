"""Executor history can be narrowed to the LP positions.

The history list is dominated by swap legs: an LP band opens, rebalances and
closes, and every leg of each swap lands in the same list, so LP positions are
about one row in five. The filter is a persisted view preference, not a query —
the API has no "type = lp" filter — so it narrows what was already fetched and
widens the fetch window to keep enough LP rows after filtering.

These pin the three things that can silently break: the window widening, the
filter actually restricting rows, and the preference surviving a re-render.
"""

import asyncio
from types import SimpleNamespace

import pytest

from handlers.executors import menu


class _Message:
    def __init__(self):
        self.texts = []
        self.buttons = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)
        markup = kwargs.get("reply_markup")
        self.buttons = (
            [b.text for row in markup.inline_keyboard for b in row] if markup else []
        )


class _Query:
    def __init__(self):
        self.message = _Message()


def _executor(kind, pair, pnl=1.0):
    return {
        "id": f"{kind}-{pair}",
        "status": "COMPLETED",
        "type": kind,
        "trading_pair": pair,
        "config": {"trading_pair": pair, "side": "BUY"},
        "net_pnl_quote": pnl,
    }


@pytest.fixture
def calls(monkeypatch):
    """A two-row history (one LP, one swap leg) and the fetch args recorded."""
    recorded = {}

    async def _client(chat_id, user_data):
        return None, "srv"

    async def _search(client, status=None, limit=None):
        recorded["status"] = status
        recorded["limit"] = limit
        return [_executor("lp_executor", "BAND-SOL"), _executor("order", "SWAP-SOL")]

    monkeypatch.setattr(menu, "get_executors_client", _client)
    monkeypatch.setattr(menu, "search_running_executors", _search)
    return recorded


def _render(user_data=None):
    query = _Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=1),
        message=None,
    )
    context = SimpleNamespace(user_data=user_data or {})
    asyncio.run(menu.show_history(update, context))
    return query.message


def test_the_default_view_keeps_every_executor_and_a_narrow_window(calls):
    message = _render()

    assert calls["limit"] == 100
    assert "BAND-SOL" in message.texts[-1] and "SWAP-SOL" in message.texts[-1]
    assert any("LP only" in b for b in message.buttons), "the toggle is offered"
    assert "_LP only_" not in message.texts[-1], "no filter claimed when none is active"


def test_lp_only_restricts_rows_and_widens_the_window(calls):
    message = _render({"executor_history_filter": "lp"})

    assert calls["limit"] == 300, "swap legs would eat the LP rows"
    assert "BAND-SOL" in message.texts[-1]
    assert "SWAP-SOL" not in message.texts[-1]
    assert "_LP only_" in message.texts[-1]
    assert any("Show all" in b for b in message.buttons), "the way back is offered"


def test_the_preference_survives_a_re_render(calls):
    user_data = {"executor_history_filter": "lp"}

    _render(user_data)
    _render(user_data)

    assert user_data["executor_history_filter"] == "lp"
    assert calls["limit"] == 300


def test_an_empty_lp_history_says_so(monkeypatch):
    async def _client(chat_id, user_data):
        return None, "srv"

    async def _swap_only(client, status=None, limit=None):
        return [_executor("order", "SWAP-SOL")]

    monkeypatch.setattr(menu, "get_executors_client", _client)
    monkeypatch.setattr(menu, "search_running_executors", _swap_only)

    message = _render({"executor_history_filter": "lp"})

    assert "No LP executor history found" in message.texts[-1]


def test_sort_key_falls_back_to_created_at():
    assert menu._history_sort_key({"created_at": 1_700_000_000}) == 1_700_000_000.0
    assert menu._history_sort_key({}) == 0.0
    # A zero timestamp must not beat a real created_at.
    assert menu._history_sort_key({"timestamp": 0, "created_at": 5}) == 5.0
