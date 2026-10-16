"""A loop is told whether it can trade BEFORE it starts (FEAT-139).

Issue #277: entries lost race hours to problems a preflight would have caught —
capital of equal total value sitting in the wrong token, an account in the wrong
position mode, size and notional rejections discovered one order at a time.

What is pinned here:

- the 780-USDC case: a requirement in an exact asset fails against capital of
  equal total value in two other tokens, and the refusal names the asset, the
  shortfall and what is actually held; the same requirement met in that asset
  passes;
- position mode and leverage are read from the ACCOUNT (not from config) and
  reported, and a mode mismatch against the loop's requirement fails;
- the venue's rules are reported per pair as values — min size, min notional,
  tick, fees — because a fee tier is a judgement, not a verdict;
- a venue that cannot be reached is "could not be checked": not a pass, not a
  failure, and it does not refuse a start;
- the preflight's verdict IS the create path's verdict, because it calls the
  same two validators;
- a loop that states no requirements is reported on and never gated, so the
  check cannot ground a working loop;
- `--agent` wires the section into doctor, and the engine refuses a start with
  the reason.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from condor import preflight
from condor.agents.config import AgentConfig
from condor.preflight import FAIL, PASS, UNKNOWN, PreflightFailed, run_preflight
from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.tools import executor_create

# The #277 case, at $390 a side: the strategy needs 780 USDC *in USDC*.
EQUAL_VALUE_ELSEWHERE = [
    {"token": "USDC", "units": 0.0, "available_units": 0.0, "value": 0.0},
    {"token": "SOL", "units": 2.6, "available_units": 2.6, "value": 390.0},
    {"token": "AVAX", "units": 48.75, "available_units": 48.75, "value": 390.0},
]
IN_USDC = [{"token": "USDC", "units": 780.0, "available_units": 780.0, "value": 780.0}]

AVAX_RULES = {
    "min_order_size": 0.01,
    "min_notional_size": 5.0,
    "min_price_increment": 0.001,
    "maker_fee": 0.001,
    "taker_fee": 0.00075,
}


class _Client:
    """The shape the preflight reads: connectors, accounts, portfolio, trading."""

    def __init__(
        self,
        rows=None,
        *,
        connector="binance",
        pair="SOL-USDC",
        rules=None,
        credentials=("binance",),
        position_mode="HEDGE",
        positions=None,
        price=150.0,
        connectors_error=None,
        fresh_rows=None,
    ):
        self.creates = []
        self.state_calls = []
        outer = self

        class _Connectors:
            @staticmethod
            async def list_connectors():
                if connectors_error is not None:
                    raise connectors_error
                return [connector, "kucoin"]

            @staticmethod
            async def get_trading_rules(connector_name, trading_pairs=None):
                return {trading_pairs[0]: rules} if rules else {}

        class _Accounts:
            @staticmethod
            async def list_account_credentials(account_name):
                return list(credentials)

        class _Portfolio:
            @staticmethod
            async def get_state(
                account_names=None, connector_names=None, refresh=False
            ):
                outer.state_calls.append(refresh)
                held = fresh_rows if (refresh and fresh_rows is not None) else rows
                return {account_names[0]: {connector_names[0]: list(held or [])}}

        class _Trading:
            @staticmethod
            async def get_position_mode(account_name, connector_name):
                # The endpoint's own wrapping: a value under "data".
                return {"data": {"position_mode": position_mode}}

            @staticmethod
            async def get_positions(account_names=None, connector_names=None):
                # GET /trading/positions answers {"data": [...]}.
                return {"data": list(positions or [])}

        class _MarketData:
            @staticmethod
            async def get_prices(connector_name, trading_pairs):
                pairs = (
                    [trading_pairs]
                    if isinstance(trading_pairs, str)
                    else list(trading_pairs)
                )
                return {"prices": {pairs[0]: price}} if price else {}

        class _Executors:
            @staticmethod
            async def create_executor(executor_config, account_name, controller_id):
                outer.creates.append(executor_config)
                return {"executor_id": "exec-1"}

        self.connectors = _Connectors()
        self.accounts = _Accounts()
        self.portfolio = _Portfolio()
        self.trading = _Trading()
        self.market_data = _MarketData()
        self.executors = _Executors()
        self.pair = pair


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(executor_create, "READ_BACK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(executor_create, "READ_BACK_WINDOW_SECONDS", 0.001)
    monkeypatch.setattr(
        executor_create.executor_preferences, "get_defaults", lambda _type: {}
    )
    trading_rules_cache.clear()
    yield
    trading_rules_cache.clear()


def _config(**requirements):
    """A loop config stating trading requirements, through AgentConfig itself."""
    stated = {
        "connector": "binance",
        "trading_pairs": ["SOL-USDC"],
        "capital_asset": "USDC",
        "capital_amount": 780.0,
    }
    stated.update(requirements)
    return AgentConfig(trading_requirements=stated).model_dump()


def _report(client, config=None):
    return asyncio.run(run_preflight(client, config or _config(), label="demo.loop_1"))


def _check(report, name):
    matching = [c for c in report.checks if c.name == name]
    assert matching, f"no {name!r} check in {[c.name for c in report.checks]}"
    return matching[0]


# ── Capital in the exact asset ───────────────────────────────────────────────


def test_capital_of_equal_total_value_in_other_tokens_fails():
    """#277: 780 USDC needed, $780 held — in SOL and AVAX."""
    report = _report(_Client(rows=EQUAL_VALUE_ELSEWHERE))

    capital = _check(report, "capital")
    assert capital.state == FAIL
    assert "needs 780 USDC" in capital.detail
    assert "only 0 USDC is available" in capital.detail
    assert "short 780 USDC" in capital.detail
    # What IS there, so the operator sees where the money went.
    assert "SOL 2.6" in capital.detail and "AVAX 48.75" in capital.detail
    assert "Total value is not the check" in capital.detail
    assert not report.ok
    assert "capital:" in report.reason


def test_the_same_requirement_held_in_the_exact_asset_passes():
    report = _report(_Client(rows=IN_USDC, rules=AVAX_RULES))

    assert _check(report, "capital").state == PASS
    assert "780 USDC available" in _check(report, "capital").detail
    assert report.ok


def test_a_stale_cached_shortfall_is_rechecked_before_it_refuses():
    """The create path's rule: a deposit the cache has not seen cannot refuse."""
    client = _Client(rows=EQUAL_VALUE_ELSEWHERE, fresh_rows=IN_USDC)

    report = _report(client)

    assert _check(report, "capital").state == PASS
    assert client.state_calls[:2] == [False, True]


def test_unreadable_balances_are_unchecked_not_a_pass():
    report = _report(_Client(rows=[]))

    capital = _check(report, "capital")
    assert capital.state == UNKNOWN
    assert "could not be read" in capital.detail
    # An unknown never grounds a loop.
    assert report.ok


# ── Account mode and leverage, read from the account ─────────────────────────


def test_position_mode_and_leverage_are_read_from_the_account():
    client = _Client(
        rows=IN_USDC,
        connector="binance_perpetual",
        credentials=("binance_perpetual",),
        position_mode="HEDGE",
        positions=[{"trading_pair": "SOL-USDC", "leverage": 5}],
    )
    config = _config(connector="binance_perpetual", position_mode="HEDGE", leverage=5.0)

    mode = _check(_report(client, config), "account mode")

    assert mode.state == PASS
    assert "HEDGE mode" in mode.detail and "leverage 5x" in mode.detail


def test_leverage_is_read_from_a_bare_position_list_too():
    """``get_open_positions`` hands back a list, not a ``{"data": ...}`` wrap."""
    rows = [{"trading_pair": "SOL-USDC", "leverage": 3}]

    assert preflight._account_leverage(rows, ("SOL-USDC",)) == 3
    assert preflight._account_leverage({"data": rows}, ("SOL-USDC",)) == 3
    assert preflight._account_leverage({"data": rows}, ("BTC-USDT",)) is None


def test_a_position_mode_mismatch_fails_the_preflight():
    client = _Client(
        rows=IN_USDC,
        connector="binance_perpetual",
        credentials=("binance_perpetual",),
        position_mode="one-way",  # the venue's spelling of ONEWAY
        positions=[{"trading_pair": "SOL-USDC", "leverage": 5}],
    )
    config = _config(connector="binance_perpetual", position_mode="HEDGE")

    report = _report(client, config)
    mode = _check(report, "account mode")

    assert mode.state == FAIL
    assert "requires HEDGE" in mode.detail and "ONEWAY mode" in mode.detail
    assert not report.ok


def test_a_stated_mode_is_normalized_but_nonsense_is_kept_as_unmeetable():
    assert preflight.requirements_of(
        _config(position_mode="one-way")
    ).position_mode == ("ONEWAY")
    # Not a position mode: kept, so it fails against the account rather than
    # quietly becoming "no requirement".
    assert preflight.requirements_of(_config(position_mode="long")).position_mode == (
        "LONG"
    )


def test_a_mode_requirement_on_a_spot_venue_is_a_failure():
    config = _config(position_mode="HEDGE")

    mode = _check(_report(_Client(rows=IN_USDC), config), "account mode")

    assert mode.state == FAIL
    assert "spot venue" in mode.detail


def test_a_spot_venue_with_no_mode_requirement_passes():
    mode = _check(_report(_Client(rows=IN_USDC)), "account mode")

    assert mode.state == PASS
    assert "no position mode or leverage" in mode.detail


# ── Venue rules, as values ───────────────────────────────────────────────────


def test_venue_rules_are_reported_per_pair():
    config = _config(trading_pairs=["SOL-USDC", "AVAX-USDC"])

    rules = _check(
        _report(_Client(rows=IN_USDC, rules=AVAX_RULES), config), "venue rules"
    )

    assert rules.state == PASS
    for pair in ("SOL-USDC", "AVAX-USDC"):
        assert pair in rules.detail
    assert rules.detail.count("min size 0.01") == 2
    assert "min notional $5.00" in rules.detail
    assert "tick 0.001" in rules.detail
    assert "maker 0.001" in rules.detail and "taker 0.00075" in rules.detail


def test_rules_a_venue_does_not_publish_are_unchecked():
    rules = _check(_report(_Client(rows=IN_USDC, rules=None)), "venue rules")

    assert rules.state == UNKNOWN
    assert "publishes no rules" in rules.detail


# ── Reachability: could not be checked is a third state ──────────────────────


def test_a_venue_that_cannot_be_reached_is_neither_a_pass_nor_a_failure():
    report = _report(
        _Client(rows=IN_USDC, connectors_error=OSError("connection refused"))
    )

    reachable = _check(report, "venue reachable")
    assert reachable.state == UNKNOWN
    assert reachable.state not in (PASS, FAIL)
    assert "could not be checked" in reachable.detail
    assert "connection refused" in reachable.detail
    assert report.ok  # unknown never refuses a start
    assert reachable in report.unchecked


def test_a_connector_the_api_does_not_list_is_reported_not_refused():
    """A gateway connector is listed by the gateway, so absence cannot refuse."""
    report = _report(_Client(rows=IN_USDC, connector="kraken"))

    reachable = _check(report, "venue reachable")
    assert reachable.state == UNKNOWN
    assert "'binance' is not among the 2 connectors" in reachable.detail
    assert report.ok


def test_a_missing_credential_is_reported_but_does_not_refuse_a_start():
    """A gateway connector trades from a wallet, so this cannot be a refusal."""
    report = _report(_Client(rows=IN_USDC, credentials=("kucoin",)))

    credential = _check(report, "connector credential")
    assert credential.state == UNKNOWN
    assert "lists no binance credential" in credential.detail
    assert "it has kucoin" in credential.detail
    assert report.ok


def test_the_clock_is_unchecked_when_the_client_exposes_no_session():
    assert _check(_report(_Client(rows=IN_USDC)), "clock").state == UNKNOWN


class _Dated:
    """An aiohttp-shaped session answering one ``Date`` header."""

    def __init__(self, date):
        self._date = date

    def get(self, _url):
        headers = {"Date": self._date} if self._date else {}
        response = SimpleNamespace(headers=headers)

        class _Ctx:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()


def _timed(date):
    client = _Client(rows=IN_USDC, rules=AVAX_RULES)
    client._session = _Dated(date)
    client.base_url = "http://api.test:8000"
    return client


def _http_date(offset_seconds):
    from email.utils import format_datetime

    stamp = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return format_datetime(stamp, usegmt=True)


def test_a_clock_in_step_with_the_api_host_passes():
    clock = _check(_report(_timed(_http_date(0))), "clock")

    assert clock.state == PASS
    assert "of the API host" in clock.detail


def test_a_clock_too_far_off_the_api_host_fails():
    report = _report(_timed(_http_date(-120)))

    clock = _check(report, "clock")
    assert clock.state == FAIL
    assert "off the API host" in clock.detail
    assert "one of the two clocks is wrong" in clock.detail
    assert not report.ok


def test_a_skew_the_measurement_cannot_pin_on_either_clock_is_reported():
    """Past the venue's recv window, inside what a bad round trip could explain."""
    report = _report(_timed(_http_date(-12)))

    clock = _check(report, "clock")
    assert clock.state == UNKNOWN
    assert "reported rather than refused" in clock.detail
    assert report.ok


def test_an_api_host_that_sends_no_date_is_unchecked():
    clock = _check(_report(_timed(None)), "clock")

    assert clock.state == UNKNOWN
    assert "no Date header" in clock.detail


# ── Pins ─────────────────────────────────────────────────────────────────────


def test_the_pins_are_the_server_resolution_actually_resolves_to(monkeypatch):
    import config_manager

    monkeypatch.setattr(
        config_manager, "resolve_stored_server", lambda *a, **kw: "moneymaker"
    )
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: object())
    config = dict(_config(), server_name="local", agent_key="claude-code")

    report = asyncio.run(run_preflight(_Client(rows=IN_USDC), config, user_id=7))

    pins = _check(report, "pins")
    assert pins.state == PASS
    assert "resolves to server 'moneymaker'" in pins.detail
    assert "model claude-code" in pins.detail


def test_a_pinned_server_that_cannot_be_used_fails_the_preflight(monkeypatch):
    import config_manager

    def _refuse(*_args, **_kwargs):
        raise config_manager.ServerUnavailable("server 'gone' is not available")

    monkeypatch.setattr(config_manager, "resolve_stored_server", _refuse)
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: object())

    report = asyncio.run(
        run_preflight(
            _Client(rows=IN_USDC),
            dict(_config(), server_name="gone"),
            user_id=7,
            server_pinned=True,
        )
    )

    pins = _check(report, "pins")
    assert pins.state == FAIL
    assert "server 'gone' is not available" in pins.detail
    assert not report.ok


def test_pins_without_a_user_say_so_rather_than_guessing():
    pins = _check(_report(_Client(rows=IN_USDC)), "pins")

    assert pins.state == UNKNOWN
    assert "no user context was given" in pins.detail


# ── One definition: the create path's own verdict ────────────────────────────


def _created_error(client, amount, side=1):
    result = asyncio.run(
        executor_create.create_position_executor(
            client,
            connector_name="binance",
            trading_pair="SOL-USDC",
            side=side,
            amount=amount,
        )
    )
    return result.get("error", "")


def test_the_preflight_min_size_verdict_is_the_create_path_verdict():
    """Asserted through the shared helpers: one definition, not two."""
    big_minimum = dict(AVAX_RULES, min_order_size=100.0)
    config = _config(capital_amount=780.0)  # 5.2 SOL at 150

    report = _report(_Client(rows=IN_USDC, rules=big_minimum), config)
    refusal = _created_error(_Client(rows=IN_USDC, rules=big_minimum), 780.0 / 150.0)

    viability = _check(report, "order viability")
    assert viability.state == FAIL
    assert refusal and viability.detail == refusal
    assert "below binance's minimum of 100" in viability.detail


def test_the_preflight_balance_verdict_is_the_create_path_verdict():
    config = _config(capital_amount=780.0)

    report = _report(_Client(rows=EQUAL_VALUE_ELSEWHERE), config)
    refusal = _created_error(_Client(rows=EQUAL_VALUE_ELSEWHERE), 780.0 / 150.0)

    viability = _check(report, "order viability")
    assert viability.state == FAIL
    assert refusal and viability.detail == refusal
    assert "Insufficient balance on binance" in viability.detail


def test_a_price_tick_between_the_two_reads_does_not_refuse_a_funded_start():
    """One price, used by both halves.

    The probe sized the order from a live price and ``balance_violation`` then
    priced that amount *again*: on an account holding exactly its stated
    capital, a tick up between the two reads invented a shortfall. 780 USDC at
    150 is 5.2 SOL; re-priced at 151 the check demanded 785.2 USDC and refused
    a start the account could fund. The price travels in the probe, so there is
    only ever one read to disagree with.
    """
    client = _Client(rows=IN_USDC, rules=AVAX_RULES)
    reads = []
    ticking = iter([150.0, 151.0])

    async def moving(connector_name, trading_pairs):
        pairs = (
            [trading_pairs] if isinstance(trading_pairs, str) else list(trading_pairs)
        )
        price = next(ticking, 151.0)
        reads.append(price)
        return {"prices": {pairs[0]: price}}

    client.market_data.get_prices = moving

    viability = _check(
        _report(client, _config(capital_amount=780.0)), "order viability"
    )

    assert viability.state == PASS
    assert "5.2 SOL buy" in viability.detail
    # And the second price was never needed: one read, carried.
    assert reads == [150.0]


def test_an_order_both_the_venue_and_the_account_accept_passes():
    report = _report(_Client(rows=IN_USDC, rules=AVAX_RULES), _config())

    viability = _check(report, "order viability")
    assert viability.state == PASS
    assert "5.2 SOL buy" in viability.detail


# ── Nothing stated, nothing gated ────────────────────────────────────────────


def test_a_loop_that_states_no_requirements_is_reported_on_and_not_gated():
    report = asyncio.run(
        run_preflight(_Client(rows=IN_USDC), AgentConfig().model_dump())
    )

    stated = _check(report, "trading requirements")
    assert stated.state == UNKNOWN
    assert "none stated" in stated.detail
    assert report.ok
    # No venue check ran at all: there was nothing to check against.
    assert [c.name for c in report.checks] == ["pins", "trading requirements"]


def test_a_run_with_no_api_client_reports_every_venue_check_as_unchecked():
    report = asyncio.run(run_preflight(None, _config()))

    assert report.ok
    assert "no API client" in _check(report, "trading requirements").detail


def test_the_capital_budget_fills_in_the_amount_for_the_pairs_quote():
    """``total_amount_quote`` is the requirement when no amount is stated."""
    config = AgentConfig(
        total_amount_quote=780.0,
        trading_requirements={
            "connector": "binance",
            "trading_pairs": ["SOL-USDC"],
        },
    ).model_dump()

    req = preflight.requirements_of(config)

    assert req.capital_asset == "USDC" and req.capital_amount == 780.0


def test_a_base_asset_requirement_does_not_inherit_the_quote_budget():
    config = AgentConfig(
        total_amount_quote=780.0,
        trading_requirements={
            "connector": "binance",
            "trading_pairs": ["SOL-USDC"],
            "capital_asset": "SOL",
        },
    ).model_dump()

    req = preflight.requirements_of(config)

    assert req.capital_asset == "SOL" and req.capital_amount is None


# ── The start path refuses, with the reason ──────────────────────────────────


def _engine(config, client):
    """A stand-in carrying only what ``_preflight_gate`` reads."""
    return SimpleNamespace(
        config=config,
        agent_id="demo.loop_1",
        strategy=None,
        agent=None,
        user_id=0,
        server_pinned=False,
        _get_client=lambda: _async(client),
    )


async def _async(value):
    return value


def test_a_start_is_refused_with_the_readable_reason():
    from condor.agents.engine import TickEngine

    engine = _engine(_config(), _Client(rows=EQUAL_VALUE_ELSEWHERE))

    with pytest.raises(PreflightFailed) as refused:
        asyncio.run(TickEngine._preflight_gate(engine))

    assert "needs 780 USDC" in str(refused.value)
    assert "capital" in refused.value.report.reason
    assert "✗ capital" in refused.value.report.render()


def test_a_start_whose_requirements_hold_is_not_refused():
    from condor.agents.engine import TickEngine

    engine = _engine(_config(), _Client(rows=IN_USDC, rules=AVAX_RULES))

    assert asyncio.run(TickEngine._preflight_gate(engine)) is None


def test_an_undeclared_loop_start_never_touches_the_api():
    from condor.agents.engine import TickEngine

    def _no_client():
        raise AssertionError("the gate must not read the API for an undeclared loop")

    engine = SimpleNamespace(
        config=AgentConfig().model_dump(),
        agent_id="demo.loop_1",
        _get_client=_no_client,
    )

    assert asyncio.run(TickEngine._preflight_gate(engine)) is None


def test_an_experiment_is_reported_on_but_never_gated():
    from condor.agents.engine import TickEngine

    config = _config()
    config["execution_mode"] = "dry_run"
    engine = _engine(config, _Client(rows=EQUAL_VALUE_ELSEWHERE))

    assert asyncio.run(TickEngine._preflight_gate(engine)) is None


# ── doctor --agent ───────────────────────────────────────────────────────────


def test_doctor_agent_runs_the_section_and_exits_non_zero(
    monkeypatch, capsys, tmp_path
):
    from condor import doctor

    agent = SimpleNamespace(slug="demo", created_by=0, agent_key="")
    strategy = SimpleNamespace(
        slug="loop", created_by=0, agent_key="", home=tmp_path, default_config=_config()
    )
    monkeypatch.setattr(doctor, "_agent_loops", lambda target: (agent, [strategy]))
    monkeypatch.setattr(
        doctor,
        "_agent_client",
        lambda config, user_id: _async(_Client(rows=EQUAL_VALUE_ELSEWHERE)),
    )

    exit_code = doctor.main(["--agent", "demo", "--only-agent"])
    out = capsys.readouterr().out

    assert "Trading preflight — demo.loop" in out
    assert "needs 780 USDC" in out
    assert exit_code == 1
    # Only the agent section ran.
    assert "Dependencies" not in out


def test_doctor_names_an_agent_it_cannot_find(monkeypatch):
    from condor import doctor

    monkeypatch.setattr(doctor, "_agent_loops", lambda target: (None, []))

    sections = doctor.check_agent_trading("ghost")

    assert [c.state for _, checks in sections for c in checks] == [doctor.FAIL]
    assert "no agent with slug 'ghost'" in sections[0][1][0].detail


def test_the_preflight_states_map_to_doctors_three_badges():
    from condor import doctor

    assert doctor._PREFLIGHT_STATES == {
        PASS: doctor.OK,
        FAIL: doctor.FAIL,
        UNKNOWN: doctor.WARN,
    }


# ── The start route answers 422, naming the reason ───────────────────────────


class _FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_server(self, server_name):
        return None

    def has_server_access(self, *a, **k):
        return False


def test_the_start_route_answers_422_with_the_preflight_reason(tmp_path, monkeypatch):
    """The refusal reaches the operator, not just the log (CORR-728's lesson)."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.agents import engine as engine_module
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore
    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: _FakeConfigManager()
    )

    report = preflight.PreflightReport(label="brigado.scalp_1")
    report.add("capital", FAIL, "this loop needs 780 USDC on binance but only 0 USDC")

    class _Refused:
        def __init__(self, **_kwargs):
            self.agent_id = "brigado.scalp_1"
            self.session_num = 1

        async def start(self):
            raise PreflightFailed(report)

    monkeypatch.setattr(engine_module, "TickEngine", _Refused)
    AgentStore().create(name="Brigado", description="BRL market making")
    StrategyStore().create(agent_slug="brigado", name="Scalp")

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=555, username="u", first_name="U", role="user"
    )

    resp = TestClient(app).post("/agents/brigado/strategies/scalp/start", json={})

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("Trading preflight failed: ")
    assert "needs 780 USDC" in detail
