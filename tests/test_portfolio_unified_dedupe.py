"""Hyperliquid unified-account de-duplication is one rule, shared (ARCH-248).

In unified / portfolio-margin mode Hyperliquid reports the SAME USDC collateral
on both ``hyperliquid`` (spot) and ``hyperliquid_perpetual`` (perp), so summing
``portfolio.get_state()`` raw inflates the portfolio. The rule used to live in
the body of the REST handler as ``_dedupe_hyperliquid_unified``, operating on
``ConnectorBalance`` models — which meant the dashboard was the only surface
that got it right, while ``/portfolio`` (Telegram) and the agent's
``get_portfolio_overview`` (MCP) both double-counted the collateral.

It now lives in ``condor.fetchers.portfolio.dedupe_unified_accounts``, on the
raw payload. These tests pin the thing that actually matters: the three
surfaces agree on the total, the standard (non-unified) account is untouched
everywhere, and the SDS-cached payload the route reads is never mutated.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from condor.fetchers.portfolio import (
    UNIFIED_ACCOUNT_NOTE,
    dedupe_unified_accounts,
)
from condor.web.models import WebUser
from condor.web.routes.portfolio import get_portfolio
from handlers import portfolio as portfolio_handler
from mcp_servers.hummingbot_api.tools.portfolio import get_portfolio_overview
from utils.telegram_formatters import format_portfolio_overview

SERVER = "prod"
USER_ID = 777
_USER = WebUser(id=USER_ID, role="admin")


def _state(perp_stable_value: float):
    """One account holding the same 1,000 USDC on spot and (maybe) on perp."""
    return {
        "master": {
            "binance": [{"token": "BTC", "units": 0.01, "value": 500.0}],
            "hyperliquid": [{"token": "USDC", "units": 1000.0, "value": 1000.0}],
            "hyperliquid_perpetual": [
                {
                    "token": "USDC",
                    "units": perp_stable_value,
                    "value": perp_stable_value,
                },
                {"token": "HYPE", "units": 10.0, "value": 200.0},
            ],
        }
    }


# Unified: perp collateral mirrors spot, so it must be counted once.
UNIFIED = _state(1000.0)
UNIFIED_TOTAL = 500.0 + 1000.0 + 200.0

# Standard: spot and perp stables genuinely differ, so both are real money.
STANDARD = _state(250.0)
STANDARD_TOTAL = 500.0 + 1000.0 + 250.0 + 200.0


def _sum_raw(state) -> float:
    """The naive total every un-deduped surface computes."""
    return sum(
        balance["value"]
        for account in state.values()
        for balances in account.values()
        for balance in balances
    )


# ---------------------------------------------------------------- the rule ---


def test_unified_collateral_is_dropped_from_the_perp_side_only():
    deduped, annotated = dedupe_unified_accounts(UNIFIED)

    assert _sum_raw(deduped) == UNIFIED_TOTAL
    assert annotated == {("master", "hyperliquid_perpetual")}
    # The spot side and the perp-only position both survive untouched.
    assert deduped["master"]["hyperliquid"] == UNIFIED["master"]["hyperliquid"]
    assert deduped["master"]["hyperliquid_perpetual"] == [
        {"token": "HYPE", "units": 10.0, "value": 200.0}
    ]


def test_standard_account_is_left_alone():
    deduped, annotated = dedupe_unified_accounts(STANDARD)

    assert deduped == STANDARD
    assert annotated == set()
    assert _sum_raw(deduped) == STANDARD_TOTAL


def test_the_cached_payload_is_never_mutated():
    """The route hands in the object SDS is caching; in-place edits would poison it."""
    state = _state(1000.0)
    deduped, _ = dedupe_unified_accounts(state)

    assert deduped is not state
    assert state == _state(1000.0)
    assert _sum_raw(state) == _sum_raw(_state(1000.0))


def test_detection_is_per_account():
    """Two accounts on one server can be in different modes."""
    state = {"unified": _state(1000.0)["master"], "standard": _state(250.0)["master"]}

    deduped, annotated = dedupe_unified_accounts(state)

    assert annotated == {("unified", "hyperliquid_perpetual")}
    assert deduped["standard"] == state["standard"]
    assert _sum_raw(deduped) == UNIFIED_TOTAL + STANDARD_TOTAL


@pytest.mark.parametrize("state", [None, "not-a-dict", {}, {"master": None}])
def test_malformed_payloads_pass_through(state):
    assert dedupe_unified_accounts(state) == (state, set())


# ------------------------------------------------------------- surface: web ---


class _FakeSDS:
    def __init__(self, state):
        self._state = state
        self._fetch_registry = {}
        self._health = {}

    async def get_or_fetch(self, name, data_type, **kwargs):
        return self._state


@pytest.fixture
def web_portfolio(monkeypatch):
    async def _call(state):
        monkeypatch.setattr(
            "condor.server_data_service.get_server_data_service",
            lambda: _FakeSDS(state),
        )
        return await get_portfolio(SERVER, refresh=False, user=_USER)

    return _call


@pytest.mark.asyncio
async def test_rest_total_dedupes_and_annotates_the_perp_connector(web_portfolio):
    response = await web_portfolio(UNIFIED)

    assert response.total_usd == pytest.approx(UNIFIED_TOTAL)
    perp = next(
        c for c in response.connectors if c.connector == "hyperliquid_perpetual"
    )
    assert perp.note == UNIFIED_ACCOUNT_NOTE
    assert [b.token for b in perp.balances] == ["HYPE"]
    spot = next(c for c in response.connectors if c.connector == "hyperliquid")
    assert spot.note is None


@pytest.mark.asyncio
async def test_rest_total_is_untouched_for_a_standard_account(web_portfolio):
    response = await web_portfolio(STANDARD)

    assert response.total_usd == pytest.approx(STANDARD_TOTAL)
    perp = next(
        c for c in response.connectors if c.connector == "hyperliquid_perpetual"
    )
    assert perp.note is None
    assert {b.token for b in perp.balances} == {"USDC", "HYPE"}


# -------------------------------------------------------- surface: telegram ---


class _FakeBot:
    def __init__(self):
        self.texts = []

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.texts.append(text)


class _FakeQuery:
    def __init__(self, bot):
        self._bot = bot

    def get_bot(self):
        return self._bot


class _FakeUpdate:
    def __init__(self, bot):
        self.callback_query = _FakeQuery(bot)


class _FakeContext:
    def __init__(self, user_data):
        self.user_data = user_data


class _FakeClient:
    def __init__(self, state):
        self.portfolio = self
        self._state = state

    async def get_state(self, **kwargs):
        return self._state


class _FakeConfigManager:
    def __init__(self, state):
        self._state = state

    def list_servers(self):
        return {SERVER: {"enabled": True}}

    def get_accessible_servers(self, user_id):
        return [SERVER]

    async def get_client(self, name):
        return _FakeClient(self._state)


@pytest.fixture
def telegram_balances(monkeypatch):
    """Run the /portfolio refresh and hand back what the formatter was fed."""

    async def _call(state):
        captured = {}
        real = portfolio_handler.format_portfolio_overview

        def _spy(overview_data, **kwargs):
            captured["balances"] = overview_data.get("balances")
            return real(overview_data, **kwargs)

        monkeypatch.setattr(portfolio_handler, "format_portfolio_overview", _spy)
        monkeypatch.setattr(
            "config_manager.get_config_manager", lambda: _FakeConfigManager(state)
        )

        bot = _FakeBot()
        context = _FakeContext(
            {
                "_user_id": USER_ID,
                portfolio_handler.KEY_CHAT_ID: 1,
                portfolio_handler.KEY_TEXT_MESSAGE_ID: 2,
            }
        )
        await portfolio_handler.refresh_portfolio_dashboard(_FakeUpdate(bot), context)
        assert bot.texts, "refresh produced no message"
        return captured["balances"], bot.texts[-1]

    return _call


@pytest.mark.asyncio
async def test_telegram_overview_counts_unified_collateral_once(telegram_balances):
    balances, message = await telegram_balances(UNIFIED)

    assert _sum_raw(balances) == pytest.approx(UNIFIED_TOTAL)
    # $1.70K, not the $2.70K the raw payload would render.
    assert "1\\.70K" in message


@pytest.mark.asyncio
async def test_telegram_overview_leaves_a_standard_account_alone(telegram_balances):
    balances, _ = await telegram_balances(STANDARD)

    assert _sum_raw(balances) == pytest.approx(STANDARD_TOTAL)


def test_formatter_total_matches_the_deduped_payload():
    """``format_portfolio_overview`` sums whatever it is handed — pin both cases."""
    for state, expected in ((UNIFIED, UNIFIED_TOTAL), (STANDARD, STANDARD_TOTAL)):
        deduped, _ = dedupe_unified_accounts(state)
        message = format_portfolio_overview({"balances": deduped})
        assert f"{expected / 1000:.2f}K".replace(".", "\\.") in message


# -------------------------------------------------------------- surface: mcp ---


async def _mcp_overview(state):
    return await get_portfolio_overview(
        client=_FakeClient(state),
        include_balances=True,
        include_perp_positions=False,
        include_lp_positions=False,
        include_active_orders=False,
    )


async def _mcp_total(state):
    return (await _mcp_overview(state))["total_balance_value"]


@pytest.mark.asyncio
async def test_mcp_overview_counts_unified_collateral_once():
    assert await _mcp_total(UNIFIED) == pytest.approx(UNIFIED_TOTAL)


@pytest.mark.asyncio
async def test_mcp_overview_leaves_a_standard_account_alone():
    assert await _mcp_total(STANDARD) == pytest.approx(STANDARD_TOTAL)


@pytest.mark.asyncio
async def test_mcp_overview_explains_the_deduped_connector():
    """The note is what stops the model re-adding the collateral it cannot see.

    The dedupe strips every stable row from ``hyperliquid_perpetual``, so on a
    flat-collateral unified account that connector leaves the table entirely.
    An agent told to size perps from this output would read "no margin" — the
    note, the same sentence the dashboard renders, is the explanation.
    """
    result = await _mcp_overview(UNIFIED)
    balances = next(s for s in result["sections"] if s["title"] == "Token Balances")

    assert UNIFIED_ACCOUNT_NOTE in balances["content"]
    assert "hyperliquid_perpetual (master)" in balances["content"]
    assert UNIFIED_ACCOUNT_NOTE in result["formatted_output"]
    # And the note explains the total rather than changing it.
    assert result["total_balance_value"] == pytest.approx(UNIFIED_TOTAL)


@pytest.mark.asyncio
async def test_mcp_overview_says_nothing_about_a_standard_account():
    result = await _mcp_overview(STANDARD)

    assert UNIFIED_ACCOUNT_NOTE not in result["formatted_output"]


def test_mcp_portfolio_tool_does_not_drag_in_pool_data():
    """The hummingbot MCP server is also run standalone (see its settings.py).

    ``condor.fetchers.portfolio`` reaches ``condor.pool_data`` and its
    geckoterminal/orca/dotenv chain, so the dedupe helper is imported lazily
    inside the tool. A fresh interpreter proves it: importing this test module
    alone would not, since it imports the helper eagerly at the top.
    """
    probe = (
        "import sys; import mcp_servers.hummingbot_api.tools.portfolio as m; "
        "assert 'condor.pool_data' not in sys.modules, sorted("
        "k for k in sys.modules if k.startswith('condor'))"
    )
    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )


# ------------------------------------------------------ the three agree ------


@pytest.mark.asyncio
async def test_all_three_surfaces_report_the_same_total(
    web_portfolio, telegram_balances
):
    for state, expected in ((UNIFIED, UNIFIED_TOTAL), (STANDARD, STANDARD_TOTAL)):
        rest = (await web_portfolio(state)).total_usd
        telegram = _sum_raw((await telegram_balances(state))[0])
        mcp = await _mcp_total(state)

        assert rest == pytest.approx(expected)
        assert telegram == pytest.approx(expected)
        assert mcp == pytest.approx(expected)
