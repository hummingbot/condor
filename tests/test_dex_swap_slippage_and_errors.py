"""The DEX swap surface must not invent a slippage, and must not hide Gateway's reason.

Two contracts pinned here:

1. **Slippage.** Stack-wide, an OMITTED slippage means "the connector's configured slippage
   applies"; an explicit 0 is a real value. The Telegram swap UI used to substitute "1.0" for an
   unset slippage and forward it unconditionally, silently overriding every connector's own
   setting on the money path.

2. **Error surfacing.** Quote/execute failures used to be caught bare, logged at warning, and
   rendered to the user as "No quotes available for this pair" — a 404 "No pool found", a 400
   "Unsupported swap provider", and a genuine no-route were indistinguishable. The SDK raises
   aiohttp.ClientResponseError carrying the HTTP status and the API's own detail; that has to
   reach the user.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
from decimal import Decimal

import aiohttp
import pytest

from condor.preferences import get_dex_swap_defaults, set_dex_slippage
from handlers.dex.swap import (
    _last_swap_params,
    _quote_errors,
    _slippage_button_label,
    describe_api_error,
    get_slippage_pct,
    resolve_quote_denominated_amount,
)


def _response_error(status: int, message: str) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=None, history=(), status=status, message=message
    )


# ---------------------------------------------------------------- slippage


def test_unset_slippage_is_omitted_not_defaulted_to_one_percent():
    """No slippage in params -> None, which makes the SDK omit slippage_pct entirely."""
    assert get_slippage_pct({}) is None
    assert get_slippage_pct({"connector": "jupiter", "slippage": None}) is None


def test_explicit_zero_slippage_is_a_real_zero():
    """'0' means zero tolerance, NOT 'use the default' — it must survive as Decimal(0)."""
    assert get_slippage_pct({"slippage": "0"}) == Decimal("0")


def test_explicit_slippage_is_parsed_with_or_without_a_percent_sign():
    assert get_slippage_pct({"slippage": "2.5"}) == Decimal("2.5")
    assert get_slippage_pct({"slippage": "2.5%"}) == Decimal("2.5")


def test_the_button_reads_as_the_connector_default_until_the_user_overrides():
    assert _slippage_button_label({}) == "📊 Slippage: default"
    assert _slippage_button_label({"slippage": "0"}) == "📊 0%"
    assert _slippage_button_label({"slippage": "2.5"}) == "📊 2.5%"


def test_swap_defaults_carry_no_slippage_for_a_fresh_user():
    """A user who never set one must not get a fabricated 1.0 out of preferences."""
    defaults = get_dex_swap_defaults({})
    assert "slippage" not in defaults
    assert defaults["connector"] and defaults["network"] and defaults["trading_pair"]


def test_swap_defaults_return_a_slippage_the_user_did_set():
    user_data = {}
    set_dex_slippage(user_data, "3.0")
    assert get_dex_swap_defaults(user_data)["slippage"] == "3.0"


def test_last_swap_remembers_a_slippage_only_when_one_was_set():
    """Persisting a fabricated slippage would resurrect the override on the next swap."""
    without = _last_swap_params("jupiter", "solana-mainnet-beta", "SOL-USDC", "BUY", {})
    assert "slippage" not in without

    with_override = _last_swap_params(
        "jupiter", "solana-mainnet-beta", "SOL-USDC", "BUY", {"slippage": "0.5"}
    )
    assert with_override["slippage"] == "0.5"


# ---------------------------------------------------------------- error surfacing


def test_an_http_error_keeps_its_status_and_the_apis_own_message():
    """404 'No pool found' must not read the same as 400 'Unsupported swap provider'."""
    assert (
        describe_api_error(_response_error(404, "No pool found"))
        == "HTTP 404: No pool found"
    )
    assert (
        describe_api_error(_response_error(400, "Unsupported swap provider"))
        == "HTTP 400: Unsupported swap provider"
    )


def test_a_non_http_error_still_names_its_cause():
    assert describe_api_error(ValueError("bad pair")) == "bad pair"
    assert describe_api_error(TimeoutError()) == "TimeoutError"


def test_only_the_sides_that_raised_are_reported_as_errors():
    failure = _response_error(404, "No pool found")
    assert _quote_errors({"price": "1"}, failure) == ["SELL: HTTP 404: No pool found"]
    assert _quote_errors(failure, failure) == [
        "BUY: HTTP 404: No pool found",
        "SELL: HTTP 404: No pool found",
    ]
    assert _quote_errors({"price": "1"}, {"price": "1"}) == []


# ---------------------------------------------------------------- $ amount conversion


class _FakeSwapRouter:
    def __init__(self, quote=None, error=None):
        self.quote = quote
        self.error = error
        self.calls = []

    async def get_swap_quote(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.quote


class _FakeClient:
    def __init__(self, router):
        self.gateway_swap = router


def test_a_dollar_amount_converts_at_the_quoted_price():
    router = _FakeSwapRouter(quote={"price": "200"})
    amount = asyncio.run(
        resolve_quote_denominated_amount(
            _FakeClient(router),
            Decimal("100"),
            "jupiter/router",
            "solana-mainnet-beta",
            "SOL-USDC",
            None,
        )
    )
    assert amount == Decimal("0.5")
    # The pricing call must itself omit slippage_pct when the user set none
    assert router.calls[0]["slippage_pct"] is None


def test_a_failed_pricing_call_raises_with_the_real_cause_instead_of_using_the_raw_number():
    """Treating '$100' as 100 base tokens would size the trade catastrophically wrong."""
    router = _FakeSwapRouter(error=_response_error(404, "No pool found"))
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            resolve_quote_denominated_amount(
                _FakeClient(router),
                Decimal("100"),
                "meteora/amm",
                "solana-mainnet-beta",
                "NEW-SOL",
                None,
            )
        )
    assert "HTTP 404: No pool found" in str(excinfo.value)


def test_a_priceless_quote_raises_rather_than_defaulting_the_price_to_one():
    router = _FakeSwapRouter(quote={"amount_out": "3"})
    with pytest.raises(ValueError, match="no price"):
        asyncio.run(
            resolve_quote_denominated_amount(
                _FakeClient(router),
                Decimal("100"),
                "jupiter/router",
                "solana-mainnet-beta",
                "SOL-USDC",
                None,
            )
        )
