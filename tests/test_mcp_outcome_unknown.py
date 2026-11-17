"""A write that was sent and never answered is OUTCOME UNKNOWN, not an error (MH-02).

aiohttp's total timeout raises a bare ``TimeoutError`` whose text is empty, so a
timed-out create used to read ``"Error creating position_executor: "`` — a blank
error for the one failure where money may already have moved. No spend path has
an idempotency key, so the natural reaction (retry) opens a second position or
signs a second swap.

What is pinned here:

- a timeout or a mid-response transport error on a create, a swap or a liquidity
  write says the outcome is unknown, says not to retry, and names the read that
  settles it;
- a connection that was never established stays an ordinary error (nothing was
  sent, so calling it "may have succeeded" would be a lie);
- an HTTP error the backend actually answered with stays an ordinary error;
- no error text is ever blank.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.middleware import (
    handle_errors,
    is_outcome_unknown,
)
from mcp_servers.hummingbot_api.schemas import (
    AMMRequest,
    CLMMRequest,
    GatewaySwapRequest,
)
from mcp_servers.hummingbot_api.tools import executor_create
from mcp_servers.hummingbot_api.tools.gateway_amm import manage_amm_impl
from mcp_servers.hummingbot_api.tools.gateway_clmm import manage_clmm_impl
from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps


def _http_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=SimpleNamespace(real_url="http://10.0.0.7:8000/x"),
        history=(),
        status=status,
        message="boom",
    )


def _raising(exc):
    async def call(*_args, **_kwargs):
        raise exc

    return call


class _Router:
    """Every method raises ``exc``; counts how many requests were attempted."""

    def __init__(self, exc):
        self.exc = exc
        self.sent = 0

    def __getattr__(self, _name):
        async def call(*_args, **_kwargs):
            self.sent += 1
            raise self.exc

        return call


@pytest.fixture(autouse=True)
def _no_saved_defaults(monkeypatch):
    monkeypatch.setattr(
        executor_create.executor_preferences, "get_defaults", lambda _type: {}
    )
    trading_rules_cache.clear()
    yield
    trading_rules_cache.clear()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        asyncio.TimeoutError(),
        TimeoutError(),
        aiohttp.ServerTimeoutError(),
        aiohttp.ServerDisconnectedError(),
        aiohttp.ClientPayloadError("truncated"),
        aiohttp.ClientOSError(104, "Connection reset by peer"),
        _http_error(504),
        _http_error(502),
    ],
)
def test_a_request_that_may_have_been_sent_is_outcome_unknown(exc):
    assert is_outcome_unknown(exc)


@pytest.mark.parametrize(
    "exc",
    [
        aiohttp.ClientConnectorError(
            SimpleNamespace(host="h", port=1, ssl=None), OSError(61, "refused")
        ),
        _http_error(400),
        _http_error(422),
        _http_error(429),
        _http_error(500),
        ValueError("bad"),
        ToolError("nope"),
    ],
)
def test_a_refusal_or_an_unsent_request_is_an_ordinary_error(exc):
    assert not is_outcome_unknown(exc)


# ---------------------------------------------------------------------------
# Executor create
# ---------------------------------------------------------------------------


def _create(exc):
    client = SimpleNamespace(executors=SimpleNamespace(create_executor=_raising(exc)))
    return asyncio.run(
        executor_create.create_position_executor(
            client,
            connector_name="binance_perpetual",
            trading_pair="BTC-USDT",
            side=1,
            amount=0.01,
            controller_id="agent-7",
        )
    )


def test_a_timed_out_create_says_it_may_have_succeeded_and_not_to_retry():
    result = _create(asyncio.TimeoutError())
    text = result["formatted_output"]

    assert text.startswith("OUTCOME UNKNOWN")
    assert "MAY HAVE SUCCEEDED" in text
    assert "Do NOT retry" in text
    assert "list_executors(controller_ids=['agent-7'], trading_pairs=['BTC-USDT'])" in (
        text
    )
    assert "Error creating" not in text
    assert result["outcome_unknown"] is True


def test_a_refused_create_is_still_an_error_and_never_blank():
    refused = _create(_http_error(422))["formatted_output"]
    assert refused.startswith("Error creating position_executor: ")
    assert "OUTCOME UNKNOWN" not in refused

    class _Silent(Exception):
        pass

    blank = _create(_Silent())["formatted_output"]
    assert blank == "Error creating position_executor: _Silent"


# ---------------------------------------------------------------------------
# Swap
# ---------------------------------------------------------------------------


def _swap(exc, action="execute"):
    router = _Router(exc)
    request = GatewaySwapRequest(
        action=action,
        connector="jupiter/router",
        network="solana-mainnet-beta",
        trading_pair="SOL-USDC",
        side="SELL",
        amount="0.5",
    )
    return router, manage_gateway_swaps(SimpleNamespace(gateway_swap=router), request)


def test_a_timed_out_swap_is_outcome_unknown_and_names_search_swaps():
    router, call = _swap(asyncio.TimeoutError())

    with pytest.raises(ToolError) as caught:
        asyncio.run(call)

    text = str(caught.value)
    assert text.startswith("OUTCOME UNKNOWN")
    assert "Do NOT retry" in text
    assert "search_swaps(trading_pair='SOL-USDC')" in text
    assert router.sent == 1


def test_a_swap_gateway_refused_is_not_called_unknown():
    _router, call = _swap(_http_error(400))

    with pytest.raises(aiohttp.ClientResponseError):
        asyncio.run(call)


def test_a_timed_out_quote_is_just_a_failed_read():
    """A quote moves nothing, so there is nothing to be unsure about."""
    _router, call = _swap(asyncio.TimeoutError(), action="quote")

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(call)


# ---------------------------------------------------------------------------
# Liquidity writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {
            "action": "open",
            "pool_address": "Pool1",
            "lower_price": "130",
            "upper_price": "170",
            "base_token_amount": "1",
        },
        {
            "action": "add_liquidity",
            "position_address": "Pos1",
            "base_token_amount": "1",
        },
        {
            "action": "remove_liquidity",
            "position_address": "Pos1",
            "percentage_to_remove": "50",
        },
        {"action": "close", "position_address": "Pos1"},
        {"action": "collect_fees", "position_address": "Pos1"},
    ],
)
def test_a_timed_out_clmm_write_is_outcome_unknown(fields):
    router = _Router(asyncio.TimeoutError())
    request = CLMMRequest(connector="orca", network="solana-mainnet-beta", **fields)

    with pytest.raises(ToolError) as caught:
        asyncio.run(manage_clmm_impl(SimpleNamespace(gateway_clmm=router), request))

    text = str(caught.value)
    assert text.startswith("OUTCOME UNKNOWN")
    assert "Do NOT retry" in text
    assert "manage_clmm(action='position_info'" in text


def test_a_timed_out_clmm_read_is_just_a_failed_read():
    router = _Router(asyncio.TimeoutError())
    request = CLMMRequest(
        action="position_info", connector="orca", network="solana-mainnet-beta"
    )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(manage_clmm_impl(SimpleNamespace(gateway_clmm=router), request))


@pytest.mark.parametrize(
    "fields",
    [
        {
            "action": "add_liquidity",
            "pool_address": "Pool1",
            "base_token_amount": "1",
            "quote_token_amount": "2",
        },
        {
            "action": "remove_liquidity",
            "pool_address": "Pool1",
            "percentage_to_remove": "50",
        },
    ],
)
def test_a_timed_out_amm_write_is_outcome_unknown(fields):
    router = _Router(aiohttp.ServerDisconnectedError())
    request = AMMRequest(connector="raydium", network="solana-mainnet-beta", **fields)

    with pytest.raises(ToolError) as caught:
        asyncio.run(manage_amm_impl(SimpleNamespace(gateway_amm=router), request))

    text = str(caught.value)
    assert text.startswith("OUTCOME UNKNOWN")
    assert "manage_amm(action='position_info'" in text
    assert "Pool1" in text


# ---------------------------------------------------------------------------
# The decorator every tool wears
# ---------------------------------------------------------------------------


def test_the_unknown_outcome_text_survives_the_tool_wrapper_without_a_logs_hint():
    @handle_errors("execute a Gateway swap", "\n\nGateway's logs usually say why.")
    async def execute_swap():
        _router, call = _swap(asyncio.TimeoutError())
        return await call

    with pytest.raises(ToolError) as caught:
        asyncio.run(execute_swap())

    text = str(caught.value)
    assert text.startswith("OUTCOME UNKNOWN")
    assert "logs usually say why" not in text


def test_a_blank_exception_is_never_rendered_as_a_blank_error():
    @handle_errors("get prices")
    async def get_prices():
        raise asyncio.TimeoutError()

    with pytest.raises(ToolError) as caught:
        asyncio.run(get_prices())

    assert str(caught.value) == "Failed to get prices: TimeoutError"
