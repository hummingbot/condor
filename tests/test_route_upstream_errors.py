"""Tests for SEC-130: no route module names the backend in a failed response.

SEC-116 established the rule for the executor mutations and SEC-126 extended it
to the executor reads: ``str(exc)`` on an ``aiohttp`` client exception embeds the
backend's own URL, so raising it as an ``HTTPException`` detail publishes the
internal host and port to anyone who can provoke a backend blip. Four more route
modules — ``bots``, ``settings``, ``market``, ``controller_performance`` — were
still doing exactly that at every backend call boundary.

They now go through the same helper, lifted into ``condor/web/routes/_errors.py``
so there is one implementation rather than five. These tests pin both halves of
the contract: the client loses the address, and the operator does not — the full
exception still reaches the server log, because a redaction that also destroys
the diagnostic is not a fix.

SEC-590 extended the sweep to the three modules none of those items listed —
``archived``, ``portfolio``, ``dex`` — and to ``condor/fetchers/archived_run.py``,
which built the same leak one call deeper and handed it to the route as an
``ArchivedRunUnavailable.detail``. The static guard below now also rejects the
f-string form (``detail=f"...{e}"``) those three used, which is why three audits
in a row could re-discover the same pattern: the guard only knew ``detail=str(e)``.
"""

import asyncio
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from aiohttp import ClientConnectorError, ClientResponseError, RequestInfo
from fastapi import HTTPException
from multidict import CIMultiDict
from yarl import URL

import condor.fetchers.archived_run as archived_run_module
import condor.fetchers.gateway_tokens as gateway_tokens_module
import condor.web.routes.archived as archived_module
import condor.web.routes.bots as bots_module
import condor.web.routes.controller_performance as cperf_module
import condor.web.routes.dex as dex_module
import condor.web.routes.market as market_module
import condor.web.routes.portfolio as portfolio_module
import condor.web.routes.settings as settings_module
from condor.web.models import WebUser

# The internal address a trader on a shared server must never be shown.
BACKEND_URL = "http://10.0.0.7:8000/bot-orchestration/status"
BACKEND_HOST = "10.0.0.7"
BACKEND_PORT = "8000"


def _api_error(status: int, detail: str) -> ClientResponseError:
    """The error the hummingbot client raises: API ``detail`` + backend URL."""
    info = RequestInfo(
        url=URL(BACKEND_URL),
        method="GET",
        headers=CIMultiDict(),
        real_url=URL(BACKEND_URL),
    )
    return ClientResponseError(info, (), status=status, message=detail)


def _transport_error() -> ClientConnectorError:
    """What a backend outage raises: no HTTP answer, host in the string."""
    return ClientConnectorError(
        SimpleNamespace(host=BACKEND_HOST, port=int(BACKEND_PORT), ssl=None),
        OSError(61, "Connection refused"),
    )


class _RaisingNamespace:
    """Any method looked up on this raises the bound exception."""

    def __init__(self, exc):
        self._exc = exc

    def __getattr__(self, _name):
        async def _call(*_args, **_kwargs):
            raise self._exc

        return _call


class FakeClient:
    """API client whose every sub-API fails the same way."""

    def __init__(self, exc):
        self._exc = exc

    def __getattr__(self, _name):
        return _RaisingNamespace(self._exc)


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, *_args, **_kwargs):
        return True

    async def get_client(self, _name):
        return self._client


_USER = WebUser(id=1, role="admin")


# --- One backend-call endpoint per converted module ---


def _bots_status():
    return asyncio.run(bots_module.get_bot(name="srv", bot_id="bot-1", user=_USER))


def _settings_pull_status():
    return asyncio.run(settings_module.gateway_pull_status(server="srv", user=_USER))


def _market_order_book():
    return asyncio.run(
        market_module.get_order_book(
            name="srv",
            connector="binance",
            trading_pair="SOL-USDC",
            depth=20,
            user=_USER,
        )
    )


def _cperf_delete_run():
    return asyncio.run(
        cperf_module.delete_bot_run(name="srv", bot_run_id=7, user=_USER)
    )


def _archived_list_databases():
    return asyncio.run(archived_module.list_archived_bots(name="srv", user=_USER))


def _portfolio_refresh():
    return asyncio.run(
        portfolio_module.get_portfolio(name="srv", refresh=True, user=_USER)
    )


# Wrapped SOL, purely as a syntactically valid mint for the address parser.
_SOL_MINT = "So11111111111111111111111111111111111111112"


def _dex_add_token():
    """The token-registration route, entered at the branch that can actually raise.

    ``ensure_tokens_listed`` folds its own upstream errors into a ``failed``
    verdict, so the handler's catch-all is never reached through it. What does
    reach it is the second question the collision branch asks: Gateway refuses
    the ticker, and the follow-up lookup naming the current holder is the call
    that blips. The stand-in for that lookup reaches through the client it is
    handed, so it raises whatever the fixture bound rather than a second, made-up
    error.
    """

    async def _symbol_taken(_client, _network, addresses, **_kwargs):
        return {address: "symbol_taken" for address in addresses}

    async def _holder_lookup_fails(client, *_args, **_kwargs):
        return await client.gateway.get_tokens()

    with (
        mock.patch.object(gateway_tokens_module, "ensure_tokens_listed", _symbol_taken),
        mock.patch.object(
            gateway_tokens_module, "find_symbol_holder", _holder_lookup_fails
        ),
    ):
        return asyncio.run(
            dex_module.add_dex_token(
                name="srv",
                body=dex_module.AddTokenRequest(
                    network="solana-mainnet-beta",
                    address=_SOL_MINT,
                    symbol="WSOL",
                ),
                user=_USER,
            )
        )


ENDPOINTS = [
    pytest.param(bots_module, _bots_status, id="bots-get-bot-status"),
    pytest.param(settings_module, _settings_pull_status, id="settings-pull-status"),
    pytest.param(market_module, _market_order_book, id="market-order-book"),
    pytest.param(cperf_module, _cperf_delete_run, id="cperf-delete-bot-run"),
    pytest.param(archived_module, _archived_list_databases, id="archived-list-dbs"),
    pytest.param(portfolio_module, _portfolio_refresh, id="portfolio-refresh"),
    pytest.param(dex_module, _dex_add_token, id="dex-add-token"),
]


@pytest.fixture
def failing_backend(monkeypatch):
    """Point a route module's config manager at a client that always fails."""

    def _bind(module, exc):
        monkeypatch.setattr(
            module, "get_config_manager", lambda: _FakeCM(FakeClient(exc))
        )

    return _bind


@pytest.mark.parametrize("module,call", ENDPOINTS)
def test_a_backend_outage_never_shows_the_backend_address(
    module, call, failing_backend
):
    exc = _transport_error()
    # Precondition: the raw string really does carry the internal address.
    assert BACKEND_HOST in str(exc)
    failing_backend(module, exc)

    with pytest.raises(HTTPException) as caught:
        call()

    assert caught.value.status_code == 502, "an unreachable backend is not a 400"
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail
    assert BACKEND_URL not in caught.value.detail


@pytest.mark.parametrize("module,call", ENDPOINTS)
def test_an_api_rejection_never_shows_the_backend_address(
    module, call, failing_backend
):
    exc = _api_error(400, "unknown bot name")
    assert BACKEND_HOST in str(exc)
    failing_backend(module, exc)

    with pytest.raises(HTTPException) as caught:
        call()

    assert caught.value.status_code == 400, "an upstream 4xx is the caller's own"
    assert "unknown bot name" in caught.value.detail, "the API's own reason survives"
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail


@pytest.mark.parametrize("module,call", ENDPOINTS)
def test_an_upstream_5xx_is_a_502(module, call, failing_backend):
    failing_backend(module, _api_error(503, "backend restarting"))

    with pytest.raises(HTTPException) as caught:
        call()

    assert caught.value.status_code == 502


@pytest.mark.parametrize("module,call", ENDPOINTS)
def test_the_full_exception_still_reaches_the_server_log(
    module, call, failing_backend, caplog
):
    """Only the client loses the address — the operator keeps the diagnostic."""
    failing_backend(module, _transport_error())

    with caplog.at_level(logging.ERROR, logger=module.__name__):
        with pytest.raises(HTTPException):
            call()

    assert BACKEND_HOST in caplog.text, "diagnostics were lost, not just redacted"
    assert any(
        record.exc_info for record in caplog.records
    ), "the traceback is what makes the log entry actionable"


# --- The fetcher one call deeper, whose detail the route re-raises verbatim ---


def test_the_archived_run_fetcher_does_not_hand_the_address_to_the_route(
    failing_backend, caplog
):
    """``ArchivedRunUnavailable.detail`` becomes the 502 body unchanged.

    ``archived.py::_load_run`` re-raises it as-is, so a detail built with
    ``str(e)`` leaks the backend exactly as if the route had interpolated it
    itself — the redaction has to happen where the string is built.
    """
    failing_backend(archived_module, _transport_error())

    with caplog.at_level(logging.ERROR, logger=archived_run_module.__name__):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(
                archived_module.get_archived_performance(
                    name="srv",
                    db_path="/data/leak-probe.sqlite",
                    include_executors=False,
                    user=_USER,
                )
            )

    assert caught.value.status_code == 502, "a reachable-but-broken backend is a 502"
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail
    assert BACKEND_URL not in caught.value.detail
    assert BACKEND_HOST in caplog.text, "the operator still gets the address"


# --- The settings helper every settings endpoint funnels through ---


def test_a_client_that_cannot_be_built_does_not_name_the_backend_either(monkeypatch):
    """``_get_client`` interpolated the exception straight into the detail."""

    class _UnreachableCM:
        def has_server_access(self, *_a, **_kw):
            return True

        async def get_client(self, _name):
            raise _transport_error()

    monkeypatch.setattr(settings_module, "get_config_manager", _UnreachableCM)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(settings_module.gateway_pull_status(server="srv", user=_USER))

    assert caught.value.status_code == 502
    assert BACKEND_HOST not in caught.value.detail
    assert BACKEND_PORT not in caught.value.detail


def test_an_unknown_server_still_says_so(monkeypatch):
    """The config manager's own ValueError names only what the caller typed."""

    class _NoSuchServerCM:
        def has_server_access(self, *_a, **_kw):
            return True

        async def get_client(self, name):
            raise ValueError(f"Server '{name}' not found")

    monkeypatch.setattr(settings_module, "get_config_manager", _NoSuchServerCM)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(settings_module.gateway_pull_status(server="srv", user=_USER))

    assert "not found" in caught.value.detail


# --- The pattern must not creep back in ---

# Both shapes of the same mistake. Only the first was pinned until SEC-590, and
# the three modules that item found all used the second — which is how the rule
# survived three audits without the guard ever noticing.
_LEAK = re.compile(r"""detail=(?:str\((?:e|exc)\)|f["'][^"']*\{\s*(?:e|exc)\b)""")
_BARE_EXCEPT = re.compile(r"^\s*except (Exception|BaseException) as (e|exc):")

CONVERTED_MODULES = [
    bots_module,
    settings_module,
    market_module,
    cperf_module,
    archived_module,
    portfolio_module,
    dex_module,
]


@pytest.mark.parametrize(
    "module", CONVERTED_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_no_backend_call_stringifies_the_exception_into_the_detail(module):
    """A bare ``except Exception`` is what catches the aiohttp client errors.

    The handful of ``detail=str(e)`` sites left in these modules catch a *named*
    domain exception — a rejected identifier, an unparseable URL — whose message
    is our own text about the caller's own input. Those are the correct message
    for the caller and carry no address. What must never come back is the bare
    catch-all feeding the exception string to the client.
    """
    lines = Path(module.__file__).read_text().split("\n")

    offenders = []
    for index, line in enumerate(lines):
        if not _LEAK.search(line):
            continue
        clause = next(
            (
                lines[back]
                for back in range(index, -1, -1)
                if lines[back].lstrip().startswith("except ")
            ),
            "",
        )
        if _BARE_EXCEPT.match(clause):
            offenders.append(f"{module.__file__}:{index + 1}: {line.strip()}")

    assert not offenders, "a backend call leaks the exception string:\n" + "\n".join(
        offenders
    )


def test_the_shared_helper_is_the_only_mapping():
    """Each converted module raises through the shared helper, not its own copy."""
    for module in CONVERTED_MODULES:
        source = Path(module.__file__).read_text()
        assert "from condor.web.routes._errors import upstream_error" in source
        assert "raise upstream_error(" in source
        assert "describe_executor_error" not in source, "no second sanitizer"
