"""Parsing of the numeric strings a tool parameter arrives as.

Amounts, prices and slippage cross the MCP boundary as strings (a ``Decimal``
does not survive JSON, and a float would round a lamport-exact amount). One
guarded parser, so a value that is not a number is refused by NAME before any
request is built: ``quote_swap(amount="$100")`` used to surface as
``[<class 'decimal.ConversionSyntax'>]`` followed by a pointer at Gateway's
logs, which sends the reader to look for a failure that never left this process.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError


def dec(value: Any, name: str) -> Decimal:
    """``value`` as a finite ``Decimal``, or a ``ToolError`` naming ``name``."""
    if value is None:
        raise ToolError(f"{name} is required for this action")
    text = str(value).strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ToolError(_not_a_number(name, value)) from exc
    if not number.is_finite():
        raise ToolError(_not_a_number(name, value))
    return number


def opt_dec(value: Any, name: str) -> Decimal | None:
    """:func:`dec` for an optional parameter: ``None`` stays ``None``."""
    return None if value is None else dec(value, name)


def _not_a_number(name: str, value: Any) -> str:
    text = str(value).strip()
    hint = ""
    if any(symbol in text for symbol in "$€£"):
        hint = (
            f" A currency symbol is not accepted here: {name} is a token amount, "
            "not a USD value. Convert with the current price first "
            "(amount = usd_value / price)."
        )
    elif "," in text:
        hint = f" Remove the thousands separator: {text.replace(',', '')!r}."
    elif text.endswith("%"):
        hint = f" Pass the bare number without the percent sign: {text[:-1]!r}."
    return (
        f"{name} must be a plain decimal number such as '100' or '0.5', got "
        f"{value!r}.{hint}"
    )
