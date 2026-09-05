"""Risk engine -- pre-tick validation and guardrails.

Enforces position limits, daily loss caps, drawdown limits, executor counts,
and LLM cost caps.  Also provides a permission callback that auto-approves
safe tool calls and blocks dangerous ones that violate risk limits.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from condor.runtime.danger import (
    CREATE_EXECUTOR_TOOLS,
    DANGEROUS_AMM_ACTIONS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_CLMM_ACTIONS,
    LEVERAGE_TOOL,
    LEVERAGED_EXECUTOR_TOOLS,
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
    # Largest |drift| in quote, among rows this agent's controllers are party to,
    # that still counts as a book worth trading on ([[FEAT-113]]). -1 = disabled
    # (the default: small drift is normal — dust from partial fills, a fee taken
    # in kind, a position closing between the two reads — and an install that
    # blocked on it would be taught to raise this until it never fired).
    max_drift_quote: float = -1.0
    # The most leverage a create may ask for, and the highest this session may
    # set on an account ([[SEC-558]]). The other limits bound the capital at
    # stake; this one bounds how far the market has to move before that capital
    # is gone -- a 90-quote grid at 1x and at 20x are the same number to
    # ``max_position_size_quote``. -1 = disabled, the same convention
    # ``max_drift_quote`` uses, so no existing session changes behavior on
    # upgrade. Deliberately NOT 1: a strategy whose owner approved a 5x default
    # at setup would otherwise stop trading the moment this shipped.
    max_leverage: float = -1.0

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
    # The venue check ([[FEAT-113]]). ``book_trusted`` is False when the drift
    # gate is enabled and either the venue did not answer or this agent's worst
    # drift breaches ``max_drift_quote``. An untrustworthy book is a missing
    # metric, so it refuses new exposure and lets every brake through.
    book_trusted: bool = True
    drift_quote: float | None = None  # None = nothing priced, never 0.0
    drift_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_exposure": self.total_exposure,
            "executor_count": self.executor_count,
            "drawdown_pct": self.drawdown_pct,
            "is_blocked": self.is_blocked,
            "block_reason": self.block_reason,
            "should_shutdown": self.should_shutdown,
            "shutdown_reason": self.shutdown_reason,
            "book_trusted": self.book_trusted,
            "drift_quote": self.drift_quote,
            "drift_reason": self.drift_reason,
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
            "max_drift_quote": (
                self._limits.max_drift_quote if hasattr(self, "_limits") else -1
            ),
            "max_leverage": (
                self._limits.max_leverage if hasattr(self, "_limits") else -1
            ),
        }


#: Gateway tools where reaching the tool at all is the signature -- there is no
#: ``action`` to look up, because the split gave the signing call its own name
#: (FEAT-064). The confirmation gate lists these in ``DANGEROUS_TOOLS``.
ALWAYS_SIGNING_DEX_TOOLS = frozenset({"execute_swap"})

#: The signing actions of each action-gated Gateway tool, by tool name. These are
#: the DANGEROUS_* sets the confirmation gate already uses: loop mode stands in for
#: the human those sets would otherwise put in front of the call, so it has to
#: agree with them exactly or the two gates disagree about the same signature.
_SIGNING_DEX_ACTIONS = {
    "manage_clmm": DANGEROUS_CLMM_ACTIONS,
    "manage_amm": DANGEROUS_AMM_ACTIONS,
}


def _is_signing_dex_call(tool_name: str, input_data: dict[str, Any]) -> bool:
    """Whether a Gateway call signs, by tool name or by action.

    The two spellings of the same question: a name-gated tool signs on every
    call, an action-gated one only on its own ``DANGEROUS_*`` actions.
    """
    if tool_name in ALWAYS_SIGNING_DEX_TOOLS:
        return True
    action = input_data.get("action", "")
    return action in _SIGNING_DEX_ACTIONS.get(tool_name, frozenset())


def _dex_call_label(tool_name: str, input_data: dict[str, Any]) -> str:
    """How a refused Gateway call names itself in the reason string."""
    return input_data.get("action", "") or tool_name


def _requested_leverage(input_data: dict[str, Any]) -> float | None:
    """The leverage a call asks for, or ``None`` when it names none.

    Raises ``ValueError`` when the field is there but is not a positive finite
    number. An unreadable leverage is not read as 1: the module refuses what it
    cannot value (SEC-093), and reading a malformed field as the safest possible
    value is how an unparseable call gets approved.
    """
    value = input_data.get("leverage")
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"got {value!r}")
    if isinstance(value, str):
        value = value.strip().removesuffix("x").removesuffix("X")
    try:
        leverage = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"got {value!r}") from exc
    if not math.isfinite(leverage) or leverage <= 0:
        raise ValueError(f"got {value!r}")
    return leverage


#: Signing actions that return capital instead of committing it. Allowed even
#: under a breached limit: refusing them would trap a loop agent in a position
#: it is no longer permitted to unwind.
RISK_REDUCING_DEX_ACTIONS = frozenset({"remove_liquidity", "close", "collect_fees"})


#: Bot actions that add exposure, and so are the ones an untrustworthy book
#: refuses. ``stop_bot``/``stop_controllers`` are the brakes and are never gated
#: here — the ``danger.py`` rule that the failure mode of standing in front of a
#: brake is worse than the failure mode of letting one through.
EXPOSURE_ADDING_BOT_ACTIONS = frozenset(
    {"deploy", "start_controllers", "update_config"}
)


def _book_refusal(current_state: "RiskState | None") -> tuple[bool, str] | None:
    """The drift guard, or None when the book is trustworthy.

    An untrustworthy book is a missing metric ([[FEAT-113]]), and the repo
    already answers that at ``RiskEngine.get_state``: without real numbers we
    must not approve creates. So this refuses **adding** exposure and is placed,
    in every gate, after that gate has decided the call adds any — every
    exposure-reducing path returns before reaching it.
    """
    if current_state is None or current_state.book_trusted:
        return None
    reason = current_state.drift_reason or "the venue check did not agree"
    return False, f"Book untrusted: {reason}"


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

    def _leverage_refusal(
        self, input_data: dict[str, Any], label: str, *, required: bool
    ) -> tuple[bool, str] | None:
        """The leverage guard, or ``None`` when this call clears it.

        ``required`` is what separates a create from a leverage-setting call. A
        create that names no leverage still gets one -- the backend picks its
        own default, and for a grid that default is 20
        (mcp_servers/hummingbot_api/server.py:822) -- so on a limit-enabled
        session an omitted leverage is refused rather than read as 1: an omitted
        parameter is not a conservative one, and the gate cannot see the number
        the backend would choose. ``set_account_position_mode_and_leverage``
        with no ``leverage`` sets none (it only moves the position mode), so
        there is nothing there to bound.
        """
        limit = self.limits.max_leverage
        if limit < 0:  # disabled: every path behaves exactly as it did
            return None
        try:
            leverage = _requested_leverage(input_data)
        except ValueError as exc:
            return False, f"{label}: leverage could not be read ({exc})"
        if leverage is None:
            if not required:
                return None
            return False, (
                f"{label}: no leverage declared, so the venue's own default "
                f"would apply -- declare one at or below the {limit:g}x "
                "leverage limit"
            )
        if leverage > limit:
            return False, (
                f"{label}: leverage {leverage:g}x exceeds the "
                f"{limit:g}x leverage limit"
            )
        return None

    def check_executor_action(
        self,
        tool_call: dict,
        current_state: RiskState,
        planned_amount_quote: float | None = None,
    ) -> tuple[bool, str]:
        """Check if an executor creation is within risk limits.

        Gated by tool NAME since the typed split (FEAT-062): every
        ``create_*_executor`` is a create, and ``stop_executor`` — the only other
        dangerous name in the family — reduces exposure and is never gated here.
        There is no ``action`` to read, so there is nothing to fail closed on.

        On approval of a create, accumulates it into ``current_state``
        (executor count and exposure) so subsequent checks within the same
        tick see the running totals instead of the frozen per-tick snapshot.
        The state is recomputed from the journal at the start of each tick.

        Returns (allowed, reason).
        """
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"

        if tool_call_name(tool_call) not in CREATE_EXECUTOR_TOOLS:
            return True, ""

        # A book the venue contradicts cannot size a create. `stop_executor`
        # returned above, so this only ever stands in front of new exposure.
        refusal = _book_refusal(current_state)
        if refusal:
            return refusal

        # Check executor count
        if current_state.executor_count >= self.limits.max_open_executors:
            return (
                False,
                f"Max open executors ({self.limits.max_open_executors}) reached",
            )

        # Before the exposure check on purpose, so a leveraged create is
        # refused for the reason that is actually true of it ([[SEC-558]]):
        # the quote figure the position limit weighs is the same at 1x and at
        # 20x, and only this line names the difference.
        if tool_call_name(tool_call) in LEVERAGED_EXECUTOR_TOOLS:
            refusal = self._leverage_refusal(
                input_data, tool_call_name(tool_call), required=True
            )
            if refusal:
                return refusal

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

    def check_bot_action(
        self, tool_call: dict, current_state: RiskState | None = None
    ) -> tuple[bool, str]:
        """Check a manage_bots call against risk limits.

        A bot's capital lives in saved controller configs on the API server,
        so exposure can't be computed from the tool inputs alone. Instead,
        bound the loss: a deploy must declare ``max_global_drawdown_quote``
        (the platform-enforced kill switch) no larger than the strategy's
        position limit. Stops are risk-reducing and always allowed; an
        ``update_config`` is only gated when it declares a
        ``total_amount_quote`` above the position limit.

        ``current_state`` carries the venue check's verdict ([[FEAT-113]]);
        without one (older callers, tests) the drift guard simply does not fire.

        Returns (allowed, reason).
        """
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"
        action = input_data.get("action", "")

        if action in EXPOSURE_ADDING_BOT_ACTIONS:
            refusal = _book_refusal(current_state)
            if refusal:
                return refusal

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

    def check_leverage_action(self, tool_call: dict) -> tuple[bool, str]:
        """Check a ``set_account_position_mode_and_leverage`` call ([[SEC-558]]).

        The second half of the leverage envelope. A create is gated on the
        leverage it asks for; this is the call that can raise the leverage on
        positions that are already open, and it is scoped to an account and a
        pair rather than to one executor -- so a tick raising it re-prices
        every position on that pair, including a human's.

        Anything else passes through untouched, and a session with no leverage
        limit set behaves exactly as it does today.

        Returns (allowed, reason).
        """
        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"

        if tool_call_name(tool_call) != LEVERAGE_TOOL:
            return True, ""

        refusal = self._leverage_refusal(input_data, LEVERAGE_TOOL, required=False)
        if refusal:
            return refusal

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
        ``execute_swap`` signs a swap on every call, ``manage_clmm`` and
        ``manage_amm`` move liquidity on their signing actions. Anything else
        (``quote_swap``, ``search_swaps``, pool and position reads, the guide
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
        tool_name = tool_call_name(tool_call)
        action = input_data.get("action", "")

        if not _is_signing_dex_call(tool_name, input_data):
            return True, ""

        if action in RISK_REDUCING_DEX_ACTIONS:
            return True, ""

        # Everything that returns capital has passed above; what is left signs
        # new exposure against a book the venue contradicts.
        refusal = _book_refusal(current_state)
        if refusal:
            return refusal

        if (
            notional_quote is None
            or not math.isfinite(notional_quote)
            or notional_quote <= 0
        ):
            return False, (
                f"DEX {_dex_call_label(tool_name, input_data)} quote notional "
                "is unavailable"
            )

        projected = current_state.total_exposure + notional_quote
        if projected > self.limits.max_position_size_quote:
            return False, (
                f"DEX {_dex_call_label(tool_name, input_data)} would exceed "
                f"position limit: ${projected:.2f} > "
                f"${self.limits.max_position_size_quote:.2f}"
            )

        # Approved: accumulate so the next signature in this tick is gated
        # against the running total, not the pre-tick numbers.
        current_state.total_exposure += notional_quote

        return True, ""


#: How many refusals one session keeps. The tick prompt shows a handful; the
#: rest exist so a burst of them is still visible in the journal.
_MAX_REFUSALS = 20


class RefusalLog:
    """What the unattended gate refused, and why.

    A permission callback can only say *allow* or *cancel*. Neither ACP's
    permission response nor the pydantic-ai gate carries a reason back on its
    own, so a create refused for breaching the position limit and a create that
    genuinely wanted a human look identical from inside the model: both come
    back as "cancelled". A loop agent reading that reports it as *awaiting
    approval* and holds — which is exactly what an unattended seat must never
    do, since nobody is coming.

    So the gate writes down why it said no. Same shape as ``BotLedger``'s
    violation list, and used the same way: appended here, drained by the engine
    after the tick, journaled, and shown in the next tick's prompt.
    """

    def __init__(self) -> None:
        self._pending: list[dict[str, Any]] = []

    def note(self, tool: str, reason: str, now: float | None = None) -> None:
        self._pending.append(
            {
                "tool": tool or "",
                "reason": reason or "",
                "at": time.time() if now is None else now,
            }
        )
        self._pending = self._pending[-_MAX_REFUSALS:]

    def drain(self) -> list[dict[str, Any]]:
        """Return the refusals not yet reported, clearing the pending list."""
        pending, self._pending = self._pending, []
        return pending


async def _fetch_controller_id(client: Any, executor_id: str) -> str:
    """The controller tag on one executor, straight from the API.

    The fallback path of the ownership check: an id the tick's snapshot cannot
    place is either an executor this session opened *after* the snapshot was
    taken (earlier in this same tick) or somebody else's. Only the API can tell
    those apart. Returns ``""`` when the id resolves to nothing — an unknown id,
    an unreachable API, or an executor carrying no tag at all — which the caller
    reads as "unattributable", never as "mine".
    """
    if client is None or not executor_id:
        return ""
    try:
        from condor.fetchers.executors import get_executor_detail

        detail = await get_executor_detail(client, executor_id)
    except Exception:  # pragma: no cover - defensive, the fetcher already traps
        return ""
    if not isinstance(detail, dict):
        return ""
    config = detail.get("config")
    cfg = config if isinstance(config, dict) else detail
    return str(cfg.get("controller_id") or detail.get("controller_id") or "")


async def _stop_refusal(
    executor_id: str,
    agent_id: str,
    executor_owners: dict[str, str] | None,
    client: Any,
) -> str:
    """Why this session may not stop ``executor_id`` — ``""`` when it may.

    Ownership is read off the tick's own executor snapshot first: those rows
    *are* this session's executors (the provider builds them from the session's
    ``controller_id`` plus the bots its ledger owns), so membership settles it
    without a round trip. Controller-mode executors carry their bot controller's
    tag rather than the ``agent_id``, which is why membership — not a tag
    comparison — is what the snapshot answers.
    """
    if not executor_id:
        return "it names no executor_id, so the executor's owner cannot be checked"
    if executor_owners and executor_id in executor_owners:
        return ""

    controller = await _fetch_controller_id(client, executor_id)
    if not controller:
        return (
            f"executor {executor_id!r} could not be attributed to any session — "
            "it is in neither this tick's executor snapshot nor the API's records"
        )
    if controller != agent_id:
        return (
            f"executor {executor_id!r} belongs to controller {controller!r}, not "
            f"to this session {agent_id!r} — stopping it would close another "
            "session's position"
        )
    return ""


def auto_approve_with_risk_check(
    risk_engine: RiskEngine,
    risk_state: RiskState,
    execution_mode: str = "loop",
    ledger: "BotLedger | None" = None,
    agent_id: str = "",
    price_client: Any = None,
    refusals: RefusalLog | None = None,
    executor_owners: dict[str, str] | None = None,
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

    ``refusals`` collects why each cancelled call was cancelled. Without it a
    refusal is a log line the model never sees, and an unattended agent has no
    way to tell "the gate refused this for a reason it could act on" from "a
    human never approved it" — the second reading makes it wait for a human it
    does not have. See :class:`RefusalLog`.

    ``executor_owners`` is the tick's executor snapshot as ``id -> controller_id``
    and binds ``stop_executor`` to this session the way the tag above binds a
    create (SEC-559). Without it — and without an ``agent_id`` — a stop is only
    as scoped as it is today, which is not at all. ``price_client`` doubles as the
    API client for the single-executor lookup that resolves an id the snapshot
    does not carry.
    """

    def deny(
        tool_name: str,
        reason: str,
        *,
        record: bool = True,
        level: int = logging.WARNING,
    ) -> dict[str, Any]:
        """Cancel this call, saying why — in the log, in the record, in the reply.

        The reason rides back on the callback result so the pydantic-ai gate can
        hand it to the model in-band as the tool's result. The ACP bridge gets
        only the ``outcome`` (``condor.acp.client._on_request_permission`` keeps
        the rest off the wire, which its schema does not carry), so the same
        reason also goes to ``refusals`` for the journal and the next tick.

        ``record=False`` is for a refusal that already keeps its own record — the
        ownership one, which the ledger writes down and the [CONTROLLER MODE]
        block reports — so the session journal does not carry the same event
        under two names. ``level`` drops a dry run's refusals back to INFO: there
        every mutation is refused, and twenty warnings a tick is not a signal.
        """
        log.log(level, "Risk gate refused %s: %s", tool_name or "<unknown>", reason)
        if record and refusals is not None:
            refusals.note(tool_name, reason)
        return {"outcome": {"outcome": "cancelled"}, "reason": reason}

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            tool_name = tool_call_name(tool_call)

            # A dangerous tool whose arguments we can't read can't be risk-checked
            # either, so it never runs unattended: cancel instead of falling
            # through to the auto-approve tail (SEC-093).
            input_data = tool_call_input(tool_call)
            if input_data is None:
                return deny(tool_name, "its arguments could not be read")

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
                return deny(
                    tool_name,
                    "this session runs in dry-run mode, where nothing mutates",
                    level=logging.INFO,
                )

            # For executor creates, run risk check. The name is the classification
            # since FEAT-062 — `stop_executor` is dangerous too, but it reduces
            # exposure and so is confirmed without being risk-checked.
            if tool_name in CREATE_EXECUTOR_TOOLS:
                # Validate controller_id on create — presence AND value, since
                # this tag is what per-session PnL attribution keys on. It is a
                # top-level typed parameter now, not a field buried in a config
                # blob, so there is one place it can be.
                tag = str(input_data.get("controller_id") or "")
                if not tag:
                    return deny(
                        tool_name,
                        "it carries no controller_id, so the position it opens "
                        "could never be attributed to this session",
                    )
                if agent_id and tag != agent_id:
                    return deny(
                        tool_name,
                        f"controller_id {tag!r} is not this session's id "
                        f"{agent_id!r} — the position would be unattributable",
                    )

                try:
                    planned_amount_quote = await _planned_amount_quote(
                        tool_name, input_data, price_client
                    )
                except Exception as exc:
                    return deny(tool_name, f"it could not be priced: {exc}")

                allowed, reason = risk_engine.check_executor_action(
                    tool_call, risk_state, planned_amount_quote
                )
                if not allowed:
                    return deny(tool_name, reason)

            # A stop moves real funds too — `keep_position=False`, the default,
            # closes the position at market — and unlike a create it carries no
            # ownership tag of its own: only an id, and `list_executors` hands
            # out the whole fleet's. So bind it to the session the same way the
            # create above is bound (SEC-559).
            #
            # `danger.py` deliberately never stands in front of a brake, and
            # this is a refusal on a stop. The rule holds: a session's brake is
            # *its own* executors, and those stay ungated — resolved from the
            # snapshot, no round trip, no risk check. What is refused is one
            # session braking for another, which is not a brake but a
            # liquidation. Attended seats (empty `agent_id`: chat, consults,
            # tests) keep today's behavior, where a human confirms the stop.
            if tool_name == "stop_executor" and agent_id:
                reason = await _stop_refusal(
                    str(input_data.get("executor_id") or ""),
                    agent_id,
                    executor_owners,
                    price_client,
                )
                if reason:
                    return deny(tool_name, reason)

            # Account leverage is the one dangerous call that opens no
            # position of its own: it re-prices the ones already open, and it
            # is account- and pair-scoped, so a tick can raise the leverage
            # under a position it never opened (SEC-558). Gated against the
            # same `max_leverage` the creates above are gated against.
            if tool_name == LEVERAGE_TOOL:
                allowed, reason = risk_engine.check_leverage_action(tool_call)
                if not allowed:
                    return deny(tool_name, reason)

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
                            ledger.note_violation(bot_name, action)
                            return deny(
                                tool_name,
                                f"bot '{bot_name}' is outside this session's "
                                f"namespace '{ledger.namespace}'",
                                record=False,  # the ledger already has it
                            )

                allowed, reason = risk_engine.check_bot_action(tool_call, risk_state)
                if not allowed:
                    return deny(tool_name, reason)

                # Recorded only once the call is actually going through, so a
                # risk-rejected deploy never lands in the ledger.
                if ledger is not None:
                    if input_data.get("action", "") == "deploy":
                        ledger.note_deploy(input_data.get("bot_name", "") or "")

            # Gateway signatures move funds straight out of the user's wallet,
            # so they are priced and gated here (SEC-224). Interactive surfaces
            # still route the same call to a human via confirmations.py; this
            # branch is what stands in for that human in loop mode.
            if (
                tool_name in ALWAYS_SIGNING_DEX_TOOLS
                or tool_name in _SIGNING_DEX_ACTIONS
            ):
                action = input_data.get("action", "")
                notional_quote = None
                if (
                    _is_signing_dex_call(tool_name, input_data)
                    and action not in RISK_REDUCING_DEX_ACTIONS  # unpriced
                ):
                    try:
                        notional_quote = await _dex_notional_quote(
                            tool_name, input_data, price_client
                        )
                    except Exception as exc:
                        return deny(tool_name, f"it could not be priced: {exc}")

                allowed, reason = risk_engine.check_dex_action(
                    tool_call, risk_state, notional_quote
                )
                if not allowed:
                    return deny(tool_name, reason)

            # Block direct order placement entirely
            if tool_name == "place_order":
                return deny(
                    tool_name,
                    "an agent never places an order directly — use a "
                    "create_*_executor tool instead",
                )

        # Auto-approve everything else
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return deny(tool_call_name(tool_call), "no permission option was offered")

    return callback


def _quote_amount(value: Any, name: str) -> float:
    """One executor amount as a non-negative float.

    The typed tools take numbers, but `create_order_executor`'s ``amount`` is a
    string so it can also carry the "$100" USD form, so this parses rather than
    casts. The "$" is stripped by :func:`_is_quote_denominated`, which is what
    decides whether the figure still has to be priced.
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, str):
        value = value.strip().removeprefix("$")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return amount


def _is_quote_denominated(value: Any) -> bool:
    """Whether an ``amount`` is already in quote currency.

    ``create_order_executor`` accepts "$100" to mean "one hundred of the quote
    token", alongside a plain figure meaning base units. Pricing a "$" amount as
    if it were base would value a $100 order at 100x the pair's price, so the
    marker has to be read before the multiplication, not stripped and forgotten.
    """
    return isinstance(value, str) and value.strip().startswith("$")


async def _planned_amount_quote(
    tool_name: str, input_data: dict[str, Any], client: Any
) -> float:
    """Value an executor create in its quote token, by tool name.

    Each ``create_*_executor`` sizes itself in a different field and a different
    currency, and since FEAT-062 those fields are top-level typed parameters
    rather than keys of an opaque ``executor_config``. The table below is that
    difference made explicit:

    - grid: ``total_amount_quote``, already in quote currency.
    - dca: the sum of the ``amounts_quote`` ladder.
    - position / order: ``amount`` in BASE currency, so it is priced — unless it
      carries order-executor's "$" marker, in which case it already is the notional.
    - lp: ``quote_amount`` plus ``base_amount`` priced.

    Raises ``ValueError`` when the call cannot be valued; the gate cancels rather
    than approving an unpriced create.
    """
    if tool_name == "create_grid_executor":
        amount = _quote_amount(
            input_data.get("total_amount_quote"), "total_amount_quote"
        )
    elif tool_name == "create_dca_executor":
        amounts = [
            _quote_amount(value, "amounts_quote")
            for value in (input_data.get("amounts_quote") or [])
        ]
        if not amounts or any(value <= 0 for value in amounts):
            raise ValueError("amounts_quote must contain positive finite numbers")
        amount = sum(amounts)
    elif tool_name in {
        "create_position_executor",
        "create_order_executor",
        "create_lp_executor",
    }:
        is_lp = tool_name == "create_lp_executor"
        raw = input_data.get("base_amount") if is_lp else input_data.get("amount")
        figure = _quote_amount(raw, "base_amount" if is_lp else "amount")
        quote = _quote_amount(input_data.get("quote_amount"), "quote_amount")
        # A "$100" order is already the notional; anything else is base units.
        base = 0.0 if _is_quote_denominated(raw) else figure
        if _is_quote_denominated(raw):
            quote += figure
        if base:
            if client is None:
                raise ValueError("Hummingbot price client is unavailable")
            from condor.fetchers.market_data import fetch_current_price

            price = await fetch_current_price(
                client,
                input_data.get("connector_name", ""),
                input_data.get("trading_pair", ""),
            )
            if price is None:
                raise ValueError("reference price is unavailable")
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("reference price must be a positive finite number")
            amount = quote + base * price
        else:
            amount = quote
    else:
        raise ValueError(f"unsupported executor tool: {tool_name or 'unknown'}")

    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("planned quote amount must be a positive finite number")
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
    differently. ``execute_swap`` is *pair*-scoped -- it names a
    ``trading_pair``, so ``fetch_current_price`` can price it directly. The LP
    tools are *pool*-scoped: they name a ``pool_address`` and a ``base_token``
    but never a pair, so they are valued off the pool's own mid price (see
    :func:`_amm_base_price`).
    """
    action = input_data.get("action", "")

    if tool_name == "execute_swap":
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
    """The quote-per-base price to value an ``execute_swap`` call.

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
