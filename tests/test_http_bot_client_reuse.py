"""XC-12: ``_HttpBot`` sends every message through one ``httpx`` client.

It used to open a new ``httpx.AsyncClient`` per call — a fresh TLS handshake to
Telegram for each notification a web- or MCP-started routine sent.
"""

import asyncio

import httpx
import pytest

from condor.routine_store import _HttpBot


class FakeResponse:
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.posts = []
        self.is_closed = False
        FakeClient.instances.append(self)

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse()


@pytest.fixture
def bot(monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    return _HttpBot()


def test_two_sends_reuse_one_client(bot):
    async def scenario():
        await bot.send_message(chat_id=1, text="one")
        await bot.send_message(chat_id=1, text="two")
        await bot.send_photo(chat_id=1, photo=b"png")

    asyncio.run(scenario())

    assert len(FakeClient.instances) == 1
    client = FakeClient.instances[0]
    assert client.kwargs == {"timeout": 30}
    assert [url.rsplit("/", 1)[1] for url, _ in client.posts] == [
        "sendMessage",
        "sendMessage",
        "sendPhoto",
    ]


def test_a_new_event_loop_gets_a_new_client(bot):
    """A client is bound to the loop it first ran on."""
    asyncio.run(bot.send_message(chat_id=1, text="one"))
    asyncio.run(bot.send_message(chat_id=1, text="two"))

    assert len(FakeClient.instances) == 2


def test_a_closed_client_is_replaced(bot):
    async def scenario():
        await bot.send_message(chat_id=1, text="one")
        FakeClient.instances[0].is_closed = True
        await bot.send_message(chat_id=1, text="two")

    asyncio.run(scenario())

    assert len(FakeClient.instances) == 2


def test_no_token_still_sends_nothing(monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)

    assert asyncio.run(_HttpBot().send_message(chat_id=1, text="x")) is None
    assert FakeClient.instances == []
