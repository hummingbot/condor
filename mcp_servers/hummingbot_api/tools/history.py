"""
Historical data search tools for Hummingbot MCP Server

Provides access to historical data:
- Orders (filled, cancelled, failed)
- Perpetual positions (open and closed)
- CLMM positions (open and closed)
"""

import logging
from typing import Any, Literal

from condor.fetchers._pagination import next_cursor
from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.formatters.base import format_exact, get_field
from mcp_servers.hummingbot_api.formatters.table_builder import render_table
from mcp_servers.hummingbot_api.hummingbot_client import HummingbotClient
from mcp_servers.hummingbot_api.middleware import describe_error

from . import gateway_clmm as gateway_clmm_tools
from . import trading as trading_tools

logger = logging.getLogger("hummingbot-mcp")


# Filters that each data_type can actually forward to the backend. Anything else
# in the shared signature is refused instead of being silently dropped.
_UNSUPPORTED_FILTERS: dict[str, tuple[str, ...]] = {
    # The three branches paginate three different ways, and the shared signature
    # offers both spellings, so each one has to refuse the spelling it drops
    # (CORR-569). POST /trading/orders/search is cursor-only — it has no offset
    # parameter at all — while gateway_clmm.search_positions is offset-only.
    "orders": ("offset",),
    # The trading router has no closed-position history endpoint: get_positions
    # POSTs /trading/positions with only account_names/connector_names/limit. The
    # route accepts a cursor but our wrapper (tools/trading.py:get_positions)
    # neither takes nor forwards one, so cursor is refused here too rather than
    # accepted and dropped.
    "perp_positions": (
        "trading_pairs",
        "status",
        "start_time",
        "end_time",
        "offset",
        "cursor",
    ),
    # gateway_clmm.search_positions has no time-window parameter at any layer
    # (CORR-618): forwarding start_time/end_time would silently return the
    # unfiltered newest-50 positions while looking like a time-windowed history.
    "clmm_positions": ("cursor", "start_time", "end_time"),
}

_FILTER_ALTERNATIVES: dict[str, str] = {
    "orders": (
        "orders is cursor-paginated: pass the cursor printed at the end of the "
        "previous page back as cursor=, rather than an offset."
    ),
    "perp_positions": (
        "perp_positions returns the CURRENT open book (the backend has no closed "
        'position history endpoint). Use data_type="orders" for a time-windowed '
        "history, or get_portfolio_overview() for the same open positions."
    ),
    "clmm_positions": (
        "clmm_positions is offset-paginated: use offset= (the value printed at "
        "the end of the previous page) rather than a cursor. The CLMM position "
        "store has no time-window filter either: read created_at/closed_at on "
        'the returned rows, or use data_type="orders" for time-windowed history.'
    ),
}


def _reject_unsupported_filters(data_type: str, **filters: Any) -> None:
    """Raise ToolError naming any filter the given data_type cannot honour.

    ``offset`` is only a filter when it is non-zero, since it defaults to 0.
    """
    unsupported = _UNSUPPORTED_FILTERS.get(data_type, ())
    # A falsy value (None, [], the default offset=0) means "not supplied".
    supplied = [name for name in unsupported if filters.get(name)]
    if not supplied:
        return

    names = ", ".join(supplied)
    plural = "s" if len(supplied) > 1 else ""
    raise ToolError(
        f"search_history(data_type={data_type!r}) cannot filter by {names}: "
        f"the parameter{plural} would be silently ignored. "
        f"{_FILTER_ALTERNATIVES.get(data_type, '')}".strip()
    )


def _reject_multi_value_clmm_filters(**filters: list[str] | None) -> None:
    """Refuse a CLMM search that names more than one connector or pair.

    ``gateway_clmm.search_positions`` filters on ONE connector and ONE pair.
    Two of either used to set the filter to ``None`` and return every position
    — an unfiltered answer wearing a filtered question — where the rest of this
    module refuses rather than silently ignores.
    """
    for name, values in filters.items():
        if values and len(values) > 1:
            raise ToolError(
                f"search_history(data_type='clmm_positions') can filter by only "
                f"one of {name} per call, got {len(values)} ({', '.join(values)}): "
                "the filter would be silently dropped and every position returned. "
                f"Call once per value, or omit {name} to list them all."
            )


#: Addresses whole and amounts exact: the rows are what a close, a fee collect
#: or an orphan reconciliation is built from.
CLMM_POSITION_COLUMNS = [
    "connector",
    "network",
    "pair",
    "status",
    "lower_price",
    "upper_price",
    "base_amount",
    "quote_amount",
    "base_fees_collected",
    "quote_fees_collected",
    "position_address",
    "pool_address",
    "created",
    "closed",
]


async def search_history(
    client: HummingbotClient,
    data_type: Literal["orders", "perp_positions", "clmm_positions"],
    # Common filters
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    status: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    # Pagination
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
    # CLMM-specific filters
    network: str | None = None,
    wallet_address: str | None = None,
    position_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """
    Search historical data from the backend database.

    This tool is for historical analysis, reporting, and tax purposes.
    For real-time current state, use get_portfolio_overview() instead.

    Data Types:
    - orders: Historical order data (filled, cancelled, failed)
    - perp_positions: The CURRENT open perpetual book. The backend has no closed
      position history endpoint, so this cannot be filtered by pair, status or
      time; use get_portfolio_overview() for the same data, or data_type="orders"
      for a time-windowed history.
    - clmm_positions: CLMM LP positions (both open and closed)

    Args:
        client: Hummingbot client instance
        data_type: Type of historical data to search
        account_names: Filter by account names (all data types, optional)
        connector_names: Filter by connector names (all data types, optional)
        trading_pairs: Filter by trading pairs (orders, clmm_positions; optional)
        status: Filter by status (orders, clmm_positions; optional, e.g., 'FILLED')
        start_time: Start timestamp in seconds (orders only, optional)
        end_time: End timestamp in seconds (orders only, optional)
        limit: Maximum number of results (all data types, default: 50, max: 1000)
        offset: Pagination offset (clmm_positions only, default: 0)
        cursor: Pagination cursor from the previous page (orders only, optional)
        network: Network filter for CLMM positions (optional)
        wallet_address: Wallet address filter for CLMM positions (optional)
        position_addresses: Specific position addresses for CLMM (optional)

    Returns:
        Dictionary containing search results with formatted output

    Raises:
        ToolError: If a filter is supplied that the chosen data_type cannot honour
    """
    # Fail loudly rather than silently dropping filters the branch cannot apply.
    # Raised before the try/except below, which would flatten it into a generic
    # Exception, and before any request reaches the client.
    _reject_unsupported_filters(
        data_type,
        trading_pairs=trading_pairs,
        status=status,
        start_time=start_time,
        end_time=end_time,
        offset=offset,
        cursor=cursor,
    )
    if data_type == "clmm_positions":
        _reject_multi_value_clmm_filters(
            connector_names=connector_names, trading_pairs=trading_pairs
        )

    try:
        # ============================================
        # ORDERS - Historical order data
        # ============================================
        if data_type == "orders":
            # Use existing trading_tools.search_orders function
            result = await trading_tools.search_orders(
                client=client,
                account_names=account_names,
                connector_names=connector_names,
                trading_pairs=trading_pairs,
                status=status,
                start_time=start_time,
                end_time=end_time,
                limit=min(limit, 1000),
                cursor=cursor,
            )

            formatted_output = f"Order History\n{'=' * 100}\n\n{result['orders_table']}"

            # The backend paginates this route by opaque cursor. The hint used to
            # print `use offset=N`, which search_orders has no parameter for, so a
            # model that followed it re-fetched page one forever and read the
            # identical rows as fresh history (CORR-569).
            following = next_cursor(result)
            if following and following == cursor:
                # hummingbot-api before its keyset-cursor fix built every order's
                # cursor from fields its rows do not carry (timestamp/client_order_id
                # instead of created_at/order_id), so every page handed back "0:" and
                # the page after it overlapped the one before. Surfacing it again would
                # send the model round the same page forever.
                formatted_output += (
                    "\n\n... the backend handed back the same cursor it was given, so it "
                    "cannot page any further and the rows above may repeat the previous "
                    "page. Narrow the search with start_time/end_time to reach older orders."
                )
            elif following:
                formatted_output += (
                    f'\n\n... and more (use cursor="{following}" to see the next page)'
                )
            elif result["pagination"].get("has_more"):
                formatted_output += (
                    "\n\n... and more, but the backend returned no next cursor: "
                    "narrow the search with start_time/end_time to reach the rest."
                )

            return {
                "data_type": "orders",
                "total_count": result["total_returned"],
                "results": result["orders"],
                "formatted_output": formatted_output,
            }

        # ============================================
        # PERP POSITIONS - Perpetual positions
        # ============================================
        elif data_type == "perp_positions":
            # The backend exposes no closed-position history: this is the current
            # open book. Unsupported filters were already refused above.
            result = await trading_tools.get_positions(
                client=client,
                account_names=account_names,
                connector_names=connector_names,
                limit=min(limit, 1000),
            )

            formatted_output = (
                f"Perpetual Positions (current open book)\n{'=' * 100}\n\n"
                f"{result['positions_table']}"
            )

            return {
                "data_type": "perp_positions",
                "total_count": result["total_positions"],
                "results": result["positions"],
                "formatted_output": formatted_output,
            }

        # ============================================
        # CLMM POSITIONS - LP positions
        # ============================================
        elif data_type == "clmm_positions":
            # Build search parameters for CLMM positions
            page_size = max(1, min(limit, 1000))
            search_params = {
                "limit": page_size,
                "offset": offset,
                "refresh": False,  # Don't refresh from blockchain for historical search
            }

            # Add CLMM-specific filters
            if network:
                search_params["network"] = network
            if wallet_address:
                search_params["wallet_address"] = wallet_address
            # Exactly one of each, or none: more than one was refused above.
            if connector_names:
                search_params["connector"] = connector_names[0]
            if trading_pairs:
                search_params["trading_pair"] = trading_pairs[0]
            if status:
                search_params["status"] = status
            if position_addresses:
                search_params["position_addresses"] = position_addresses

            # Search CLMM positions using gateway_clmm tools
            result = await client.gateway_clmm.search_positions(**search_params)

            if not result or not isinstance(result, dict):
                return {
                    "data_type": "clmm_positions",
                    "total_count": 0,
                    "results": [],
                    "formatted_output": "No CLMM positions found",
                }

            positions = [
                pos for pos in (result.get("data") or []) if isinstance(pos, dict)
            ]
            total_count = len(positions)

            # Format CLMM positions as table
            if positions:
                rows = [
                    [
                        get_field(pos, "connector", default=None),
                        get_field(pos, "network", default=None),
                        get_field(pos, "trading_pair", default=None),
                        get_field(pos, "status", default=None),
                        format_exact(get_field(pos, "lower_price", default=None)),
                        format_exact(get_field(pos, "upper_price", default=None)),
                        format_exact(get_field(pos, "base_token_amount", default=None)),
                        format_exact(
                            get_field(pos, "quote_token_amount", default=None)
                        ),
                        format_exact(
                            get_field(pos, "base_fee_collected", default=None)
                        ),
                        format_exact(
                            get_field(pos, "quote_fee_collected", default=None)
                        ),
                        get_field(pos, "position_address", default=None),
                        get_field(pos, "pool_address", default=None),
                        get_field(pos, "created_at", default=None),
                        get_field(pos, "closed_at", default=None),
                    ]
                    for pos in positions
                ]
                table_lines = [
                    "CLMM LP Positions History",
                    "",
                    render_table(CLMM_POSITION_COLUMNS, rows),
                ]

                # The backend was asked for at most `page_size` rows, so a full
                # page is the only signal that more exist: the old test
                # (`len(rows) > limit`) could never be true, and a full first
                # page read as the whole history.
                if total_count >= page_size:
                    table_lines.append(
                        f"\n... this page is full ({total_count} rows), so there "
                        f"may be more: use offset={offset + total_count} to see "
                        "the next page."
                    )

                formatted_output = "\n".join(table_lines)
            else:
                formatted_output = "No CLMM positions found"
                if offset:
                    formatted_output += f" at offset={offset} (the end of the list)"

            return {
                "data_type": "clmm_positions",
                "total_count": total_count,
                "results": positions,
                "formatted_output": formatted_output,
            }

        else:
            return {
                "data_type": data_type,
                "total_count": 0,
                "results": [],
                "formatted_output": f"Unknown data type: {data_type}",
            }

    except Exception as e:
        logger.error(f"Error in search_history: {str(e)}", exc_info=True)
        raise Exception(f"Failed to search history: {describe_error(e)}")
