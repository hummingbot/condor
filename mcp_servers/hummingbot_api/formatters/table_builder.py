"""
Generic table builder for consistent table formatting across the codebase.

This module provides the TableBuilder class and ColumnDef dataclass that standardize
table creation, reducing code duplication across all formatters.
"""

from dataclasses import dataclass
from typing import Any, Callable

#: What sits between two cells. No padding and no dashed separator line: a
#: padded table has to cut whatever is wider than its column, and the 120-300
#: dashes under each header were tokens that said nothing.
COLUMN_SEPARATOR = " | "

#: Shown for a value the row does not carry, so an empty cell cannot be misread
#: as a shifted column.
EMPTY_CELL = "-"


def cell(value: Any) -> str:
    """One table cell: the value whole, with the separator kept out of it."""
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace(" | ", " / ").strip()
    return text or EMPTY_CELL


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render delimiter-separated rows under a header line.

    The one renderer every result table goes through. Nothing is truncated: a
    cell is an identifier or a number the next tool call is built from.
    """
    lines = [COLUMN_SEPARATOR.join(headers)]
    lines += [COLUMN_SEPARATOR.join(cell(value) for value in row) for row in rows]
    return "\n".join(lines)


@dataclass
class ColumnDef:
    """
    Definition for a table column.

    Attributes:
        name: Column header name displayed in the table
        key: Field key(s) to extract from data. Can be a single string or a list of
             strings for fallback keys (tries each in order until one is found)
        width: Unused. Kept so existing column definitions still construct;
             cells are neither padded nor truncated
        align: Unused, for the same reason
        formatter: Optional callable to format the value. Receives the raw value
                   and should return a string
        default: Default value if the key is not found in the data

    Examples:
        >>> # Simple column
        >>> col = ColumnDef(name="Price", key="price", width=10)

        >>> # Column with fallback keys
        >>> col = ColumnDef(name="Time", key=["created_at", "timestamp", "time"], width=12)

        >>> # Column with custom formatter
        >>> col = ColumnDef(
        ...     name="Amount",
        ...     key="amount",
        ...     width=12,
        ...     formatter=lambda x: f"{x:.4f}" if x else "N/A"
        ... )
    """

    name: str
    key: str | list[str]
    width: int
    align: str = "left"
    formatter: Callable[[Any], str] | None = None
    default: str = "N/A"

    def get_value(self, item: dict[str, Any]) -> Any:
        """
        Extract value from item using key(s) with fallback.

        Args:
            item: Dictionary to extract value from

        Returns:
            The extracted value or the default
        """
        keys = [self.key] if isinstance(self.key, str) else self.key

        for k in keys:
            if k in item and item[k] is not None:
                return item[k]

        return self.default

    def format_value(self, item: dict[str, Any]) -> str:
        """
        Extract and format value from item.

        Args:
            item: Dictionary to extract value from

        Returns:
            Formatted string value
        """
        value = self.get_value(item)

        if self.formatter is not None:
            try:
                return str(self.formatter(value))
            except (ValueError, TypeError):
                return str(self.default)

        return str(value) if value is not None else str(self.default)

    def format_cell(self, item: dict[str, Any]) -> str:
        """
        The cell for this column: the formatted value, whole.

        ``width`` and ``align`` are no longer applied — see :func:`render_table`.
        """
        return cell(self.format_value(item))


class TableBuilder:
    """
    Generic table builder for consistent table formatting.

    Provides a standardized way to create delimiter-separated tables from
    column definitions. Rows are rendered by :func:`render_table`.

    Example:
        >>> from mcp_servers.hummingbot_api.formatters.base import format_number
        >>> columns = [
        ...     ColumnDef(name="ID", key="id", width=12),
        ...     ColumnDef(name="Price", key="price", width=10, align="right",
        ...               formatter=lambda x: format_number(x, decimals=2)),
        ...     ColumnDef(name="Status", key="status", width=8),
        ... ]
        >>> builder = TableBuilder(columns)
        >>> data = [
        ...     {"id": "abc123", "price": 1234.56, "status": "active"},
        ...     {"id": "def456", "price": 789.01, "status": "pending"},
        ... ]
        >>> print(builder.build(data))
        ID | Price | Status
        abc123 | 1234.56 | active
        def456 | 789.01 | pending
    """

    def __init__(
        self,
        columns: list[ColumnDef],
        empty_message: str = "No data found.",
    ):
        """
        Initialize the table builder.

        Args:
            columns: List of column definitions
            empty_message: Message to return if data is empty
        """
        self.columns = columns
        self.empty_message = empty_message

    def build(
        self, data: list[dict[str, Any]], empty_message: str | None = None
    ) -> str:
        """
        Build the complete table string.

        Args:
            data: List of dictionaries containing the data to display
            empty_message: Optional override for the empty message

        Returns:
            Formatted table string
        """
        if not data:
            return empty_message or self.empty_message

        return render_table(
            [col.name for col in self.columns],
            [[col.format_value(item) for col in self.columns] for item in data],
        )

    def build_with_title(
        self, data: list[dict[str, Any]], title: str, empty_message: str | None = None
    ) -> str:
        """
        Build table with a title above it.

        Args:
            data: List of dictionaries containing the data to display
            title: Title to display above the table
            empty_message: Optional override for the empty message

        Returns:
            Formatted table string with title
        """
        table = self.build(data, empty_message)
        return f"{title}\n\n{table}"
