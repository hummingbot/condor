"""Risk engine -- pre-tick validation and guardrails.

Enforces position limits, daily loss caps, drawdown limits, executor counts,
and LLM cost caps.  Also provides a permission callback that auto-approves
safe tool calls and blocks dangerous ones that violate risk limits.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from condor.runtime.danger import (
    DANGEROUS_AMM_ACTIONS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_CLMM_ACTIONS,
    DANGEROUS_SWAP_ACTIONS,
    is_dangerous_tool_call,
    tool_call_input,
    tool_call_name,
)

if TYPE_CHECKING:
    from .ownership import BotLedger

log = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_position_size_quote: float = 500.0
    max_open_executors: int = 5
    max_drawdown_pct: float = -1.0
    # Hard kill-switch: a deeper drawdown than the soft ``max_drawdown_pct`` pause;
    # breaching it winds down positions (see condor.agents.shutdown). -1 = disabled.
    shutdown_drawdown_pct: float = -1.0

    @classmethod
    def from_dict(cls, d: dict) -> RiskLimits:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RiskState:
    total_exposure: float = 0.0
    executor_count: int = 0
    drawdown_pct: float = 0.0
    is_blocked: bool = False
    block_reason: str = ""
    # Hard escalation: set when the shutdown drawdown threshold is breached. The
    # soft ``is_blocked`` only pauses the tick; this triggers an emergency winddown.
    should_shutdown: bool = False
    shutdown_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_exposure": self.total_exposure,
            "executor_count": self.executor_count,
            "drawdown_pct": self.drawdown_pct,
            "is_blocked": self.is_blocked,
            "block_reason": self.block_reason,
            "should_shutdown": self.should_shutdown,
            "shutdown_reason": self.shutdown_reason,
            # Include limits for prompt display
            "max_position_size": (
                self._limits.max_position_size_quote
                if hasattr(self, "_limits")
                else 500
            ),
            "max_open_executors": (
                self._limits.max_open_executors if hasattr(self, "_limits") else 5
            ),
            "max_drawdown_pct": (
                self._limits.max_drawdown_pct if hasattr(self, "_limits") else -1
            ),
            "shutdown_drawdown_pct": (
                self._limits.shutdown_drawdown_pct if hasattr(self, "_limits") else -1
            ),
        }


#: The signing actions of each Gateway tool, by tool name. These are the
#: DANGEROUS_* sets the confirmation gate already uses: loop mode stands in for
#: the human those sets would otherwise put in front of the call, so it has to
#: agree with them exactly or the two gates disagree about the same signature.
_SIGNING_DEX_ACTIONS = {
    "manage_gateway_swaps": DANGEROUS_SWAP_ACTIONS,
    "manage_clmm": DANGEROUS_CLMM_ACTIONS,
    "manage_amm": DANGEROUS_AMM_ACTIONS,
}

#: Signing actions that return capital instead of committing it. Allowed even
#: under a breached limit: refusing them would trap a loop agent in a position
#: it is no longer permitted to unwind.
RISK_REDUCING_DEX_ACTIONS = frozenset({"remove_liquidity", "close", "collect_fees"})


class RiskEngine:
    """Evaluates risk state and can block snapshots or individual tool calls."""

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def get_state(self, tracker: Any) -> RiskState:
        """Compute current risk metrics from tracker data."""
        state = RiskState()
        state._limits = self.limits

        try:
            state.total_exposure = tracker.get_total_exposure()
            state.executor_count = tracker.get_open_executor_count()
            state.drawdown_pct = tracker.get_drawdown_pct()
        except Exception as exc:
            log.exception("Failed to compute risk state from tracker")
            # Fail closed: without real metrics we must not approve creates
            # against zeroed exposure/count. A blocked state makes the engine
            # pause the tick and notify instead of trading blind.
            state.is_blocked = True
            state.block_reason = f"risk state unavailable: {exc}"
            return state

        # Check blocking conditions
        reasons = []

        if (
            self.limits.max_drawdown_pct >= 0
            and state.drawdown_pct > self.limits.max_drawdown_pct
        ):
            reasons.append(
                f"Drawdown {state.drawdown_pct:.1f}% exceeds limit {self.limits.max_drawdown_pct:.1f}%"
            )

        if reasons:
            state.is_blocked = True
            state.block_reason = "; ".join(reasons)

        # Hard kill-switch: a deeper drawdown than the soft pause. Evaluated
        # independently so a breach escalates to a winddown even though it also
        # trips the soft block (the engine checks should_shutdown first).
        if (
            self.limits.shutdown_drawdown_pct >= 0
            and state.drawdown_pct > self.limits.shutdown_drawdown_pct
        ):
            state.should_shutdown = True
            state.shutdown_reason = (
                f"Drawdown {state.drawdown_pct:.1f}% exceeds shutdown limit "
                f"{self.limits.shutdown_drawdown_pct:.1f}%"
            )

        return state

    def check_executor_action(
        self,
        tool_call: dict,
        current_state: RiskState,
        planned_amount_quote: float | None = None,
    ) -> tuple[bool, str]:
        """Check if an executor creation is within risk limits.

        On approval of a "create", accumulates it into ``current_state``
        (executor count and exposure) so subsequent checks within the same
        tick see the running totals instead of the frozen per-tick snapshot.
        The state is recomputed from the journal at the start of each tick.

        Returns (allowed, reason).
        """
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"
        action = input_data.get("action", "")

        # Only gate "create" actions
        if action != "create":
            return True, ""

        # Check executor count
        if current_state.executor_count >= self.limits.max_open_executors:
            return (
                False,
                f"Max open executors ({self.limits.max_open_executors}) reached",
            )

        if (
            planned_amount_quote is None
            or not math.isfinite(planned_amount_quote)
            or planned_amount_quote <= 0
        ):
            return False, "Planned quote exposure is unavailable"
        amount = planned_amount_quote

        if current_state.total_exposure + amount > self.limits.max_position_size_quote:
            return False, (
                f"Would exceed position limit: ${current_state.total_exposure + amount:.2f} > "
                f"${self.limits.max_position_size_quote:.2f}"
            )

        # Approved: accumulate into the snapshot so the next create in this
        # tick is gated against the running totals, not the pre-tick numbers.
        current_state.executor_count += 1
        current_state.total_exposure += amount

        return True, ""

    def check_bot_action(self, tool_call: dict) -> tuple[bool, str]:
        """Check a manage_bots call against risk limits.

        A bot's capital lives in saved controller configs on the API server,
        so exposure can't be computed from the tool inputs alone. Instead,
        bound the loss: a deploy must declare ``max_global_drawdown_quote``
        (the platform-enforced kill switch) no larger than the strategy's
        position limit. Stops are risk-reducing and always allowed; an
        ``update_config`` is only gated when it declares a
        ``total_amount_quote`` above the position limit.

        Returns (allowed, reason).
        """
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"
        action = input_data.get("action", "")

        if action == "deploy":
            cap = input_data.get("max_global_drawdown_quote")
            if not cap:
                return False, (
                    "Bot deploy must declare max_global_drawdown_quote "
                    f"(≤ ${self.limits.max_position_size_quote:.2f}) so the "
                    "platform kill switch bounds the loss"
                )
            if float(cap) > self.limits.max_position_size_quote:
                return False, (
                    f"max_global_drawdown_quote ${float(cap):.2f} exceeds "
                    f"position limit ${self.limits.max_position_size_quote:.2f}"
                )
        elif action == "update_config":
            amount = float(
                (input_data.get("config_data") or {}).get("total_amount_quote", 0) or 0
            )
            if amount > self.limits.max_position_size_quote:
                return False, (
                    f"update_config total_amount_quote ${amount:.2f} exceeds "
                    f"position limit ${self.limits.max_position_size_quote:.2f}"
                )

        return True, ""

    def check_dex_action(
        self,
        tool_call: dict,
        current_state: RiskState,
        notional_quote: float | None = None,
    ) -> tuple[bool, str]:
        """Check a signing DEX call against the position limit.

        The Gateway tools sign straight from the user's wallet -- no executor,
        no controller, no saved config in the path -- so this gate is the only
        thing bounding a loop agent's DEX capital. Three tools reach it:
        ``manage_gateway_swaps`` signs a swap, ``manage_clmm`` and
        ``manage_amm`` move liquidity. Anything outside their
        ``DANGEROUS_*_ACTIONS`` (quotes, pool and position reads, the guide
        load) is not a signature and passes through untouched.

        ``remove_liquidity``, ``close`` and ``collect_fees`` withdraw capital
        rather than commit it, so they are allowed even under a breached limit
        -- the same reasoning that lets a bot stop through
        ``check_bot_action``.

        ``notional_quote`` is the call valued in the pool's quote token by
        :func:`_amm_notional_quote`; the callback prices it, exactly as it does
        for an executor create. ``None`` or a non-finite/non-positive value
        means the call could not be valued, and an unpriced signature is
        refused rather than approved blind (SEC-093).

        On approval the notional accumulates into ``current_state`` the way
        :meth:`check_executor_action` does, so a second swap in the same tick
        is gated against the running total and not the frozen snapshot.

        Returns (allowed, reason).
        """
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"
        action = input_data.get("action", "")

        if action not in _SIGNING_DEX_ACTIONS.get(
            tool_call_name(tool_call), frozenset()
        ):
            return True, ""

        if action in RISK_REDUCING_DEX_ACTIONS:
            return True, ""

        if (
            notional_quote is None
            or not math.isfinite(notional_quote)
            or notional_quote <= 0
        ):
            return False, f"DEX {action} quote notional is unavailable"

        projected = current_state.total_exposure + notional_quote
        if projected > self.limits.max_position_size_quote:
            return False, (
                f"DEX {action} would exceed position limit: ${projected:.2f} > "
                f"${self.limits.max_position_size_quote:.2f}"
            )

        # Approved: accumulate so the next signature in this tick is gated
        # against the running total, not the pre-tick numbers.
        current_state.total_exposure += notional_quote

        return True, ""


def auto_approve_with_risk_check(
    risk_engine: RiskEngine,
    risk_state: RiskState,
    execution_mode: str = "loop",
    ledger: "BotLedger | None" = None,
    agent_id: str = "",
    price_client: Any = None,
):
    """Build a permission callback that auto-approves safe tools and risk-checks dangerous ones.

    ``ledger`` (FEAT-017) scopes bot ownership: with one, a ``manage_bots`` action
    that deploys or mutates a bot outside the session's namespace is cancelled and
    recorded. ``None`` (consults, delegations, chat, executor-mode agents) keeps
    today's behavior exactly.

    ``agent_id``, when given, is the session's own ``controller_id`` tag and an
    executor create must carry exactly it. The tag is model-supplied (the prompt
    merely asks for it) and is the sole link between a real position and the
    session that opened it, so checking only that *some* tag is present lets a
    mistyped one open a live position no session can ever claim. Empty (consults,
    chat, tests) keeps the presence-only check.
    """

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            tool_name = tool_call_name(tool_call)

            # A dangerous tool whose arguments we can't read can't be risk-checked
            # either, so it never runs unattended: cancel instead of falling
            # through to the auto-approve tail (SEC-093).
            input_data = tool_call_input(tool_call)
            if input_data is None:
                log.warning("Blocked %s: tool arguments could not be read", tool_name)
                return {"outcome": {"outcome": "cancelled"}}

            # Dry-run mode: block ALL mutating actions.
            #
            # Everything here is already inside `is_dangerous_tool_call`, which
            # did the per-action matching against DANGEROUS_*_ACTIONS. Repeating
            # it per tool was not just redundant, it reopened SEC-093: that gate
            # fails CLOSED on an unreadable action, so `manage_clmm` with a
            # missing, null or non-string `action` arrives here as dangerous —
            # and the re-check then read it as "" , matched no set, fell through
            # every branch and hit the auto-approve tail. A malformed write
            # executed for real in the one mode whose whole promise is that
            # nothing does. Reaching this line is the decision; blocking is
            # unconditional.
            if execution_mode == "dry_run":
                log.info(
                    "Dry-run mode: blocked %s(%s)",
                    tool_name,
                    input_data.get("action", "") or "?",
                )
                return {"outcome": {"outcome": "cancelled"}}

            # For executor actions, run risk check
            if tool_name == "manage_executors":
                action = input_data.get("action", "")

                # Validate controller_id on create — presence AND value, since
                # this tag is what per-session PnL attribution keys on.
                if action == "create":
                    executor_config = input_data.get("executor_config", {})
                    tag = str(executor_config.get("controller_id") or "")
                    if not tag:
                        log.warning("Blocked executor create: missing controller_id")
                        return {"outcome": {"outcome": "cancelled"}}
                    if agent_id and tag != agent_id:
                        log.warning(
                            "Blocked executor create: controller_id %r is not this "
                            "session's agent_id %r — the position would be "
                            "unattributable",
                            tag,
                            agent_id,
                        )
                        return {"outcome": {"outcome": "cancelled"}}

                planned_amount_quote = None
                if action == "create":
                    try:
                        planned_amount_quote = await _planned_amount_quote(
                            input_data, price_client
                        )
                    except Exception as exc:
                        log.warning("Blocked executor create: %s", exc)
                        return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_executor_action(
                    tool_call, risk_state, planned_amount_quote
                )
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
                    return {"outcome": {"outcome": "cancelled"}}

            # Bot deploys place real capital via controllers — bound the loss
            # (declared drawdown kill switch) since the amount isn't in the call
            if tool_name == "manage_bots":
                # Ownership first: an agent may only touch bots in its own
                # namespace. Read-only actions (status/logs/get_config) are not
                # in DANGEROUS_BOT_ACTIONS, so it still sees the whole fleet.
                # Only a session that declared controller mode is held to the
                # namespace; every session still has its deploys recorded below.
                if ledger is not None and ledger.enforced:
                    action = input_data.get("action", "")
                    if action in DANGEROUS_BOT_ACTIONS:
                        bot_name = input_data.get("bot_name", "") or ""
                        if not ledger.owns(bot_name):
                            log.warning(
                                "Ownership: blocked manage_bots(%s) on '%s' "
                                "(namespace %s)",
                                action,
                                bot_name,
                                ledger.namespace,
                            )
                            ledger.note_violation(bot_name, action)
                            return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_bot_action(tool_call)
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
                    return {"outcome": {"outcome": "cancelled"}}

                # Recorded only once the call is actually going through, so a
                # risk-rejected deploy never lands in the ledger.
                if ledger is not None:
                    if input_data.get("action", "") == "deploy":
                        ledger.note_deploy(input_data.get("bot_name", "") or "")

            # Gateway signatures move funds straight out of the user's wallet,
            # so they are priced and gated here (SEC-224). Interactive surfaces
            # still route the same call to a human via confirmations.py; this
            # branch is what stands in for that human in loop mode.
            if tool_name in _SIGNING_DEX_ACTIONS:
                action = input_data.get("action", "")
                notional_quote = None
                if (
                    action in _SIGNING_DEX_ACTIONS[tool_name]
                    and action not in RISK_REDUCING_DEX_ACTIONS  # unpriced
                ):
                    try:
                        notional_quote = await _dex_notional_quote(
                            tool_name, input_data, price_client
                        )
                    except Exception as exc:
                        log.warning("Blocked %s(%s): %s", tool_name, action, exc)
                        return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_dex_action(
                    tool_call, risk_state, notional_quote
                )
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
                    return {"outcome": {"outcome": "cancelled"}}

            # Block direct order placement entirely
            if tool_name == "place_order":
                log.warning("Blocked direct place_order (agents must use executors)")
                return {"outcome": {"outcome": "cancelled"}}

        # Auto-approve everything else
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}

    return callback


async def _planned_amount_quote(input_data: dict[str, Any], client: Any) -> float:
    """Value a supported executor create in its quote token."""

    config = input_data.get("executor_config") or {}
    executor_type = (
        input_data.get("executor_type")
        or config.get("type")
        or config.get("executor_type")
    )

    if executor_type == "dca_executor":
        amounts = [float(value) for value in config.get("amounts_quote", [])]
        if any(not math.isfinite(value) or value <= 0 for value in amounts):
            raise ValueError("amounts_quote must contain positive finite numbers")
        amount = sum(amounts)
    elif executor_type in {"order_executor", "position_executor", "lp_executor"}:
        base = float(
            config.get("base_amount", 0)
            if executor_type == "lp_executor"
            else config.get("amount", 0)
        )
        quote = float(config.get("quote_amount", 0))
        if base < 0 or quote < 0:
            raise ValueError("executor amounts cannot be negative")
        if base:
            if client is None:
                raise ValueError("Hummingbot price client is unavailable")
            from condor.fetchers.market_data import fetch_current_price

            price = await fetch_current_price(
                client,
                config.get("connector_name", ""),
                config.get("trading_pair", ""),
            )
            if price is None:
                raise ValueError("reference price is unavailable")
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("reference price must be a positive finite number")
            amount = quote + base * price
        else:
            amount = quote
    elif executor_type in {"grid_executor", "twap_executor"}:
        amount = float(config["total_amount_quote"])
    elif executor_type == "onchain_executor":
        amount = await _onchain_notional_quote(config, client)
    else:
        raise ValueError(f"unsupported executor type: {executor_type or 'unknown'}")

    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("planned quote amount must be a positive finite number")
    return amount


# Native token per EVM chain the on-chain executor can sign on, as the CEX pair
# the price feed quotes it in. A chain not listed here cannot have its native
# value priced, so its create must declare ``notional_quote`` instead.
_NATIVE_PRICE_PAIR = {
    1: "ETH-USDT",
    10: "ETH-USDT",
    56: "BNB-USDT",
    137: "POL-USDT",
    8453: "ETH-USDT",
    42161: "ETH-USDT",
    59144: "ETH-USDT",
}
_WEI_PER_NATIVE = 10**18


def _wei(value: Any) -> int:
    """A call's ``value`` in wei: an int, a decimal string or ``0x`` hex.

    Refuses a negative amount and anything it cannot read: the caller is about
    to sign a transaction carrying it.
    """
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError(f"value must be a wei amount, got {value!r}")
    if isinstance(value, int):
        wei = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            wei = int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError as exc:
            raise ValueError(f"value must be a wei amount, got {value!r}") from exc
    else:
        raise ValueError(f"value must be a wei amount, got {value!r}")
    if wei < 0:
        raise ValueError("value cannot be negative")
    return wei


async def _onchain_notional_quote(config: dict[str, Any], client: Any) -> float:
    """Value an ``onchain_executor`` create in quote (USD-ish) terms.

    The executor signs arbitrary calls, so the position it opens is not
    readable from its config the way an order's ``amount`` is. Three things
    bound it, and the gate takes the largest reading of the first two plus the
    third: the agent's declared ``notional_quote``, the native value the calls
    carry (priced through the CEX feed), and the declared ``max_gas_quote``.
    A create that declares nothing and sends no native value has no bound and
    is refused, the same fail-closed rule the DEX path applies.
    """
    notional = float(config.get("notional_quote") or 0)
    if not math.isfinite(notional) or notional < 0:
        raise ValueError("notional_quote must be a non-negative finite number")
    gas = float(config.get("max_gas_quote") or 0)
    if not math.isfinite(gas) or gas < 0:
        raise ValueError("max_gas_quote must be a non-negative finite number")

    calls = config.get("calls") or []
    wei = sum(_wei(call.get("value")) for call in calls if isinstance(call, dict))

    priced_native = 0.0
    if wei > 0:
        chain_id = int(config.get("chain_id") or 0)
        pair = _NATIVE_PRICE_PAIR.get(chain_id)
        price = None
        if pair and client is not None:
            from condor.fetchers.market_data import fetch_current_price

            price = await fetch_current_price(
                client, config.get("price_connector") or "binance", pair
            )
        if price is None:
            if notional <= 0:
                raise ValueError(
                    "native value cannot be priced on chain "
                    f"{chain_id or '?'}; declare notional_quote"
                )
        else:
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("reference price must be a positive finite number")
            priced_native = wei / _WEI_PER_NATIVE * price

    amount = max(notional, priced_native) + gas
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError(
            "onchain_executor create has no quote bound: declare notional_quote"
        )
    return amount


def _amm_field(value: Any, name: str, default: float | None = None) -> float:
    """One numeric ``manage_amm`` field as a non-negative float.

    The tool's schema types every amount as a string, so this parses rather
    than casts, and refuses anything it cannot read: the caller is about to
    sign a transaction with it.
    """
    if value is None or value == "":
        if default is None:
            raise ValueError(f"{name} is required to price this call")
        return default
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return amount


def _positive_price(value: Any) -> float:
    """A reference price, or ``ValueError`` if it cannot bound anything."""
    if value is None or value == "":
        raise ValueError("reference price is unavailable")
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"reference price must be a number, got {value!r}") from exc
    if not math.isfinite(price) or price <= 0:
        raise ValueError("reference price must be a positive finite number")
    return price


async def _amm_base_price(input_data: dict[str, Any], client: Any) -> float:
    """The quote-per-base price to value a ``manage_amm`` call's base leg.

    ``manage_amm`` is pool-scoped, not pair-scoped: ``execute_swap`` and
    ``add_liquidity`` name a ``pool_address`` and a ``base_token`` but never a
    trading pair, so ``fetch_current_price`` -- which keys off a pair -- cannot
    price them. The pool's own mid price is both available from the same client
    and the price the call will actually execute at, so it is used instead.

    ``create_pool`` has no pool yet, so it takes the declared ``initial_price``
    (already quote per base) and otherwise falls back to the market price of
    ``base_token-quote_token``, the pair the connector itself seeds from.
    """
    if client is None:
        raise ValueError("Hummingbot price client is unavailable")

    if input_data.get("action", "") == "create_pool":
        declared = input_data.get("initial_price")
        if declared is not None and declared != "":
            return _positive_price(declared)

        base_token = input_data.get("base_token") or ""
        quote_token = input_data.get("quote_token") or ""
        if not base_token or not quote_token:
            raise ValueError("create_pool must name base_token and quote_token")

        from condor.fetchers.market_data import fetch_current_price

        return _positive_price(
            await fetch_current_price(
                client,
                input_data.get("network") or "",
                f"{base_token}-{quote_token}",
            )
        )

    pool_address = input_data.get("pool_address") or ""
    if not pool_address:
        raise ValueError("pool_address is required to price this call")

    gateway_amm = getattr(client, "gateway_amm", None)
    if gateway_amm is None:
        raise ValueError("Gateway AMM client is unavailable")

    info = (
        await gateway_amm.get_pool_info(
            connector=input_data.get("connector") or "",
            network=input_data.get("network") or "",
            pool_address=pool_address,
        )
    ) or {}
    price = info.get("price")
    if price is None or price == "":
        price = info.get("current_price")
    return _positive_price(price)


async def _dex_notional_quote(
    tool_name: str, input_data: dict[str, Any], client: Any
) -> float:
    """Value a signing Gateway call in its quote token.

    Raises ``ValueError`` when the call cannot be valued; the gate cancels
    rather than signing an unpriced transaction. Like the executor path, the
    number is denominated in the quote token, which the position limit is
    assumed to share.

    The two tool families are priced differently because they identify a market
    differently. ``manage_gateway_swaps`` is *pair*-scoped -- it names a
    ``trading_pair``, so ``fetch_current_price`` can price it directly. The LP
    tools are *pool*-scoped: they name a ``pool_address`` and a ``base_token``
    but never a pair, so they are valued off the pool's own mid price (see
    :func:`_amm_base_price`).
    """
    action = input_data.get("action", "")

    if tool_name == "manage_gateway_swaps":
        # `amount` is denominated in the pair's base token; `side` only picks
        # the direction, so it does not change what the swap is worth.
        base = _amm_field(input_data.get("amount"), "amount")
        amount = base * await _swap_base_price(input_data, client)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("swap quote notional must be a positive finite number")
        return amount

    if action == "open":
        # A CLMM open seeds one or both legs; either may be omitted.
        base = _amm_field(
            input_data.get("base_token_amount"), "base_token_amount", default=0.0
        )
        quote = _amm_field(
            input_data.get("quote_token_amount"), "quote_token_amount", default=0.0
        )
    elif action == "add_liquidity":
        base = _amm_field(input_data.get("base_token_amount"), "base_token_amount")
        quote = _amm_field(input_data.get("quote_token_amount"), "quote_token_amount")
    elif action == "create_pool":
        base = _amm_field(input_data.get("base_token_amount"), "base_token_amount")
        # Optional on create_pool: omitting it seeds from the market price.
        quote = _amm_field(
            input_data.get("quote_token_amount"), "quote_token_amount", default=0.0
        )
    else:
        raise ValueError(f"unsupported DEX action: {action or 'unknown'}")

    amount = quote
    if base:
        amount += base * await _amm_base_price(input_data, client)

    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("DEX quote notional must be a positive finite number")
    return amount


async def _swap_base_price(input_data: dict[str, Any], client: Any) -> float:
    """The quote-per-base price to value a ``manage_gateway_swaps`` call.

    Pair-scoped, so unlike the pool-scoped LP tools this is exactly the case
    :func:`condor.fetchers.market_data.fetch_current_price` is for.
    """
    if client is None:
        raise ValueError("Hummingbot price client is unavailable")

    trading_pair = input_data.get("trading_pair") or ""
    if not trading_pair:
        raise ValueError("swap must name a trading_pair")

    from condor.fetchers.market_data import fetch_current_price

    return _positive_price(
        await fetch_current_price(
            client, input_data.get("connector", "") or "", trading_pair
        )
    )
