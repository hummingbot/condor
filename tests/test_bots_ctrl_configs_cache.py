"""PERF-578: the bots-page enrichment caches each bot's controller configs.

``_fetch_ctrl_configs`` used to issue one ``get_bot_controller_configs`` per bot
on *every* enrichment refresh (SDS polls it every 30s per server) for data that
changes only when someone edits a config. It now reads through a per-``(server,
bot)`` TTL cache with in-flight coalescing.

Every fake upstream call awaits a real suspension point, so two "concurrent"
callers genuinely interleave: without it they would run to completion one after
the other and the coalescing test would pass against the unfixed code too.
"""

import asyncio

import pytest

import condor.fetchers.bots as bots_mod
from condor.fetchers.bots import (
    clear_ctrl_configs_cache,
    fetch_bots_enrichment,
    invalidate_ctrl_configs,
)

SERVER_URL = "http://hb.test:8000"


def _bots(*names: str) -> list[dict]:
    return [{"bot_name": n} for n in names]


class _Client:
    """Upstream stub counting controller-config calls, one per bot."""

    def __init__(self, base_url: str = SERVER_URL, *, fail_next: bool = False):
        self.base_url = base_url
        self.config_calls: list[str] = []
        self.fail_next = fail_next
        self.connector = "binance"
        self.gate: asyncio.Event | None = None

    @property
    def controllers(self):
        return self

    @property
    def bot_orchestration(self):
        return self

    async def get_bot_controller_configs(self, bot_name):
        self.config_calls.append(bot_name)
        if self.gate is not None:
            await self.gate.wait()
        else:
            # A real client always suspends; without this two concurrent
            # callers would run sequentially and never exercise coalescing.
            await asyncio.sleep(0)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("upstream down")
        return [
            {
                "id": f"ctrl_{bot_name}",
                "controller_name": f"name_{bot_name}",
                "connector_name": self.connector,
                "trading_pair": "BTC-USDT",
            }
        ]

    async def get_bot_runs(self, **_kw):
        return {"data": {}}

    async def get_latest_controller_performance(self):
        return {"data": []}


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_ctrl_configs_cache()
    yield
    clear_ctrl_configs_cache()


@pytest.mark.asyncio
async def test_second_enrichment_within_ttl_reuses_cached_configs():
    """Two refreshes inside the TTL cost one upstream call per bot, total."""
    client = _Client()
    first = await fetch_bots_enrichment(client, _bots("alpha", "beta"))
    second = await fetch_bots_enrichment(client, _bots("alpha", "beta"))

    assert sorted(client.config_calls) == ["alpha", "beta"]
    assert first.ctrl_configs == second.ctrl_configs
    assert second.ctrl_configs["ctrl_alpha"]["trading_pair"] == "BTC-USDT"


@pytest.mark.asyncio
async def test_concurrent_cold_enrichments_coalesce_into_one_call_per_bot():
    """Two refreshes racing on a cold cache share one call per bot, not two."""
    client = _Client()
    client.gate = asyncio.Event()

    first = asyncio.ensure_future(fetch_bots_enrichment(client, _bots("alpha", "beta")))
    second = asyncio.ensure_future(
        fetch_bots_enrichment(client, _bots("alpha", "beta"))
    )
    # Let both callers reach the upstream call before any of them completes.
    for _ in range(10):
        await asyncio.sleep(0)
    assert client.gate is not None
    client.gate.set()
    a, b = await asyncio.gather(first, second)

    assert sorted(client.config_calls) == ["alpha", "beta"]
    assert a.ctrl_configs == b.ctrl_configs
    assert "ctrl_alpha" in a.ctrl_configs and "ctrl_beta" in a.ctrl_configs


@pytest.mark.asyncio
async def test_ttl_expiry_refetches(monkeypatch):
    """Past the TTL the configs are fetched again."""
    client = _Client()
    now = [1000.0]

    class _Clock:
        @staticmethod
        def monotonic():
            return now[0]

    monkeypatch.setattr(bots_mod, "time", _Clock)

    await fetch_bots_enrichment(client, _bots("alpha"))
    await fetch_bots_enrichment(client, _bots("alpha"))
    now[0] += bots_mod._CTRL_CONFIGS_TTL + 1
    await fetch_bots_enrichment(client, _bots("alpha"))

    assert client.config_calls.count("alpha") == 2


@pytest.mark.asyncio
async def test_a_new_bot_only_fetches_itself():
    """Per-bot keying: adding a bot does not invalidate the others."""
    client = _Client()
    await fetch_bots_enrichment(client, _bots("alpha"))
    await fetch_bots_enrichment(client, _bots("alpha", "beta"))

    assert client.config_calls == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_failed_fetch_is_not_cached():
    """A call that raises is retried by the next refresh, not remembered."""
    client = _Client(fail_next=True)
    first = await fetch_bots_enrichment(client, _bots("alpha"))
    assert first.ctrl_configs == {}

    second = await fetch_bots_enrichment(client, _bots("alpha"))
    assert client.config_calls == ["alpha", "alpha"]
    assert second.ctrl_configs["ctrl_alpha"]["connector_name"] == "binance"


@pytest.mark.asyncio
async def test_invalidation_shows_the_edited_config():
    """After a config edit invalidates the bot, the next page shows the edit."""
    client = _Client()
    await fetch_bots_enrichment(client, _bots("alpha", "beta"))

    client.connector = "kucoin"
    invalidate_ctrl_configs(client, "alpha")
    page = await fetch_bots_enrichment(client, _bots("alpha", "beta"))

    assert client.config_calls == ["alpha", "beta", "alpha"]
    assert page.ctrl_configs["ctrl_alpha"]["connector_name"] == "kucoin"
    assert page.ctrl_configs["ctrl_beta"]["connector_name"] == "binance"


@pytest.mark.asyncio
async def test_server_wide_invalidation_drops_every_bot():
    """Editing a saved config by id drops the whole server's entries."""
    client = _Client()
    await fetch_bots_enrichment(client, _bots("alpha", "beta"))

    invalidate_ctrl_configs(client)
    await fetch_bots_enrichment(client, _bots("alpha", "beta"))

    assert sorted(client.config_calls) == ["alpha", "alpha", "beta", "beta"]


@pytest.mark.asyncio
async def test_two_servers_never_share_an_answer():
    """The cache is keyed by server: one server's configs never serve another."""
    a = _Client("http://a.test:8000")
    b = _Client("http://b.test:8000")
    b.connector = "kucoin"

    await fetch_bots_enrichment(a, _bots("alpha"))
    page = await fetch_bots_enrichment(b, _bots("alpha"))

    assert b.config_calls == ["alpha"]
    assert page.ctrl_configs["ctrl_alpha"]["connector_name"] == "kucoin"


@pytest.mark.asyncio
async def test_departed_bots_are_pruned():
    """A bot that leaves the fleet does not linger in the cache."""
    client = _Client()
    await fetch_bots_enrichment(client, _bots("alpha", "beta"))
    await fetch_bots_enrichment(client, _bots("alpha"))

    assert (SERVER_URL, "beta") not in bots_mod._ctrl_configs_cache
    assert (SERVER_URL, "alpha") in bots_mod._ctrl_configs_cache


@pytest.mark.asyncio
async def test_client_without_base_url_is_not_cached():
    """An unidentifiable client shares no key with anyone: it always fetches."""
    client = _Client(base_url="")
    await fetch_bots_enrichment(client, _bots("alpha"))
    await fetch_bots_enrichment(client, _bots("alpha"))

    assert client.config_calls == ["alpha", "alpha"]
    assert not bots_mod._ctrl_configs_cache
