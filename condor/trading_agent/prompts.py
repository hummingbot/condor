"""Prompt builder for trading agent ticks.

Assembles the single prompt sent to a fresh ACP session each tick,
combining: base rules, strategy instructions, config, risk state,
pre-computed core data, and journal context (learnings + recent decisions).
"""

from __future__ import annotations

from typing import Any

from .strategy import Strategy

BASE_PROMPT_LIVE = """\
You are an autonomous trading agent running inside Condor.

RULES:
- Trade ONLY via manage_executors(action="create"). NEVER use place_order.
- Be conservative. When in doubt, hold and journal why.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") \
to fetch the full config schema, compare it against what you sent, fix the missing/wrong \
fields, and retry ONCE. Journal the error and fix as a learning.
"""

BASE_PROMPT_DRY_RUN = """\
You are an autonomous trading agent running inside Condor in 🧪 DRY RUN mode.

RULES:
- This is OBSERVATION ONLY. Do NOT create or stop executors.
- manage_executors is available for read-only queries (performance_report).
- Analyze the market and describe what you WOULD do, but take NO trading action.

DRY RUN MESSAGING:
- Use conditional language: "Would place grid..." not "Grid placed"
- Prefix actions with 🧪 to signal dry-run
- End with: "No executors were created (dry run)"
"""

BASE_PROMPT_COMMON = """\
GENERAL:
- The mcp-hummingbot server is pre-configured. Do NOT call configure_server.
- Keep tool chains short (1-5 calls per tick).
- Your executor state and positions are pre-loaded in [CORE DATA] below — no need to query them.

JOURNAL:
- Write ONE action entry per tick via trading_agent_journal_write(entry_type="action", tick=<n>). One line.
- Always pass tick=<n> from [TICK INFO] as the MCP tick argument (not only tick=<n> inside the text field).
- Learnings must specify a category: "market" or "execution".
  trading_agent_journal_write(entry_type="learning", category="market|execution", text="...")
  - market: band behavior, volatility regimes, S/R patterns, routine observations.
  - execution: executor errors, schema issues, fill problems, timing.
- Keep learnings factual and short (1 line). No speculation.
- Only write a learning if it's genuinely NEW. Duplicates are auto-filtered.
- Do NOT call trading_agent_journal_read — context is already in this prompt.

ROUTINES:
- manage_routines(action="run", name="...", config={...}) for analysis scripts.
- manage_routines(action="list") to discover routines.
- Routines tagged "agent" are local to your strategy.

NOTIFICATIONS (Telegram push — keep readable on a phone screen):
- Call send_notification at the END of substantive work for this tick. Default is plain text (no Telegram markup — do NOT use MarkdownV2 backslashes like "\\|" or "\\$"; they render as ugly escape junk).
- Be brief: ideally under ~900 characters, 5–10 short lines maximum.
- **Position size in notifications:** report **notional USD** only — the position value shown on the exchange (~`total_amount_quote` used for sizing, or half for half-size entries). **Do NOT multiply notional × leverage.** Leverage sets margin (~notional/leverage), not a larger "effective" position.
- Structured layout (adapt fields to what happened):
  📊 TICK #<n> — <agent_id matching [TICK INFO]>

  ⚡ One-line WHAT (e.g. OPENED SHORT BTC-USD | ~$200 notional | 30x | SL 1.5% TP 3% market)

  🔑 Executor ID: <id on its own line, full string, monospace not required>

  💡 WHY: ≤2 short sentences OR skip if unchanged / obvious.
- No wall-of-text narration; omit scanner methodology unless it changed the trade.
"""

TOOL_PRELOAD_LIVE = (
    "IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:\n"
    'ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,'
    "mcp__mcp-hummingbot__manage_executors,"
    "mcp__mcp-hummingbot__search_history,"
    "mcp__mcp-hummingbot__explore_geckoterminal,"
    "mcp__condor__trading_agent_journal_write,"
    "mcp__condor__send_notification,"
    'mcp__condor__manage_routines")\n'
    "Do this silently."
)

TOOL_PRELOAD_DRY_RUN = (
    "IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:\n"
    'ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,'
    "mcp__mcp-hummingbot__search_history,"
    "mcp__mcp-hummingbot__explore_geckoterminal,"
    "mcp__condor__trading_agent_journal_write,"
    "mcp__condor__send_notification,"
    'mcp__condor__manage_routines")\n'
    "Do this silently."
)


def _build_routines_section(strategy: Strategy) -> str:
    """Build an [AVAILABLE ROUTINES] section listing agent-local + global routines."""
    from routines.base import discover_routines, discover_routines_from_path

    lines = ["[AVAILABLE ROUTINES]"]
    lines.append(
        f'Call via: manage_routines(action="run", name="<name>", strategy_id="{strategy.id}", config={{...}})'
    )
    lines.append("")

    # Agent-local routines first
    routines_dir = strategy.agent_dir / "routines"
    if routines_dir.exists():
        from routines.base import discover_routines_from_path

        local = discover_routines_from_path(routines_dir)
        if local:
            lines.append("Agent-local:")
            for name, r in sorted(local.items()):
                lines.append(f"  - {name}: {r.description}")

    # Global routines
    global_routines = discover_routines(force_reload=False)
    if global_routines:
        lines.append("Global:")
        for name, r in sorted(global_routines.items()):
            lines.append(f"  - {name}: {r.description}")

    return "\n".join(lines)


def _build_sizing_section(config: dict[str, Any], execution_mode: str) -> str | None:
    """Clarify notional vs leverage for position_executor sizing (prevents bad notifications)."""
    if execution_mode == "dry_run":
        return None
    raw = config.get("total_amount_quote")
    if raw is None:
        return None
    try:
        quote = float(raw)
    except (TypeError, ValueError):
        return None
    if quote <= 0:
        return None
    half = quote / 2
    return (
        "[POSITION SIZING]\n"
        f"total_amount_quote=${quote:g} → target **position notional (USD)** on the exchange "
        f"(formal/full entry). Half-size entries: ~${half:g} notional.\n"
        "Sizing: pass `notional_usd` in executor_config (e.g. notional_usd=200 for half-size); "
        "Condor converts to base `amount` using live price. Do NOT pre-compute `amount` manually. "
        "**Leverage does not increase notional** — it reduces margin required (~notional / leverage).\n"
        "Telegram WHAT line: `~$<notional> notional | <leverage>x | SL … TP …` — never notional×leverage."
    )


def build_tick_prompt(
    strategy: Strategy,
    config: dict[str, Any],
    core_data: dict[str, str],
    learnings: str,
    summary: str,
    recent_decisions: str,
    risk_state: dict[str, Any],
    tick_number: int = 1,
    agent_id: str = "",
    cached_routines_section: str | None = None,
    digest_boundary: bool = False,
    digest_interval: int = 0,
    barrier_closes_section: str = "",
) -> str:
    """Build the full prompt for one agent tick."""
    from condor.acp.cursor_sdk_client import is_cursor_sdk_model
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

    execution_mode = config.get("execution_mode", "loop")
    is_dry_run = execution_mode == "dry_run"
    agent_key = config.get("agent_key") or strategy.agent_key
    use_pydantic_ai = is_pydantic_ai_model(agent_key)
    use_cursor = is_cursor_sdk_model(agent_key)

    # Select base prompt and tool preload based on mode
    base_prompt = BASE_PROMPT_DRY_RUN if is_dry_run else BASE_PROMPT_LIVE
    sections: list[str] = [base_prompt, BASE_PROMPT_COMMON]

    # Tool preload is ACP-specific (ToolSearch); pydantic-ai auto-discovers MCP tools
    if use_cursor:
        sections.append(
            "TOOLS:\n"
            "Condor MCP stdio servers (mcp-hummingbot, condor) are attached to Composer for this "
            "local Cursor session—call MCP tools directly by name. Tool names may differ from ACP-style "
            "mcp__prefixed identifiers; rely on Composer's exposed tool list. "
            "Note: MCP tool approvals are handled by Composer, not Telegram confirmation.\n"
            'EXECUTORS: Before manage_executors(action="create"), invoke manage_executors(executor_type="...") '
            "without action once so hummingbot-api returns the live schema and guide."
        )
    elif not use_pydantic_ai:
        tool_preload = TOOL_PRELOAD_DRY_RUN if is_dry_run else TOOL_PRELOAD_LIVE
        sections.append(tool_preload)
    else:
        sections.append(
            "TOOLS:\n"
            "All MCP tools are pre-loaded and available. Call them directly by name."
        )

    # Tick identity
    tick_info = f"[TICK INFO]\nThis is tick #{tick_number}. Use this number in journal entries and notifications."
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}"
        if not is_dry_run:
            tick_info += f'\nPass controller_id="{agent_id}" as a TOP-LEVEL arg to manage_executors (not inside executor_config).'
            tick_info += (
                '\nFor manage_executors(action="search"): pass controller_id='
                f'"{agent_id}" (and/or controller_ids=[...]) and status=RUNNING '
                "to list live executors; hummingbot-api does not use ACTIVE — MCP maps synonyms if needed."
            )
    sections.append(tick_info)

    # Run-once mode note
    if execution_mode == "run_once":
        sections.append(
            "[EXECUTION MODE — RUN ONCE]\n"
            "Single-tick session with LIVE execution. The engine will stop after this tick. "
            "Make your best move now — there will be no follow-up ticks."
        )

    # Server credentials are injected via env vars into the MCP process,
    # so no need to include them in the prompt or call configure_server.

    # Strategy instructions
    sections.append(f"[STRATEGY INSTRUCTIONS]\n{strategy.instructions}")

    # Available routines (use cached version if provided)
    if cached_routines_section:
        sections.append(cached_routines_section)
    else:
        try:
            sections.append(_build_routines_section(strategy))
        except Exception:
            pass  # Don't fail the tick if routine discovery fails

    # Session trading context (natural language directives for this session)
    trading_context = config.get("trading_context", "")
    if trading_context:
        sections.append(
            "[SESSION CONTEXT]\n"
            "The user provided the following natural language context for this trading session. "
            "Use this to guide your market selection, risk appetite, and trading style:\n\n"
            f"{trading_context}"
        )

    # Strategy-specific parameters (duration fields + effective tick thresholds)
    from .strategy_configs import resolve_effective_strategy_params

    raw_params = (
        config.get("strategy_params")
        if isinstance(config.get("strategy_params"), dict)
        else {}
    )
    frequency_sec = int(config.get("frequency_sec") or 60)
    strategy_params = resolve_effective_strategy_params(
        strategy.slug, raw_params, frequency_sec
    )
    if strategy_params:
        strategy_lines = [
            "[STRATEGY CONFIG]",
            "Active strategy parameters for this session. Use these values in all formulas and gates below.",
            "Tick thresholds (neutral_pressure_activation_ticks, neutral_exit_streak, "
            "sl_symbol_cooldown_ticks, flip_cooldown_ticks) are derived from duration hours "
            "÷ frequency_sec — use the effective *_ticks values below.",
        ]
        for k, v in strategy_params.items():
            strategy_lines.append(f"{k}: {v}")
        sections.append("\n".join(strategy_lines))

    # Current config (exclude keys shown elsewhere or not useful to the LLM)
    _CONFIG_EXCLUDE = {
        "trading_context",
        "risk_limits",  # shown in dedicated sections
        "agent_key",
        "server_name",
        "frequency_sec",
        "execution_mode",  # noise / internal
        "strategy_params",  # shown in dedicated section above
    }
    config_lines = [
        "[CURRENT CONFIG]",
        "These are the ACTIVE values for this session. If the strategy instructions mention different defaults, IGNORE them and use these values instead.",
    ]
    for k, v in config.items():
        if k in _CONFIG_EXCLUDE:
            continue
        config_lines.append(f"{k}: {v}")
    sections.append("\n".join(config_lines))

    sizing_section = _build_sizing_section(config, execution_mode)
    if sizing_section:
        sections.append(sizing_section)

    # Risk state
    rs = risk_state
    max_dd = rs.get("max_drawdown_pct", -1)
    dd_display = (
        f"{rs.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit"
        if max_dd >= 0
        else "disabled"
    )
    risk_lines = [
        "[RISK STATE]",
        f"Total Exposure: ${rs.get('total_exposure', 0):.2f} / ${rs.get('max_position_size', 500):.2f} limit",
        f"Open Executors: {rs.get('executor_count', 0)} / {rs.get('max_open_executors', 5)} limit",
        f"Drawdown: {dd_display}",
        f"Status: {'BLOCKED - ' + rs.get('block_reason', '') if rs.get('is_blocked') else 'ACTIVE'}",
    ]
    sections.append("\n".join(risk_lines))

    # Core skill data (pre-computed)
    for name, data_summary in core_data.items():
        sections.append(f"[CORE DATA - {name}]\n{data_summary}")

    if barrier_closes_section:
        sections.append(barrier_closes_section)

    # Journal -- compact memory
    if learnings:
        sections.append(
            f"[LEARNINGS — do NOT repeat these, only add genuinely new insights]\n{learnings}"
        )
    if summary:
        sections.append(f"[CURRENT STATUS]\n{summary}")
    if digest_boundary and digest_interval > 0:
        sections.append(
            f"[DIGEST BOUNDARY — tick #{tick_number}]\n"
            f"Every-{digest_interval}-tick rollup tick. If this tick is hold-only (no open/close/flip, "
            f"not risk-blocked), send ONE compact digest notification per strategy NOTIFICATIONS rules. "
            f"Synthesize from [RECENT DECISIONS] below — do not call trading_agent_journal_read."
        )
    if recent_decisions:
        recent_label = (
            f"last {digest_interval} decisions"
            if digest_boundary and digest_interval > 0
            else "last 3 snapshots"
        )
        sections.append(f"[RECENT DECISIONS — {recent_label}]\n{recent_decisions}")

    return "\n\n".join(sections)
