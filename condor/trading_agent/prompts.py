"""Prompt builder for trading agent ticks.

Assembles the single prompt sent to a fresh ACP session each tick,
combining: base rules, strategy instructions, config, risk state,
pre-computed skill data, and recent journal entries.
"""

from __future__ import annotations

from typing import Any

from .strategy import Strategy

BASE_PROMPT = """\
You are an autonomous trading agent running inside Condor.

RULES:
- ALL trading MUST go through executors. Use manage_executors(action="create") \
to open positions or grids. NEVER use place_order directly.
- Before using any mcp-hummingbot tool, call configure_server with the \
credentials provided in your config section.
- Be conservative. When in doubt, take NO action and explain why in the journal.
- Keep tool call chains short (1-5 calls per tick).
- When creating executors, ALWAYS include controller_id in the executor_config. \
This tags executors as yours so the system can track which executors belong to which agent.

JOURNAL RULES:
- Write ONE compact action entry per tick via trading_agent_journal_write \
(entry_type="action"). One line: what you did and why.
- Only write a learning (entry_type="learning") if you discovered something \
NEW that isn't already in the learnings list. Learnings are deduplicated \
automatically -- don't repeat what's already there.
- Keep entries short. No paragraphs. No verbose explanations.

AVAILABLE MCP TOOLS:
- mcp-hummingbot: configure_server, get_market_data, get_portfolio_overview, \
manage_executors, manage_bots, search_history, explore_dex_pools, manage_gateway_config
- condor: trading_agent_journal_write, trading_agent_journal_read, \
send_notification
"""

TOOL_PRELOAD_HINT = (
    "IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:\n"
    'ToolSearch(query="select:mcp__mcp-hummingbot__configure_server,'
    "mcp__mcp-hummingbot__get_market_data,"
    "mcp__mcp-hummingbot__get_portfolio_overview,"
    "mcp__mcp-hummingbot__manage_executors,"
    "mcp__mcp-hummingbot__search_history,"
    "mcp__mcp-hummingbot__explore_dex_pools,"
    "mcp__mcp-hummingbot__manage_gateway_config,"
    "mcp__condor__trading_agent_journal_write,"
    "mcp__condor__trading_agent_journal_read,"
    'mcp__condor__send_notification")\n'
    "Do this silently."
)


def build_tick_prompt(
    strategy: Strategy,
    config: dict[str, Any],
    core_data: dict[str, str],
    journal: str,
    learnings: str,
    risk_state: dict[str, Any],
    server_credentials: dict[str, str] | None = None,
    tick_number: int = 1,
    agent_id: str = "",
    skill_prompts: list[str] | None = None,
) -> str:
    """Build the full prompt for one agent tick."""
    sections: list[str] = [BASE_PROMPT, TOOL_PRELOAD_HINT]

    # Tick identity
    tick_info = f"[TICK INFO]\nThis is tick #{tick_number}. Use this number in journal entries and notifications."
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}\nUse controller_id=\"{agent_id}\" in all executor configs."
    sections.append(tick_info)

    # Server credentials
    if server_credentials:
        sections.append(
            "[SERVER CREDENTIALS]\n"
            f"host={server_credentials['host']}, "
            f"port={server_credentials['port']}, "
            f"username={server_credentials['username']}, "
            f"password={server_credentials['password']}\n"
            "Call configure_server with these values before any mcp-hummingbot tool."
        )

    # Strategy instructions
    sections.append(f"[STRATEGY INSTRUCTIONS]\n{strategy.instructions}")

    # Current config
    config_lines = [f"[CURRENT CONFIG]"]
    for k, v in config.items():
        config_lines.append(f"{k}: {v}")
    sections.append("\n".join(config_lines))

    # Risk state
    rs = risk_state
    risk_lines = [
        "[RISK STATE]",
        f"Daily PnL: ${rs.get('daily_pnl', 0):+.2f} / -${rs.get('max_daily_loss', 50):.2f} limit",
        f"Position Size: ${rs.get('total_exposure', 0):.2f} / ${rs.get('max_position_size', 500):.2f} limit",
        f"Open Executors: {rs.get('executor_count', 0)} / {rs.get('max_open_executors', 5)} limit",
        f"Drawdown: {rs.get('drawdown_pct', 0):.1f}% / {rs.get('max_drawdown_pct', 10):.1f}% limit",
        f"Daily LLM Cost: ${rs.get('daily_cost', 0):.2f} / ${rs.get('max_cost_per_day', 5):.2f} limit",
        f"Status: {'BLOCKED - ' + rs.get('block_reason', '') if rs.get('is_blocked') else 'ACTIVE'}",
    ]
    sections.append("\n".join(risk_lines))

    # Core skill data (pre-computed)
    for name, summary in core_data.items():
        sections.append(f"[CORE DATA - {name}]\n{summary}")

    # Journal -- compact memory
    if learnings:
        sections.append(
            f"[LEARNINGS — do NOT repeat these, only add genuinely new insights]\n{learnings}"
        )
    if journal:
        sections.append(f"[RECENT ACTIONS — last {len(journal.splitlines())} ticks]\n{journal}")

    # Skill prompts (rendered markdown templates)
    if skill_prompts:
        for sp in skill_prompts:
            sections.append(sp)

    # Final instruction
    sections.append(
        "[INSTRUCTIONS]\n"
        "1. Analyze current state. Decide: act or skip.\n"
        "2. If acting, use manage_executors to create/stop executors.\n"
        "3. Write ONE journal action entry (short, one line).\n"
        "4. Only add a learning if it's genuinely new.\n"
        "5. Send a short notification to the user:\n\n"
        f"Tick #{tick_number} — {{strategy_name}}\n"
        "• Market: <price/trend>\n"
        "• Action: <what you did or 'Hold'>\n"
        "• Positions: <count, PnL>\n\n"
        "Keep the notification under 5 lines."
    )

    return "\n\n".join(sections)
