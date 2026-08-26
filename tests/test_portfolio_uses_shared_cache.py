"""``/portfolio`` renders from the shared cache, not from a forced re-fetch (PERF-242).

The initial render used to call ``portfolio.get_state(refresh=True)``, which makes
the backend re-query every connected exchange — the most expensive call the
portfolio API offers — on every ``/portfolio``, while ServerDataService was
already polling the very same payload every 10s (60s TTL) for the dashboard.

These tests pin the trade the fix makes: the render reads SDS and costs nothing
when the key is warm, still renders through SDS's own fallback fetch when it is
cold (and that fallback is *not* a refresh), and the explicit Refresh button
still re-queries the exchanges — writing what it got back into the cache, so the
next render cannot regress to the older polled value.
"""

import inspect

import pytest

from condor.fetchers.portfolio import fetch_portfolio
from handlers import portfolio as portfolio_handler

SERVER = "prod"
USER_ID = 777

STATE = {
    "master": {
        "binance": [{"token": "BTC", "units": 0.01, "value": 500.0}],
        "solana-mainnet-beta": [{"token": "SOL", "units": 5.0, "value": 1000.0}],
    }
}

# What the exchanges would say if anyone actually asked them.
FRESH_STATE = {
    "master": {"binance": [{"token": "BTC", "units": 0.02, "value": 2000.0}]},
}


class _CountingClient:
    """Records every ``portfolio.get_state`` call and the kwargs it was made with."""

    def __init__(self, state=FRESH_STATE):
        self.portfolio = self
        self._state = state
        self.calls = []

    async def get_state(self, **kwargs):
        self.calls.append(kwargs)
        return self._state


class _FakeSDS:
    """SDS stand-in: ``cached`` None means the key is cold and get_or_fetch fetches."""

    def __init__(self, cached, client):
        self.cached = cached
        self._client = client
        self.puts = []
        self.get_or_fetch_calls = []

    async def get_or_fetch(self, server, data_type, **params):
        self.get_or_fetch_calls.append((server, data_type))
        if self.cached is not None:
            return self.cached
        # Exactly what the real service does on a miss: run the registered
        # fetcher, which reads get_state() without refresh.
        self.cached = await fetch_portfolio(self._client)
        return self.cached

    def put(self, server, data_type, value, **params):
        self.puts.append((server, data_type, value))


class _FakeConfigManager:
    def __init__(self, client):
        self._client = client

    def list_servers(self):
        return {SERVER: {"enabled": True}}

    def get_accessible_servers(self, user_id):
        return [SERVER]

    async def get_client(self, name):
        return self._client


class _SentMessage:
    def __init__(self, sink):
        self._sink = sink
        self.message_id = 2

    async def edit_text(self, text, **kwargs):
        self._sink.append(text)


class _FakeMessage:
    def __init__(self, sink):
        self._sink = sink
        self.chat_id = 1
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return _SentMessage(self._sink)


class _FakeChat:
    id = 1


class _FakeUpdate:
    def __init__(self, message):
        self.message = message
        self.callback_query = None
        self.effective_chat = _FakeChat()


class _FakeContext:
    def __init__(self, user_data):
        self.user_data = user_data


class _FakeBot:
    def __init__(self, sink):
        self._sink = sink

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self._sink.append(text)


class _FakeQuery:
    def __init__(self, bot):
        self._bot = bot

    def get_bot(self):
        return self._bot


class _RefreshUpdate:
    def __init__(self, bot):
        self.callback_query = _FakeQuery(bot)


# ``portfolio_command`` is wrapped by @restricted / @hummingbot_api_required,
# both of which use functools.wraps; unwrap to the handler body itself.
_portfolio_command = inspect.unwrap(portfolio_handler.portfolio_command)


@pytest.fixture
def render(monkeypatch):
    """Run the initial /portfolio render; hand back (sds, client, messages)."""

    async def _run(cached=STATE, state=FRESH_STATE, user_data=None):
        client = _CountingClient(state)
        sds = _FakeSDS(cached, client)
        monkeypatch.setattr(
            "condor.server_data_service.get_server_data_service", lambda: sds
        )
        monkeypatch.setattr(
            "config_manager.get_config_manager", lambda: _FakeConfigManager(client)
        )

        sink = []
        message = _FakeMessage(sink)
        context = _FakeContext(dict(user_data or {}, _user_id=USER_ID))
        await _portfolio_command(_FakeUpdate(message), context)
        return sds, client, sink, context

    return _run


@pytest.mark.asyncio
async def test_a_warm_cache_costs_no_portfolio_call_at_all(render):
    sds, client, sink, context = await render()

    assert client.calls == [], "the render must not re-query the exchanges"
    assert sds.get_or_fetch_calls == [(SERVER, _portfolio_type())]
    assert sink, "no message was rendered"
    assert context.user_data[portfolio_handler.KEY_BALANCES] == STATE


@pytest.mark.asyncio
async def test_a_cold_key_still_renders_via_the_fallback_fetch(render):
    sds, client, sink, context = await render(cached=None)

    # One fetch, through SDS's fallback — and it is not the expensive refresh.
    assert client.calls == [{}]
    assert sink
    assert context.user_data[portfolio_handler.KEY_BALANCES] == FRESH_STATE


@pytest.mark.asyncio
async def test_an_unreachable_server_renders_instead_of_raising(monkeypatch):
    """get_or_fetch returns None rather than raising; the render must survive it."""

    class _NoneSDS(_FakeSDS):
        async def get_or_fetch(self, server, data_type, **params):
            return None

    client = _CountingClient()
    sds = _NoneSDS(None, client)
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service", lambda: sds
    )
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _FakeConfigManager(client)
    )

    sink = []
    message = _FakeMessage(sink)
    context = _FakeContext({"_user_id": USER_ID})
    await _portfolio_command(_FakeUpdate(message), context)

    assert sink, "an empty portfolio must still render a message"
    assert context.user_data[portfolio_handler.KEY_BALANCES] is None


@pytest.mark.asyncio
async def test_network_filtering_still_applies_to_the_cached_payload(
    render, monkeypatch
):
    monkeypatch.setattr(
        portfolio_handler, "get_all_enabled_networks", lambda _ud: {"ethereum-mainnet"}
    )

    _sds, _client, _sink, context = await render()

    balances = context.user_data[portfolio_handler.KEY_BALANCES]
    # The CEX connector is never filtered; the disabled gateway network is gone.
    assert set(balances["master"]) == {"binance"}


@pytest.mark.asyncio
async def test_refresh_button_re_queries_the_exchanges_and_refills_the_cache(
    monkeypatch,
):
    client = _CountingClient(FRESH_STATE)
    sds = _FakeSDS(STATE, client)
    monkeypatch.setattr(
        "condor.server_data_service.get_server_data_service", lambda: sds
    )
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _FakeConfigManager(client)
    )

    sink = []
    context = _FakeContext(
        {
            "_user_id": USER_ID,
            portfolio_handler.KEY_CHAT_ID: 1,
            portfolio_handler.KEY_TEXT_MESSAGE_ID: 2,
        }
    )
    await portfolio_handler.refresh_portfolio_dashboard(
        _RefreshUpdate(_FakeBot(sink)), context
    )

    assert client.calls == [{"refresh": True}], "Refresh must force a real re-fetch"
    assert sink, "refresh produced no message"
    assert context.user_data[portfolio_handler.KEY_BALANCES] == FRESH_STATE
    # And the shared cache now holds the raw fresh payload, so the next
    # /portfolio cannot regress to the older polled value.
    assert sds.puts == [(SERVER, _portfolio_type(), FRESH_STATE)]


def _portfolio_type():
    from condor.server_data_service import ServerDataType

    return ServerDataType.PORTFOLIO
