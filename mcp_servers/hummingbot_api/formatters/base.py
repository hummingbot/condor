"""
Base formatting utilities shared across all formatters.

This module provides common formatting functions for numbers, timestamps,
percentages, and currency values used throughout the application.
It also includes field accessor utilities for safely extracting values
from dictionaries with fallback support.
"""
from datetime import datetime, timezone
from typing import Any


def format_number(num: Any, decimals: int = 2, compact: bool = True) -> str:
    """
    Format a number to be more compact and readable.

    Args:
        num: The number to format
        decimals: Number of decimal places (default: 2)
        compact: If True, uses K/M notation for large numbers (default: True)

    Returns:
        Formatted number string

    Examples:
        >>> format_number(1500)
        '1.50K'
        >>> format_number(0.001234, decimals=4)
        '0.0012'
        >>> format_number(None)
        'N/A'
    """
    if num is None or num == "N/A":
        return "N/A"

    try:
        num_float = float(num)

        # Handle compact notation for large numbers
        if compact and num_float >= 1000:
            if num_float >= 1_000_000:
                return f"{num_float/1_000_000:.{decimals}f}M"
            return f"{num_float/1000:.{decimals}f}K"

        # Handle very small numbers
        if abs(num_float) < 0.01 and num_float != 0:
            return f"{num_float:.{max(decimals, 4)}f}"

        return f"{num_float:.{decimals}f}"
    except (ValueError, TypeError):
        return str(num)


def format_timestamp(ts: Any, format_str: str = "%m/%d %H:%M") -> str:
    """
    Format a timestamp to readable datetime string.

    Args:
        ts: Unix timestamp (int/float) or ISO datetime string
        format_str: strftime format string (default: "%m/%d %H:%M")

    Returns:
        Formatted datetime string

    Examples:
        >>> format_timestamp(1234567890)
        '02/13 23:31'
        >>> format_timestamp("2023-01-01T12:00:00Z")
        '01/01 12:00'
    """
    try:
        if isinstance(ts, (int, float)):
            # Handle both seconds and milliseconds timestamps
            timestamp = ts / 1000 if ts > 1e12 else ts
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            # Try parsing ISO format string
            dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
            # Convert to UTC if timezone-aware
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)

        return dt.strftime(format_str)
    except (ValueError, OSError, OverflowError):
        return "N/A"


def format_time_only(ts: float) -> str:
    """
    Format a timestamp to time only (HH:MM:SS).

    Args:
        ts: Unix timestamp

    Returns:
        Formatted time string
    """
    return format_timestamp(ts, "%H:%M:%S")


def format_full_datetime(ts: Any) -> str:
    """
    Format a timestamp to full datetime (YYYY-MM-DD HH:MM:SS).

    Args:
        ts: Unix timestamp or datetime string

    Returns:
        Formatted datetime string
    """
    return format_timestamp(ts, "%Y-%m-%d %H:%M:%S")


def format_percentage(pct: Any, decimals: int = 2) -> str:
    """
    Format a decimal percentage to percentage string.

    Args:
        pct: Percentage as decimal (0.05 = 5%)
        decimals: Number of decimal places (default: 2)

    Returns:
        Formatted percentage string

    Examples:
        >>> format_percentage(0.05)
        '5.00%'
        >>> format_percentage(None)
        'N/A'
    """
    if pct is None or pct == "N/A":
        return "N/A"

    try:
        pct_float = float(pct) * 100
        return f"{pct_float:.{decimals}f}%"
    except (ValueError, TypeError):
        return str(pct)


def format_currency(amount: Any, symbol: str = "$", decimals: int = 2) -> str:
    """
    Format a number as currency with symbol.

    Args:
        amount: The amount to format
        symbol: Currency symbol (default: "$")
        decimals: Number of decimal places (default: 2)

    Returns:
        Formatted currency string

    Examples:
        >>> format_currency(1234.56)
        '$1,234.56'
        >>> format_currency(0.001234, decimals=6)
        '$0.001234'
    """
    if amount is None or amount == "N/A":
        return "N/A"

    try:
        amount_float = float(amount)

        # For large amounts, use comma separator
        if abs(amount_float) >= 1:
            return f"{symbol}{amount_float:,.{decimals}f}"

        # For small amounts, show more decimals
        return f"{symbol}{amount_float:.{max(decimals, 6)}f}"
    except (ValueError, TypeError):
        return str(amount)


def truncate_string(text: str, max_len: int = 80, suffix: str = "...") -> str:
    """
    Truncate a string if it exceeds max length.

    Args:
        text: The text to truncate
        max_len: Maximum length (default: 80)
        suffix: Suffix to add when truncated (default: "...")

    Returns:
        Truncated string

    Examples:
        >>> truncate_string("a" * 100, max_len=50)
        'aaaaaaaaaa...aaaaaaa' (47 chars + '...')
    """
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


def truncate_address(address: str, prefix_len: int = 8, suffix_len: int = 6) -> str:
    """
    Truncate a blockchain address or hash to readable format.

    Args:
        address: The address to truncate
        prefix_len: Number of characters to show at start (default: 8)
        suffix_len: Number of characters to show at end (default: 6)

    Returns:
        Truncated address

    Examples:
        >>> truncate_address("0x1234567890abcdef1234567890abcdef12345678")
        '0x123456...345678'
    """
    if len(address) <= prefix_len + suffix_len + 3:
        return address
    return f"{address[:prefix_len]}...{address[-suffix_len:]}"


def format_table_separator(length: int = 120, char: str = "-") -> str:
    """
    Create a table separator line.

    Args:
        length: Length of the separator (default: 120)
        char: Character to use (default: "-")

    Returns:
        Separator string
    """
    return char * length


# ==============================================================================
# Field Accessor Utilities
# ==============================================================================


def get_field(item: dict[str, Any], *keys: str, default: Any = "N/A") -> Any:
    """
    Get a field value from a dictionary with fallback keys.

    Tries each key in order and returns the first non-None value found.
    If no keys match, returns the default value.

    Args:
        item: Dictionary to extract value from
        *keys: One or more keys to try in order
        default: Default value if no key is found (default: "N/A")

    Returns:
        The extracted value or the default

    Examples:
        >>> data = {"created_at": 1234567890, "name": "test"}
        >>> get_field(data, "timestamp", "created_at")  # Returns 1234567890
        >>> get_field(data, "missing_key", default=0)  # Returns 0
    """
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def get_timestamp_field(item: dict[str, Any], *keys: str) -> str:
    """
    Get and format a timestamp field with common fallback keys.

    If no keys are provided, tries common timestamp field names:
    timestamp, created_at, creation_timestamp, time

    Args:
        item: Dictionary to extract timestamp from
        *keys: Optional specific keys to try first

    Returns:
        Formatted timestamp string or "N/A"

    Examples:
        >>> data = {"created_at": 1234567890}
        >>> get_timestamp_field(data)  # Returns formatted timestamp
        >>> get_timestamp_field(data, "my_time", "created_at")  # Tries custom keys first
    """
    default_keys = ("timestamp", "created_at", "creation_timestamp", "time")
    all_keys = keys + default_keys if keys else default_keys

    ts = get_field(item, *all_keys, default=0)
    return format_timestamp(ts)


def get_truncated(
    item: dict[str, Any],
    key: str,
    max_len: int,
    default: str = "N/A"
) -> str:
    """
    Get a string field and truncate it to a maximum length.

    Args:
        item: Dictionary to extract value from
        key: Key to extract
        max_len: Maximum length for the result
        default: Default value if key is not found (default: "N/A")

    Returns:
        Truncated string value

    Examples:
        >>> data = {"description": "This is a very long description"}
        >>> get_truncated(data, "description", 10)  # Returns "This is..."
    """
    value = item.get(key)
    if value is None:
        return default[:max_len] if len(default) > max_len else default

    value_str = str(value)
    return truncate_string(value_str, max_len)


def get_formatted_number(
    item: dict[str, Any],
    *keys: str,
    decimals: int = 2,
    compact: bool = True,
    default: str = "N/A"
) -> str:
    """
    Get a numeric field and format it.

    Args:
        item: Dictionary to extract value from
        *keys: One or more keys to try in order
        decimals: Number of decimal places (default: 2)
        compact: Use K/M notation for large numbers (default: True)
        default: Default value if no key is found (default: "N/A")

    Returns:
        Formatted number string

    Examples:
        >>> data = {"amount": 1500.5, "volume": None}
        >>> get_formatted_number(data, "amount", decimals=2)  # Returns "1.50K"
        >>> get_formatted_number(data, "volume", "amount")  # Returns "1.50K"
    """
    value = get_field(item, *keys, default=None)
    if value is None:
        return default
    return format_number(value, decimals=decimals, compact=compact)


def get_formatted_currency(
    item: dict[str, Any],
    *keys: str,
    symbol: str = "$",
    decimals: int = 2,
    default: str = "N/A"
) -> str:
    """
    Get a numeric field and format it as currency.

    Args:
        item: Dictionary to extract value from
        *keys: One or more keys to try in order
        symbol: Currency symbol (default: "$")
        decimals: Number of decimal places (default: 2)
        default: Default value if no key is found (default: "N/A")

    Returns:
        Formatted currency string

    Examples:
        >>> data = {"price": 1234.56}
        >>> get_formatted_currency(data, "price")  # Returns "$1,234.56"
    """
    value = get_field(item, *keys, default=None)
    if value is None:
        return default
    return format_currency(value, symbol=symbol, decimals=decimals)


def get_formatted_percentage(
    item: dict[str, Any],
    *keys: str,
    decimals: int = 2,
    default: str = "N/A"
) -> str:
    """
    Get a decimal field and format it as percentage.

    Args:
        item: Dictionary to extract value from
        *keys: One or more keys to try in order
        decimals: Number of decimal places (default: 2)
        default: Default value if no key is found (default: "N/A")

    Returns:
        Formatted percentage string

    Examples:
        >>> data = {"change_pct": 0.05}
        >>> get_formatted_percentage(data, "change_pct")  # Returns "5.00%"
    """
    value = get_field(item, *keys, default=None)
    if value is None:
        return default
    return format_percentage(value, decimals=decimals)
