"""Rendering a figure in the token the strategy actually quotes in ([[LP-2]]).

The risk gate's limits have always been denominated in "quote", and quote is
whatever the strategy trades against: USDT for the CEX market makers, SOL for
``solana_dex_lp_expert``'s LP operator. Every surface rendered them with a ``$``
regardless, so a 500 SOL ceiling was shown as ``$500`` — three orders of
magnitude out, to the one audience that acts on the figure.

One module because there are two such audiences and they must not drift. The
tick prompt's risk block (``journal.render_risk_lines``) is what the agent reads
each tick; the gate's refusals (``risk.RiskEngine.check_*``) are what it reads
when a call is turned back, and the refusal is the more consequential of the
two — it is the message the model reasons from when deciding what to try next.
Fixing only the first left the gate telling an LP strategy it had exceeded
"$500.00" of a limit measured in SOL.

A leaf module on purpose: ``journal`` and ``risk`` do not import each other, and
neither should have to in order to agree on this.

Note that ``$`` is *also* a wire marker on the tool surface —
``create_order_executor`` takes ``amount="$100"`` to mean "one hundred of the
quote token", which ``risk._is_quote_denominated`` reads. That is an input
convention and this is output; nothing here touches it.
"""

from __future__ import annotations

from typing import Any

#: Quote assets a ``$`` renders correctly. Dollar stablecoins trade close
#: enough to the dollar that "$500" is the figure an operator wants to see, and
#: this is what every strategy on a CEX venue quotes in — so the overwhelmingly
#: common case keeps exactly the rendering it always had.
DOLLAR_QUOTES = frozenset({"USD", "USDT", "USDC", "DAI", "BUSD", "FDUSD"})

#: Decimals for each form. A dollar figure wants cents; a token figure wants
#: more, because the amounts are small — an LP slot is a fraction of one SOL,
#: and ``0.03 SOL`` rendered to two places is ``0.03``, which reads as a
#: rounding of anything between 0.025 and 0.035.
_DOLLAR_DECIMALS = 2
_TOKEN_DECIMALS = 4


def normalize_quote(quote_asset: Any) -> str:
    """The quote symbol, upper-cased and stripped, or ``""``.

    ``""`` is the "caller did not say" case and renders as dollars, which is
    both the previous behaviour and the right answer for the venues that do not
    set it.
    """
    return str(quote_asset or "").strip().upper()


def is_dollar_quote(quote_asset: Any) -> bool:
    """Whether figures in ``quote_asset`` should carry a ``$``."""
    unit = normalize_quote(quote_asset)
    return not unit or unit in DOLLAR_QUOTES


def format_quote(value: Any, quote_asset: Any = "") -> str:
    """``$500.00`` for a dollar quote, ``0.5000 SOL`` for anything else.

    A value that is not a number renders as ``0`` in the appropriate form rather
    than raising: every caller is building a message — a prompt line or a
    refusal reason — and a message that cannot be built is worse than one
    carrying a zero, especially on the refusal path, where raising here would
    escape the permission callback and skip the ``RefusalLog`` entirely.
    """
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    unit = normalize_quote(quote_asset)
    if is_dollar_quote(unit):
        return f"${amount:,.{_DOLLAR_DECIMALS}f}"
    return f"{amount:,.{_TOKEN_DECIMALS}f} {unit}"
