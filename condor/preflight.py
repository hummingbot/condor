"""Can this loop trade? One readable answer, before capital is committed (FEAT-139).

``condor.doctor`` checks the *install*; the checks that look at a venue and an
account ran only when an executor was created, one order and one failure at a
time. So an agent started, traded for a while, and then discovered that its
capital was in the wrong token or its account in the wrong position mode. Issue
#277: one entry was blocked for hours because its strategy needed 780 USDC *in
USDC* while its capital sat in two other tokens of equal total value.

This module runs those checks ahead of the start, against what the loop *states*
it needs (``AgentConfig.trading_requirements``) rather than against one order,
and reports them together:

- **capital** in the exact asset, on the exact venue and account — a total-value
  check is what let the 780-USDC case pass;
- the account's **position mode and leverage**, read from the account rather than
  taken from config, which is the gap the setter at
  ``tools/trading.py`` leaves;
- the **venue's rules** per configured pair, as values rather than a verdict:
  whether a fee tier makes volume work viable is the operator's judgement;
- **clock skew, venue reachability and connector health**;
- the **pins** the session will really run with, through the resolution CORR-729
  made authoritative — a preflight that reported a pin the start path then
  silently replaced would be worse than reporting none.

Two rules shape the whole module:

**One definition of a validator.** The order-viability check calls
``executor_create.trading_rule_violation`` and ``balance_violation`` — the very
functions that gate a create — so the preflight's answer and the executor path's
answer cannot diverge. A copied validator drifts, and then the preflight passes
what the create path refuses.

**"Could not be checked" is not a pass.** A venue that cannot be reached, rules
a connector does not publish, an account whose balances will not read: each is
:data:`UNKNOWN`, reported as such and never folded into either verdict. Only a
:data:`FAIL` refuses a start, and only a requirement the loop actually stated can
produce one — a loop that states nothing is reported on and started.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

log = logging.getLogger(__name__)

#: A check's outcome. ``UNKNOWN`` is a third state on purpose, never a pass.
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

_BADGES = {PASS: "✓", FAIL: "✗", UNKNOWN: "?"}

#: Clock skew against the API host worth reporting: a venue rejects a signed
#: request outside its recv window, 5s on Binance. The ``Date`` header this is
#: measured from has one-second resolution and carries the round trip, so the
#: measurement itself is good to about a second — which is why this band is
#: reported rather than refused.
CLOCK_SKEW_WARN_SECONDS = 5.0

#: Skew that refuses the start. No round trip or header granularity explains
#: half a minute: one of the two clocks is wrong, and if it is the API host's
#: then every signed order will come back a signature error.
CLOCK_SKEW_FAIL_SECONDS = 30.0

#: Rule keys venues spell differently, in the order they are tried.
_MIN_SIZE_KEYS = ("min_order_size", "min_base_amount_increment")
_MIN_NOTIONAL_KEYS = ("min_notional_size", "min_notional")
_TICK_KEYS = ("min_price_increment", "price_increment", "tick_size")
_MAKER_FEE_KEYS = ("maker_fee", "maker_percent_fee_decimal", "maker_fee_rate")
_TAKER_FEE_KEYS = ("taker_fee", "taker_percent_fee_decimal", "taker_fee_rate")

_DEFAULT_ACCOUNT = "master_account"


def _gate():
    """The executor path's own validators — the one definition this reuses.

    Imported at call time: ``mcp_servers.hummingbot_api`` pulls its own client
    module, and this module is imported by the doctor CLI, which must not pay
    for that to print an install report.
    """
    from mcp_servers.hummingbot_api.tools import executor_create

    return executor_create


@dataclass
class PreflightCheck:
    """One line of the report: what was checked, how it came out, and why."""

    name: str
    state: str
    detail: str

    def render(self) -> str:
        return f"  {_BADGES.get(self.state, '?')} {self.name}: {self.detail}"


@dataclass
class PreflightReport:
    label: str = ""
    checks: list[PreflightCheck] = field(default_factory=list)

    def add(self, name: str, state: str, detail: str) -> PreflightCheck:
        check = PreflightCheck(name, state, detail)
        self.checks.append(check)
        return check

    @property
    def failures(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.state == FAIL]

    @property
    def unchecked(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.state == UNKNOWN]

    @property
    def ok(self) -> bool:
        """Nothing stated failed. An ``UNKNOWN`` does not refuse a start.

        A rules outage or an unreadable balance must not become a trading
        outage — the same fail-open rule the create path applies — so what an
        unknown buys is being *told* it was unknown.
        """
        return not self.failures

    @property
    def reason(self) -> str:
        """Why the preflight failed, in one line per failed check."""
        return "; ".join(f"{c.name}: {c.detail}" for c in self.failures)

    def render(self) -> str:
        """The whole report in plain language, one line per check."""
        head = (
            f"Trading preflight — {self.label}" if self.label else "Trading preflight"
        )
        lines = [head, *[c.render() for c in self.checks]]
        if self.failures:
            lines.append(
                f"  {len(self.failures)} check(s) failed: this loop cannot trade yet."
            )
        elif self.unchecked:
            lines.append(
                f"  {len(self.unchecked)} check(s) could not be run — not a pass."
            )
        return "\n".join(lines)


class PreflightFailed(RuntimeError):
    """A stated requirement is not met, so the start is refused.

    Carries the whole report, so a caller can show every line rather than only
    the reason it reports.
    """

    def __init__(self, report: PreflightReport):
        self.report = report
        super().__init__(report.reason or "trading preflight failed")


@dataclass(frozen=True)
class Requirements:
    """What a loop stated it needs, with the defaults filled in."""

    connector: str = ""
    pairs: tuple[str, ...] = ()
    account: str = _DEFAULT_ACCOUNT
    capital_asset: str = ""
    capital_amount: float | None = None
    position_mode: str = ""
    leverage: float | None = None

    @property
    def declared(self) -> bool:
        """Whether anything is stated to check against."""
        return bool(self.connector)

    @property
    def is_perp(self) -> bool:
        return "perpetual" in self.connector.lower()


def _quote_of(pair: str) -> str:
    _, _, quote = (pair or "").partition("-")
    return quote.strip().upper()


def _base_of(pair: str) -> str:
    base, _, _ = (pair or "").partition("-")
    return base.strip().upper()


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def requirements_of(config: Mapping[str, Any]) -> Requirements:
    """Read ``trading_requirements`` off a loop config, defaults resolved.

    Two defaults are worth naming because they are what makes the common config
    say something useful without a second place to edit:

    - ``capital_asset`` falls back to the **quote currency of the first pair**,
      which is the asset a quote-funded strategy actually has to hold;
    - ``capital_amount`` falls back to ``total_amount_quote`` — the session's
      capital budget — but only when the asset being checked *is* that quote
      currency. Comparing a quote budget against a base-asset holding would
      invent a requirement nobody stated.
    """
    raw: Any = config.get("trading_requirements") or {}
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        raw = dump()
    if not isinstance(raw, Mapping):
        raw = {}

    connector = str(raw.get("connector") or "").strip()
    pairs = tuple(
        str(p).strip().upper()
        for p in (raw.get("trading_pairs") or [])
        if str(p).strip()
    )
    quote = _quote_of(pairs[0]) if pairs else ""
    capital_asset = str(raw.get("capital_asset") or "").strip().upper() or quote

    capital_amount = _positive(raw.get("capital_amount"))
    if capital_amount is None and capital_asset and capital_asset == quote:
        capital_amount = _positive(config.get("total_amount_quote"))

    # A stated mode is normalized so that ``one-way`` and ``ONEWAY`` are the
    # same requirement; one that is not a position mode at all is kept verbatim
    # rather than dropped, so it reads as a requirement no account can meet
    # instead of silently becoming "no requirement".
    stated_mode = str(raw.get("position_mode") or "").strip()
    if stated_mode:
        from mcp_servers.hummingbot_api.tools.trading import normalize_position_mode

        stated_mode = normalize_position_mode(stated_mode) or stated_mode.upper()

    return Requirements(
        connector=connector,
        pairs=pairs,
        account=str(raw.get("account_name") or "").strip() or _DEFAULT_ACCOUNT,
        capital_asset=capital_asset,
        capital_amount=capital_amount,
        position_mode=stated_mode,
        leverage=_positive(raw.get("leverage")),
    )


# ── Pins ─────────────────────────────────────────────────────────────────────


async def _check_pins(
    report: PreflightReport,
    config: Mapping[str, Any],
    *,
    strategy: Any,
    agent: Any,
    user_id: int,
    server_pinned: bool,
) -> None:
    """Report the server and model this session will *actually* run with.

    Resolved through ``config_manager.resolve_stored_server``, the one rule
    CORR-729 made authoritative, with the same ``pinned`` flag the start path
    carries — so a pin that would refuse the start refuses here, by name, and an
    inherited name is shown as what it resolves to rather than as what it says.
    """
    from condor.agents.engine import resolve_agent_key

    declared = str(config.get("server_name") or "")
    agent_key = ""
    if strategy is not None and agent is not None:
        agent_key = resolve_agent_key(dict(config), strategy, agent)
    agent_key = agent_key or str(config.get("agent_key") or "")
    base_url = str(config.get("model_base_url") or "")
    model = f"model {agent_key or 'unset'}" + (f" via {base_url}" if base_url else "")

    if not user_id:
        report.add(
            "pins",
            UNKNOWN,
            f"server '{declared or 'inherited'}', {model} — the server a run "
            "resolves to is per-user and no user context was given, so this is "
            "what the config says, not what a start would use",
        )
        return

    from config_manager import (
        ServerUnavailable,
        get_config_manager,
        resolve_stored_server,
    )

    try:
        resolved = resolve_stored_server(
            get_config_manager(),
            user_id,
            declared,
            pinned=server_pinned,
            chat_id=user_id,
        )
    except ServerUnavailable as exc:
        report.add("pins", FAIL, f"{exc}")
        return
    except Exception as exc:  # a config_manager that cannot answer at all
        report.add("pins", UNKNOWN, f"server could not be resolved: {exc}")
        return

    if not resolved:
        report.add(
            "pins",
            FAIL,
            f"no server this run may use ({model}); a trading loop with no API "
            "server cannot trade",
        )
        return

    how = "pinned to" if server_pinned else "resolves to"
    inherited = "" if declared else " (none configured)"
    report.add("pins", PASS, f"{how} server '{resolved}'{inherited}, {model}")


# ── Reachability, clock ──────────────────────────────────────────────────────


async def _check_reachability(
    report: PreflightReport, client: Any, req: Requirements
) -> None:
    """Does this API server serve the venue, and does the account have a key for it?

    Reported, never refused. ``GET /connectors`` answers the API's *CEX*
    connectors (which is what ``fetchers.connectors`` intersects credentials
    against); a gateway/DEX connector is listed by ``gateway.list_connectors``
    and trades from a wallet, so an absence here cannot tell a misconfigured
    venue from a perfectly good on-chain one. What refuses a start is the gate
    that can be sure: the capital, the account mode, and the create path's own
    validators below.
    """
    connectors = getattr(client, "connectors", None)
    try:
        available = await connectors.list_connectors()
    except Exception as exc:
        report.add(
            "venue reachable", UNKNOWN, f"{req.connector} could not be checked: {exc}"
        )
        return
    if not isinstance(available, (list, tuple)) or not available:
        report.add(
            "venue reachable",
            UNKNOWN,
            f"{req.connector} could not be checked: the API listed no connectors",
        )
        return
    names = {str(name) for name in available}
    if req.connector not in names:
        report.add(
            "venue reachable",
            UNKNOWN,
            f"'{req.connector}' is not among the {len(names)} connectors "
            "GET /connectors lists. Expected for a gateway/DEX connector, which "
            "is listed by the gateway instead; for a CEX it means the venue is "
            "not built into this API server",
        )
        return
    report.add("venue reachable", PASS, f"{req.connector} is served by this API")

    accounts = getattr(client, "accounts", None)
    try:
        creds = await accounts.list_account_credentials(req.account)
    except Exception as exc:
        report.add(
            "connector credential",
            UNKNOWN,
            f"account '{req.account}' could not be read: {exc}",
        )
        return
    held = {str(name).replace(".yml", "") for name in creds or []}
    if req.connector not in held:
        # Deliberately not a refusal. A CEX connector missing from the account's
        # credentials is a real problem, but a gateway/DEX one trades from a
        # wallet (``accounts.list_gateway_wallets``) and legitimately has no
        # credential here — so this cannot tell a blocked start from a normal
        # one, and a check that cannot must not ground a working loop.
        report.add(
            "connector credential",
            UNKNOWN,
            f"account '{req.account}' lists no {req.connector} credential — it "
            f"has {', '.join(sorted(held)) or 'none'}. Normal for a gateway "
            "connector (those trade from a wallet); a missing CEX credential "
            "will fail every order with an auth error",
        )
        return
    report.add(
        "connector credential",
        PASS,
        f"account '{req.account}' holds a {req.connector} credential",
    )


async def _check_clock(report: PreflightReport, client: Any) -> None:
    """Skew between this host's clock and the API host that signs the requests.

    Measured off the API's own ``Date`` header, because the API host is what
    signs a venue request and a venue rejects one outside its recv window.

    Graded in three bands rather than two, because this measurement cannot tell
    *which* of the two clocks is wrong: within :data:`CLOCK_SKEW_WARN_SECONDS`
    is a pass, beyond it is reported as something to look at, and only beyond
    :data:`CLOCK_SKEW_FAIL_SECONDS` — which no round trip or header granularity
    explains — does it refuse a start.
    """
    session = getattr(client, "_session", None)
    base_url = str(getattr(client, "base_url", "") or "")
    if session is None or not base_url:
        report.add(
            "clock", UNKNOWN, "the API client exposes no session to time against"
        )
        return
    try:
        async with session.get(f"{base_url}/") as response:
            stamp = response.headers.get("Date")
        remote = parsedate_to_datetime(stamp) if stamp else None
    except Exception as exc:
        report.add("clock", UNKNOWN, f"the API host's clock could not be read: {exc}")
        return
    if remote is None:
        report.add("clock", UNKNOWN, "the API host sent no Date header to compare")
        return
    if remote.tzinfo is None:
        remote = remote.replace(tzinfo=timezone.utc)
    skew = (datetime.now(timezone.utc) - remote).total_seconds()
    disagree = (
        f"this host is {skew:+.1f}s off the API host, which is what signs a "
        f"venue request — a venue rejects one more than ~"
        f"{CLOCK_SKEW_WARN_SECONDS:g}s out of date"
    )
    if abs(skew) > CLOCK_SKEW_FAIL_SECONDS:
        report.add("clock", FAIL, f"{disagree}; one of the two clocks is wrong")
        return
    if abs(skew) > CLOCK_SKEW_WARN_SECONDS:
        report.add(
            "clock",
            UNKNOWN,
            f"{disagree}. Which side is wrong cannot be told from here, so this "
            "is reported rather than refused",
        )
        return
    report.add("clock", PASS, f"within {abs(skew):.1f}s of the API host")


# ── Capital ──────────────────────────────────────────────────────────────────


async def _check_capital(
    report: PreflightReport, client: Any, req: Requirements
) -> None:
    """Is the capital there, in the exact asset, on the exact venue and account?

    The check #277 is about. A shortfall is re-read with ``refresh=True`` before
    it fails, exactly as ``balance_violation`` does, so a cache that has not yet
    seen a deposit cannot refuse a start — and so the two answers agree.
    """
    gate = _gate()
    if not req.capital_asset or req.capital_amount is None:
        report.add(
            "capital",
            UNKNOWN,
            "no capital requirement stated (set trading_requirements.capital_asset "
            "and capital_amount, or configure a pair whose quote the budget is in)",
        )
        return

    asset, required = req.capital_asset, req.capital_amount
    unreadable = (
        f"balances for {req.connector} ({req.account}) could not be read, so the "
        f"{required:.8g} {asset} this loop needs is unconfirmed"
    )
    covered = (
        "{available:.8g} " + asset + f" available on {req.connector} "
        f"({req.account}) covers the {required:.8g} {asset} this loop needs"
    )

    rows = await gate.balance_rows(client, req.account, req.connector)
    if not rows:
        # No rows at all is "unknown", not "empty" — the create path's own
        # reading: the connector may just be missing from the API's state.
        report.add("capital", UNKNOWN, unreadable)
        return
    available = gate.available_units(rows, asset)
    if available >= required:
        report.add("capital", PASS, covered.format(available=available))
        return

    # Only a shortfall pays for the exchange round trip, and only the fresh read
    # may refuse — a cache that has not seen a deposit must never block a start.
    rows = await gate.balance_rows(client, req.account, req.connector, refresh=True)
    if not rows:
        report.add("capital", UNKNOWN, unreadable)
        return
    available = gate.available_units(rows, asset)
    if available >= required:
        report.add("capital", PASS, covered.format(available=available))
        return
    report.add(
        "capital",
        FAIL,
        f"this loop needs {required:.8g} {asset} on {req.connector} but only "
        f"{available:.8g} {asset} is available — short {required - available:.8g} "
        f"{asset}. {gate.describe_balances(req.connector, rows)}. Total value is "
        f"not the check: the capital has to be in {asset}",
    )


# ── Account mode ─────────────────────────────────────────────────────────────


def _account_leverage(positions: Any, pairs: tuple[str, ...]) -> float | None:
    """Leverage the account is actually running, off its open positions.

    ``GET /trading/positions`` answers ``{"data": [...]}``, which is how
    ``tools/trading.get_positions`` reads it; a bare list is accepted too, since
    the client's ``get_open_positions`` shortcut hands one back.
    """
    gate = _gate()
    if isinstance(positions, dict):
        positions = positions.get("data")
    rows = positions if isinstance(positions, list) else []
    wanted = set(pairs)
    for row in rows:
        if not isinstance(row, dict):
            continue
        pair = str(row.get("trading_pair") or "").upper()
        if wanted and pair not in wanted:
            continue
        value = gate.numeric_field(row, "leverage")
        if value is not None:
            return value
    return None


async def _check_account_mode(
    report: PreflightReport, client: Any, req: Requirements
) -> None:
    """Read the account's position mode and leverage, and hold them to the loop's.

    Read from the *account*: config says what was asked for once, and a wrong
    hedge/one-way mode is what turned an intended exit into an opening order
    during the race. A value that is merely unreported (no open position to read
    a leverage off) does not degrade the check unless the loop requires it —
    reporting "unknown" for something nobody asked about would train an operator
    to ignore the line.
    """
    from mcp_servers.hummingbot_api.tools.trading import normalize_position_mode

    if not req.is_perp:
        if req.position_mode or req.leverage is not None:
            report.add(
                "account mode",
                FAIL,
                f"this loop requires "
                f"{req.position_mode or f'{req.leverage:g}x leverage'} but "
                f"{req.connector} is a spot venue, which has neither a position "
                "mode nor leverage",
            )
            return
        report.add(
            "account mode",
            PASS,
            f"{req.connector} is spot — no position mode or leverage to set",
        )
        return

    trading = getattr(client, "trading", None)
    try:
        payload = await trading.get_position_mode(req.account, req.connector)
    except Exception as exc:
        payload, mode_error = None, str(exc)
    else:
        mode_error = ""
    # The endpoint's own wrapping varies by API version ("position-mode" answers
    # the value, a dict, or a dict under "data"), and an unrecognized shape reads
    # as unreported rather than as a mismatch.
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, str)):
        payload = payload["data"]
    mode = ""
    if isinstance(payload, dict):
        for key in ("position_mode", "mode", "value"):
            mode = normalize_position_mode(payload.get(key))
            if mode:
                break
    elif isinstance(payload, str):
        mode = normalize_position_mode(payload)

    try:
        positions = await trading.get_positions(
            account_names=[req.account], connector_names=[req.connector]
        )
    except Exception as exc:
        log.debug(
            "positions for %s/%s unavailable: %s", req.account, req.connector, exc
        )
        positions = None
    leverage = _account_leverage(positions, req.pairs)

    mode_said = mode or (f"unreadable ({mode_error})" if mode_error else "unreported")
    lev_said = f"{leverage:g}x" if leverage is not None else "no open position to read"
    reported = f"account '{req.account}' is in {mode_said} mode, leverage {lev_said}"

    if req.position_mode and mode and mode != req.position_mode:
        report.add(
            "account mode",
            FAIL,
            f"this loop requires {req.position_mode} but {reported} — an order "
            "meant to close would open instead",
        )
        return
    if req.position_mode and not mode:
        report.add("account mode", UNKNOWN, f"{reported}; required {req.position_mode}")
        return
    if req.leverage is not None and leverage is not None and leverage != req.leverage:
        report.add(
            "account mode",
            FAIL,
            f"this loop requires {req.leverage:g}x leverage but {reported}",
        )
        return
    if req.leverage is not None and leverage is None:
        report.add("account mode", UNKNOWN, f"{reported}; required {req.leverage:g}x")
        return
    report.add("account mode", PASS, reported)


# ── Venue rules ──────────────────────────────────────────────────────────────


def _fee_line(gate: Any, rules: Mapping[str, Any], connector: str) -> str:
    maker = gate.numeric_field(rules, *_MAKER_FEE_KEYS)
    taker = gate.numeric_field(rules, *_TAKER_FEE_KEYS)
    tier = rules.get("fee_tier")
    if maker is None and taker is None and not tier:
        return f"fees not published by {connector}"
    parts = []
    if tier:
        parts.append(f"tier {tier}")
    if maker is not None:
        parts.append(f"maker {maker:g}")
    if taker is not None:
        parts.append(f"taker {taker:g}")
    return ", ".join(parts)


async def _check_venue_rules(
    report: PreflightReport, client: Any, req: Requirements
) -> None:
    """Report the venue's rules per pair as *values*, not as a verdict.

    #277's fee complaint — pairs whose fees made volume work unviable — is a
    judgement the operator makes, so this prints what the venue says and refuses
    nothing. Rules a connector does not publish (every gateway/DEX one) are
    ``UNKNOWN``, which is also what keeps a rules outage from reading as a pass.
    """
    if not req.pairs:
        report.add(
            "venue rules",
            UNKNOWN,
            "no trading pairs configured, so no venue rules to report",
        )
        return

    gate = _gate()
    from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache

    lines, missing = [], []
    for pair in req.pairs:
        try:
            rules = await trading_rules_cache.get(client, req.connector, pair)
        except Exception as exc:
            log.debug("rules for %s %s unavailable: %s", req.connector, pair, exc)
            rules = None
        if not isinstance(rules, dict) or not rules:
            missing.append(pair)
            continue
        min_size = gate.numeric_field(rules, *_MIN_SIZE_KEYS)
        min_notional = gate.numeric_field(rules, *_MIN_NOTIONAL_KEYS)
        tick = gate.numeric_field(rules, *_TICK_KEYS)
        lines.append(
            f"{pair} min size "
            f"{f'{min_size:g} {_base_of(pair)}' if min_size else 'unstated'}, "
            f"min notional {gate.usd(min_notional) if min_notional else 'unstated'}, "
            f"tick {f'{tick:g}' if tick else 'unstated'}, "
            f"{_fee_line(gate, rules, req.connector)}"
        )

    if missing:
        lines.append(
            f"{', '.join(missing)}: {req.connector} publishes no rules — could "
            "not be checked"
        )
    report.add("venue rules", UNKNOWN if missing else PASS, "; ".join(lines))


# ── Order viability: the create path's own verdict, early ────────────────────


async def _probe_order(client: Any, req: Requirements) -> dict[str, Any] | None:
    """The order this loop's stated capital would place, for the validators.

    Sized from the capital: a SELL when the capital is the pair's base asset, a
    BUY otherwise, converted at the venue's own price through the create path's
    :func:`reference_price`. ``None`` when there is not enough stated to size
    one — there is then nothing to ask the validators.

    The price that sized the order travels **in** the probe, as the
    ``entry_price`` ``reference_price`` reads back, so the validators price the
    amount at the price it was derived from. They would otherwise ask the venue
    again: an account holding exactly its stated capital — 780 USDC at 150,
    sized to 5.2 SOL — then fails its own balance check on a tick up to 151,
    which demands 785.2 USDC. The shortfall is the two reads disagreeing, not
    the account, and refusing a funded start over it is the false negative a
    preflight must not have.
    """
    if not req.pairs or req.capital_amount is None:
        return None
    pair = req.pairs[0]
    base = _base_of(pair)
    if not base or not _quote_of(pair):
        return None

    price = None
    if req.capital_asset == base:
        amount, side = req.capital_amount, 2  # SELL the base it holds
    else:
        price = await _gate().reference_price(client, req.connector, pair, {})
        if price is None:
            return None
        amount, side = req.capital_amount / price, 1  # BUY with the quote it holds

    probe = {
        "connector_name": req.connector,
        "trading_pair": pair,
        "side": side,
        "amount": amount,
    }
    if price is not None:
        probe["entry_price"] = price
    return probe


async def _check_order_viability(
    report: PreflightReport, client: Any, req: Requirements
) -> None:
    """Ask the create path's own validators about the order this loop would place.

    Not a second opinion — the same two functions ``create_executor`` refuses
    with, so what passes here passes there. The refusal text is reported
    verbatim for the same reason: it already names the minimum that was missed.
    """
    probe = await _probe_order(client, req)
    if probe is None:
        report.add(
            "order viability",
            UNKNOWN,
            "not enough stated to size the order this loop would place "
            "(needs a pair, a capital amount and a venue price)",
        )
        return

    gate = _gate()
    pair, amount = probe["trading_pair"], probe["amount"]
    side = "sell" if probe["side"] == 2 else "buy"
    try:
        violation = await gate.trading_rule_violation(
            client, "position_executor", probe
        )
        shortfall = await gate.balance_violation(
            client, "position_executor", probe, req.account
        )
    except Exception as exc:
        report.add(
            "order viability",
            UNKNOWN,
            f"the venue's rules and the account's balance could not be checked "
            f"against a {amount:.8g} {_base_of(pair)} {side}: {exc}",
        )
        return

    problem = violation or shortfall
    if problem:
        report.add("order viability", FAIL, problem)
        return
    # Named precisely: ``balance_violation`` leaves a perpetual to the
    # executor's own collateral check, so claiming its balance was cleared
    # would be claiming a check that did not run.
    cleared = (
        f"{req.connector}'s rules (a perpetual's collateral is the executor's "
        "own check)"
        if req.is_perp
        else f"{req.connector}'s rules and the account's balance"
    )
    report.add(
        "order viability",
        PASS,
        f"a {amount:.8g} {_base_of(pair)} {side} on {pair} clears {cleared}",
    )


# ── Entry point ──────────────────────────────────────────────────────────────


async def run_preflight(
    client: Any,
    config: Mapping[str, Any],
    *,
    label: str = "",
    strategy: Any = None,
    agent: Any = None,
    user_id: int = 0,
    server_pinned: bool = False,
) -> PreflightReport:
    """Every check this loop's own config gives something to check, in one report.

    ``client`` may be ``None``: the pins still resolve and everything that needs
    the API is reported as unchecked, which is the honest answer for a run that
    has no server yet. A config that states no ``trading_requirements`` gets the
    pins and a line saying so — nothing fails, because there is nothing stated to
    fail against.
    """
    report = PreflightReport(label=label)
    try:
        await _check_pins(
            report,
            config,
            strategy=strategy,
            agent=agent,
            user_id=user_id,
            server_pinned=server_pinned,
        )
    except Exception as exc:
        log.exception("preflight: pins check failed")
        report.add("pins", UNKNOWN, f"could not be resolved: {exc}")

    req = requirements_of(config)
    if not req.declared:
        report.add(
            "trading requirements",
            UNKNOWN,
            "none stated, so nothing was checked against a venue or an account "
            "— set trading_requirements.connector and trading_pairs to have the "
            "capital, account mode and venue rules checked before a start",
        )
        return report

    if client is None:
        report.add(
            "trading requirements",
            UNKNOWN,
            f"{req.connector} {', '.join(req.pairs) or '(no pairs)'} could not be "
            "checked: this run has no API client",
        )
        return report

    for name, run in (
        ("venue reachable", lambda: _check_reachability(report, client, req)),
        ("clock", lambda: _check_clock(report, client)),
        ("capital", lambda: _check_capital(report, client, req)),
        ("account mode", lambda: _check_account_mode(report, client, req)),
        ("venue rules", lambda: _check_venue_rules(report, client, req)),
        ("order viability", lambda: _check_order_viability(report, client, req)),
    ):
        try:
            await run()
        except Exception as exc:
            # A check that crashes is unchecked, never a pass and never a
            # refusal: a bug in the preflight must not ground a working loop.
            log.exception("preflight: %s check failed", name)
            report.add(name, UNKNOWN, f"could not be checked: {exc}")
    return report


async def gate_start(
    client: Any,
    config: Mapping[str, Any],
    *,
    label: str = "",
    strategy: Any = None,
    agent: Any = None,
    user_id: int = 0,
    server_pinned: bool = False,
) -> PreflightReport:
    """Run the preflight and raise :class:`PreflightFailed` on a stated failure.

    What a start path calls. Returns the report when nothing stated failed, so
    the caller can log or show the unchecked lines too.
    """
    report = await run_preflight(
        client,
        config,
        label=label,
        strategy=strategy,
        agent=agent,
        user_id=user_id,
        server_pinned=server_pinned,
    )
    if not report.ok:
        raise PreflightFailed(report)
    return report
