"""A loop's notices reach the dashboard bell as well as Telegram (ARCH-647).

``TickEngine._notify`` used to send straight to the resolved bot, so on any
install with Telegram a tick error, a risk block or an emergency-shutdown alert
never reached the bell. It now goes down ``announce``, which files the bell
entry once whichever rung of the sender ladder delivers.

Sync tests driving coroutines with ``asyncio.run`` (same convention as
``test_notifications.py``, whose fixtures model these).
"""

import asyncio
import inspect
import re

import pytest

from condor import notifications, paths
from condor.agents import engine as engine_module
from condor.notifications import list_for

OWNER = 111


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(notifications, "_push_sinks", [])
    return paths.notifications_path()


@pytest.fixture
def known_users(monkeypatch):
    class _CM:
        def get_user(self, user_id):
            return {"id": user_id} if user_id == OWNER else None

    monkeypatch.setattr("config_manager.get_config_manager", lambda: _CM())


def _engine():
    engine = engine_module.TickEngine.__new__(engine_module.TickEngine)
    engine.chat_id, engine.user_id, engine.agent_id = 0, OWNER, "scout.scalp_3"
    return engine


def test_notify_reaches_telegram_and_the_bell_once(store, known_users, monkeypatch):
    sent: list[dict] = []

    class _Telegram:
        async def send_message(self, **kw):
            sent.append(kw)
            return {"ok": True}

    monkeypatch.setattr(
        "condor.agents.delegate.resolve_bot", lambda bot=None: _Telegram()
    )

    msg = "Agent scout.scalp_3 blocked: max drawdown"
    asyncio.run(engine_module.TickEngine._notify(_engine(), msg))

    assert sent == [{"chat_id": OWNER, "text": msg}]
    items = list_for(OWNER)
    assert len(items) == 1
    assert items[0].text == msg
    assert items[0].kind == "agent"
    assert items[0].link == "/agents/scout"
    assert items[0].title == "Loop · scout"


def test_notify_over_the_bell_rung_is_filed_once(store, known_users, monkeypatch):
    """No Telegram at all: NotifyBot files the entry, announce must not re-file."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class _NoBotStore:
        def get_bot(self):
            return None

    monkeypatch.setattr("condor.routine_store.get_routine_store", lambda: _NoBotStore())

    asyncio.run(engine_module.TickEngine._notify(_engine(), "tick error"))

    items = list_for(OWNER)
    assert len(items) == 1
    assert items[0].link == "/agents/scout"


def test_notify_has_no_hand_rolled_sender():
    src = inspect.getsource(engine_module)
    # ``resolve_bot_name`` (the bot-naming helper) is unrelated.
    assert not re.search(r"\bresolve_bot\b", src)
    assert src.count("announce(") == 1
    assert "announce(" in inspect.getsource(engine_module.TickEngine._notify)
