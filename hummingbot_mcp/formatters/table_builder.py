"""
Generic table builder for consistent table formatting across the codebase.

This module provides the TableBuilder class and ColumnDef dataclass that standardize
table creation, reducing code duplication across all formatters.
"""
from dataclasses import dataclass, field
from typing import Any, Callable

from .base import format_table_separator


@dataclass
class ColumnDef:
    """
    Definition for a table column.

    Attributes:
        name: Column header name displayed in the table
        key: Field key(s) to extract from data. Can be a single string or a list of
             strings for fallback keys (tries each in order until one is found)
        width: Column width in characters
        align: Text alignment ("left", "right", or "center")
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
        Format value with proper width and alignment.

        Args:
            item: Dictionary to extract value from

        Returns:
            Padded string with correct width and alignment
        """
        value = self.format_value(item)

        # Truncate if too long
        if len(value) > self.width:
            value = value[:self.width - 3] + "..." if self.width > 3 else value[:self.width]

        # Apply alignment
        if self.align == "right":
            return value.rjust(self.width)
        elif self.align == "center":
            return value.center(self.width)
        else:  # left
            return value.ljust(self.width)


class TableBuilder:
    """
    Generic table builder for consistent table formatting.

    Provides a standardized way to create ASCII tables with configurable columns,
    headers, and formatting options.

    Example:
        >>> from hummingbot_mcp.formatters.base import format_number
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
        ID           | Price      | Status
        -----------------------------------------
        abc123       |    1234.56 | active
        def456       |     789.01 | pending
    """

    def __init__(
        self,
        columns: list[ColumnDef],
        separator_char: str = "-",
        column_separator: str = " | ",
        empty_message: str = "No data found."
    ):
        """
        Initialize the table builder.

        Args:
            columns: List of column definitions
            separator_char: Character used for the separator line
            column_separator: String used between columns
            empty_message: Message to return if data is empty
        """
        self.columns = columns
        self.separator_char = separator_char
        self.column_separator = column_separator
        self.empty_message = empty_message

    def _calculate_width(self) -> int:
        """Calculate total table width including separators."""
        column_widths = sum(col.width for col in self.columns)
        separator_widths = len(self.column_separator) * (len(self.columns) - 1)
        return column_widths + separator_widths

    def _build_header(self) -> str:
        """Build the header row."""
        cells = []
        for col in self.columns:
            header = col.name
            if len(header) > col.width:
                header = header[:col.width]
            cells.append(header.ljust(col.width))
        return self.column_separator.join(cells)

    def _build_row(self, item: dict[str, Any]) -> str:
        """Build a single data row."""
        cells = [col.format_cell(item) for col in self.columns]
        return self.column_separator.join(cells)

    def build(self, data: list[dict[str, Any]], empty_message: str | None = None) -> str:
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

        header = self._build_header()
        separator = format_table_separator(self._calculate_width(), self.separator_char)
        rows = [self._build_row(item) for item in data]

        return f"{header}\n{separator}\n" + "\n".join(rows)

    def build_with_title(
        self,
        data: list[dict[str, Any]],
        title: str,
        empty_message: str | None = None
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
        if data:
            return f"{title}\n\n{table}"
        return f"{title}\n\n{table}"


def create_simple_table(
    data: list[dict[str, Any]],
    column_config: list[tuple[str, str, int]],
    empty_message: str = "No data found."
) -> str:
    """
    Convenience function to create a simple table without defining ColumnDef objects.

    Args:
        data: List of dictionaries containing the data
        column_config: List of tuples (name, key, width) for each column
        empty_message: Message to return if data is empty

    Returns:
        Formatted table string

    Example:
        >>> data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        >>> config = [("Name", "name", 10), ("Age", "age", 5)]
        >>> print(create_simple_table(data, config))
        Name       | Age
        ------------------
        Alice      | 30
        Bob        | 25
    """
    columns = [ColumnDef(name=name, key=key, width=width) for name, key, width in column_config]
    builder = TableBuilder(columns, empty_message=empty_message)
    return builder.build(data)
