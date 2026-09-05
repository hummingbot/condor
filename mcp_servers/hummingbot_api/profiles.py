"""Which tools each seat mounts, as **names** (FEAT-066, FEAT-091).

A leaf module on purpose: strings and nothing else. ``server.py`` cannot be
imported to ask what it registers — importing it parses argv and builds a
``FastMCP`` singleton as an import side effect — so the web process, which has
to render a switch per tool, reads the tables from here instead.

``server.py`` resolves every name below in its own module namespace at import
and raises if one does not resolve, which is what keeps this table and the
functions provably in step: a renamed tool fails the server's import, not a
session three hours later.

Read the tables as nested rings, narrowest first. Nothing here changes a tool's
behaviour; a seat simply cannot name what was never registered.
"""

from __future__ import annotations

#: One line per tool, for the operator's switch in the brain panel. Prose, not
#: the docstring: the panel has one line of room and the docstring's first line
#: is written for the model.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_portfolio_overview": "Balances, perp positions, LP positions and open orders",
    "set_account_position_mode_and_leverage": "Set position mode and leverage",
    "search_history": "Search historical trades, orders and funding",
    "get_prices": "Latest price for one or more pairs",
    "manage_controllers": "Controller templates and saved configs (design-time)",
    "manage_bots": "Deploy, monitor and control controller-based bots",
    "create_position_executor": "Open a directional position with SL/TP — spends funds",
    "create_grid_executor": "Run a grid across a price range — spends funds",
    "create_dca_executor": "Average into a position over a ladder — spends funds",
    "create_order_executor": "Place one buy or sell order — spends funds",
    "create_lp_executor": "Open a managed CLMM position — spends funds",
    "list_executors": "List executors, filtered",
    "get_executor": "One executor's full detail, optionally with logs",
    "stop_executor": "Stop an executor and close or keep its position",
    "list_orphaned_positions": "Terminated executors that may still own a position",
    "resolve_orphaned_position": "Mark an orphaned position recovered",
    "list_positions_held": "Spot positions held by executors",
    "clear_position_held": "Clear a held position closed elsewhere",
    "get_performance_report": "Aggregate executor performance",
    "executor_defaults": "Read, replace or reset the saved executor defaults",
    "explore_dex_pools": "Discover CLMM pools and compare their yields",
    "quote_swap": "Price a DEX swap — free, signs nothing",
    "execute_swap": "Sign and submit a DEX swap — spends funds",
    "get_swap_status": "Resolve a submitted swap by transaction hash",
    "search_swaps": "Query swap history with filters",
    "explore_geckoterminal": "Explore GeckoTerminal DEX market data",
    "manage_amm": "Direct AMM pool operations and pool creation",
    "manage_clmm": "Direct CLMM position operations",
    "configure_server": "Repoint this seat at another Hummingbot API server",
    "manage_gateway_config": "Read and edit Gateway's chains, tokens and wallets",
    "manage_gateway_container": "Gateway container status, start, stop and logs",
}

#: The trading surface: everything an autonomous tick needs to read a market,
#: size a position, run it and report on it. This is the whole surface minus the
#: two rings below.
#:
#: No raw candle, order book or funding-rate reader is in it (ARCH-308). Those
#: three returned a rendered table — a string a model can read and cannot compute
#: on — so reaching for one bought a number that then had to be re-fetched through
#: ``run_code`` to be averaged, charted or compared across venues. The structured
#: equivalents are one ``run_code`` snippet away (``client.market_data.*``, which
#: returns dicts and takes an ``asyncio.gather`` across venues), and a tool absent
#: from the list is the only form of that advice a model cannot skip. ``get_prices``
#: stays: a single quote, read once and not computed on, is the one case the text
#: answers completely.
TRADING_TOOLS: tuple[str, ...] = (
    "get_portfolio_overview",
    "set_account_position_mode_and_leverage",
    "search_history",
    "get_prices",
    "manage_controllers",
    "manage_bots",
    "create_position_executor",
    "create_grid_executor",
    "create_dca_executor",
    "create_order_executor",
    "create_lp_executor",
    "list_executors",
    "get_executor",
    "stop_executor",
    "list_orphaned_positions",
    "resolve_orphaned_position",
    "list_positions_held",
    "clear_position_held",
    "get_performance_report",
    "executor_defaults",
    "explore_dex_pools",
    "quote_swap",
    "execute_swap",
    "get_swap_status",
    "search_swaps",
    "explore_geckoterminal",
)

#: Direct, un-executored liquidity operations. An attended specialist owns these
#: — the LP experts list ``manage_amm`` in their own tools, and the shared
#: ``recover_orphaned_position`` playbook closes a stranded position with
#: ``manage_clmm(action="close")``. A tick does not: its orphan hint tells it to
#: report, not to self-recover, and an unattended loop that can move liquidity
#: outside an executor has no ledger entry to show for it.
LIQUIDITY_TOOLS: tuple[str, ...] = (
    "manage_amm",
    "manage_clmm",
)

#: Infrastructure. Repointing the API server, rewriting Gateway's config and
#: restarting its container are operator actions with a human in front of them:
#: the chat, or a standalone host. No agent's tool list names one, and the chat's
#: own context prompt already says not to call ``configure_server``.
ADMIN_TOOLS: tuple[str, ...] = (
    "configure_server",
    "manage_gateway_config",
    "manage_gateway_container",
)

#: profile name → the tools it registers. ``full`` is the default because this
#: server is also run standalone (uvx, external hosts, `.mcp.json`).
PROFILE_TOOLS: dict[str, tuple[str, ...]] = {
    "tick": TRADING_TOOLS,
    "agent": TRADING_TOOLS + LIQUIDITY_TOOLS,
    "full": TRADING_TOOLS + LIQUIDITY_TOOLS + ADMIN_TOOLS,
}
