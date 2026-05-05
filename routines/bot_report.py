"""Bot Performance Report — Clean 2-message report with USD values and rebate tracking."""

import asyncio
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

REBATE_RATE = 0.00015  # 0.015% of volume


class Config(BaseModel):
    """Generate a formatted performance report for all active bots."""

    quote_currency: str = Field(
        default="BRL",
        description="Quote currency of the bots (for USD conversion)",
    )
    target_chat_id: int | None = Field(
        default=None,
        description="Send report to this chat ID instead of the current chat (e.g. a group chat)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt(n: float, decimals: int = 2) -> str:
    if abs(n) >= 1_000:
        return f"{n:,.{decimals}f}"
    return f"{n:.{decimals}f}"


def _sign(n: float) -> str:
    return "+" if n >= 0 else "-"


def _emoji(n: float) -> str:
    return "🟢" if n > 0 else "🔴" if n < 0 else "⚪"


def _md_escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in special:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


async def _get_bots_data(client) -> dict:
    data = await client.bot_orchestration.get_active_bots_status()
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return {}


async def _get_bot_runs(client) -> dict:
    runs = {}
    try:
        data = await client.bot_orchestration.get_bot_runs()
        if isinstance(data, dict) and "data" in data:
            for run in data["data"]:
                if run.get("deployment_status") == "DEPLOYED" and run.get("deployed_at"):
                    runs[run["bot_name"]] = run["deployed_at"]
    except Exception:
        pass
    return runs


async def _get_prices(client, connector: str, pairs: list[str]) -> dict[str, float]:
    try:
        result = await client.market_data.get_prices(
            connector_name=connector, trading_pairs=",".join(pairs)
        )
        return result.get("prices", {}) if result else {}
    except Exception as e:
        logger.warning(f"Failed to get prices: {e}")
        return {}


async def _get_balances(client) -> dict:
    """Fetch portfolio balances from all accounts."""
    try:
        return await client.portfolio.get_state(refresh=False)
    except Exception as e:
        logger.warning(f"Failed to get balances: {e}")
        return {}


async def _get_market_volume_since(
    client, connector: str, pair: str, since_dt: datetime | None
) -> tuple[float, float]:
    """Return (base_volume, quote_volume) accumulated from candles since *since_dt*.

    Uses 1h candles spanning from *since_dt* to now so the market volume window
    matches the bot's runtime, giving an accurate market-share calculation.
    Falls back to a single 1d candle when *since_dt* is unavailable.
    """
    try:
        if since_dt:
            now = datetime.now(timezone.utc)
            days = max(1, int((now - since_dt).total_seconds() / 86400) + 1)
            candles = await client.market_data.get_candles_last_days(
                connector_name=connector,
                trading_pair=pair,
                days=days,
                interval="1h",
            )
        else:
            candles = await client.market_data.get_candles(
                connector_name=connector,
                trading_pair=pair,
                interval="1d",
                max_records=1,
            )

        if candles:
            data = candles if isinstance(candles, list) else candles.get("data", [])
            if not data:
                return 0.0, 0.0

            since_ts = since_dt.timestamp() if since_dt else 0
            total_base = 0.0
            total_quote = 0.0
            for row in data:
                # Only count candles that start at or after the deploy time
                ts = float(row.get("timestamp", 0) or 0)
                # Normalise ms → s if needed
                if ts > 1e12:
                    ts /= 1000
                if ts >= since_ts:
                    total_base += float(row.get("volume", 0) or 0)
                    total_quote += float(row.get("quote_asset_volume", 0) or 0)
            return total_base, total_quote
    except Exception as e:
        logger.warning(f"Failed to get volume for {pair}: {e}")
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _build_report(
    bots_data: dict,
    bot_runs: dict,
    prices: dict[str, float],
    volumes_since: dict[str, tuple[float, float]],
    quote_usd_rate: float,
    quote_currency: str = "BRL",
    ctrl_configs: dict | None = None,
    balances: dict | None = None,
) -> list[str]:
    ctrl_configs = ctrl_configs or {}

    # ---- Parse controller data ----
    controllers = []

    for bot_name, bot_data in bots_data.items():
        if isinstance(bot_data, str):
            continue

        performance = {}
        if isinstance(bot_data, dict):
            performance = bot_data.get("performance", {})
        elif isinstance(bot_data, list):
            performance = {f"ctrl_{i}": {"performance": c} for i, c in enumerate(bot_data)}

        for ctrl_name, ctrl_data in performance.items():
            if not isinstance(ctrl_data, dict):
                continue
            perf = ctrl_data.get("performance", ctrl_data)
            cfg = ctrl_configs.get(ctrl_name, {})
            trading_pair = cfg.get("trading_pair", "") or perf.get("trading_pair", "")

            vol = float(perf.get("volume_traded", 0) or 0)
            realized = float(perf.get("realized_pnl_quote", 0) or 0)
            unrealized = float(perf.get("unrealized_pnl_quote", 0) or 0)

            controllers.append({
                "id": ctrl_name,
                "bot": bot_name,
                "pair": trading_pair,
                "vol": vol,
                "realized": realized,
                "unrealized": unrealized,
                "net": realized + unrealized,
            })

    if not controllers:
        return ["No controller data found\\."]

    # ---- Runtime ----
    earliest_dt = None
    for bn in {c["bot"] for c in controllers}:
        ts = bot_runs.get(bn)
        if ts:
            try:
                ts_str = str(ts)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts_str)
                if earliest_dt is None or dt < earliest_dt:
                    earliest_dt = dt
            except (ValueError, TypeError):
                pass

    now_dt = datetime.now(timezone.utc)
    runtime_h = (now_dt - earliest_dt).total_seconds() / 3600 if earliest_dt else 0
    if earliest_dt:
        start_str = earliest_dt.strftime("%b %d, %H:%M UTC")
    else:
        start_str = "N/A"

    # ---- Totals ----
    total_vol_q = sum(c["vol"] for c in controllers)
    total_realized_q = sum(c["realized"] for c in controllers)
    total_unrealized_q = sum(c["unrealized"] for c in controllers)
    total_net_q = total_realized_q + total_unrealized_q

    to_usd = quote_usd_rate
    total_vol = total_vol_q * to_usd
    total_realized = total_realized_q * to_usd
    total_unrealized = total_unrealized_q * to_usd
    total_net = total_net_q * to_usd
    total_rebate = total_vol * REBATE_RATE

    vol_hr = total_vol / runtime_h if runtime_h > 0 else 0
    realized_hr = total_realized / runtime_h if runtime_h > 0 else 0
    rebate_hr = total_rebate / runtime_h if runtime_h > 0 else 0
    net_after_rebate = total_net + total_rebate

    # ---- Market volumes ----
    unique_pairs = {c["pair"] for c in controllers if c["pair"]}
    market_lines = []
    for pair in sorted(unique_pairs):
        vol_data = volumes_since.get(pair, (0, 0))
        vol_24h_base, vol_24h_quote = vol_data if isinstance(vol_data, tuple) else (vol_data, 0)
        if vol_24h_base <= 0 and vol_24h_quote <= 0:
            continue
        base_token = pair.split("-")[0]
        quote_token = pair.split("-")[1] if "-" in pair else ""

        if quote_token.upper() not in ("USD", "USDT", "USDC", "BUSD", "FDUSD", ""):
            if vol_24h_quote > 0:
                mkt_usd = vol_24h_quote * to_usd
            else:
                bp = prices.get(f"{base_token}-USDT", 0) or prices.get(f"{base_token}-USD", 0)
                mkt_usd = vol_24h_base * bp if bp else 0
        elif base_token.upper() in ("USDT", "USDC", "BUSD", "DAI", "FDUSD"):
            mkt_usd = vol_24h_base
        else:
            bp = prices.get(f"{base_token}-USDT", 0) or prices.get(f"{base_token}-USD", 0)
            mkt_usd = vol_24h_base * bp if bp else 0
        our_vol = sum(c["vol"] for c in controllers if c["pair"] == pair) * to_usd
        share = (our_vol / mkt_usd * 100) if mkt_usd > 0 else 0
        market_lines.append(
            f"  {_md_escape(pair)}: mkt \\${_md_escape(_fmt(mkt_usd))} "
            f"\\| ours \\${_md_escape(_fmt(our_vol))} \\({_md_escape(f'{share:.2f}')}%\\)"
        )

    # ---- FX rate display ----
    qc = quote_currency.upper()
    if qc not in ("USD", "USDT"):
        fx_val = _fmt(1 / to_usd if to_usd > 0 else 0)
        fx_display = f"1 USD \\= {_md_escape(fx_val)} {_md_escape(qc)}"
    else:
        fx_display = ""

    # ---- MSG 1: Overview ----
    num_bots = len({c["bot"] for c in controllers})
    num_ctrls = len(controllers)

    # ---- Balance summary ----
    balance_lines = []
    total_balance_usd = 0.0
    if balances:
        token_totals: dict[str, float] = defaultdict(float)
        token_values: dict[str, float] = defaultdict(float)
        for account_data in balances.values():
            for connector_balances in account_data.values():
                if not isinstance(connector_balances, list):
                    continue
                for b in connector_balances:
                    token = b.get("token", "")
                    value = float(b.get("value", 0) or 0)
                    units = float(b.get("units", 0) or 0)
                    if value > 0:
                        token_totals[token] += units
                        token_values[token] += value
        total_balance_usd = sum(token_values.values())
        # Top tokens by value
        sorted_tokens = sorted(token_values.items(), key=lambda x: x[1], reverse=True)
        for token, value in sorted_tokens[:5]:
            pct = (value / total_balance_usd * 100) if total_balance_usd > 0 else 0
            balance_lines.append(
                f"  {_md_escape(token)}: \\${_md_escape(_fmt(value))} \\({_md_escape(f'{pct:.0f}')}%\\)"
            )

    msg1_parts = [
        f"*📊 TRADING REPORT*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"⏱ {_md_escape(f'{runtime_h:.1f}')}h runtime \\(since {_md_escape(start_str)}\\)",
        f"🤖 {num_bots} bots \\| {num_ctrls} controllers",
    ]
    if fx_display:
        msg1_parts.append(f"💱 {fx_display}")

    if balance_lines:
        msg1_parts += [
            f"",
            f"💰 *Balance: \\${_md_escape(_fmt(total_balance_usd))}*",
        ] + balance_lines

    msg1_parts += [
        f"",
        f"{_emoji(total_net)} *Performance \\(USD\\)*",
        f"Volume: \\${_md_escape(_fmt(total_vol))}",
        f"Realized: {_md_escape(_sign(total_realized))}\\${_md_escape(_fmt(abs(total_realized)))}",
        f"Unrealized: {_md_escape(_sign(total_unrealized))}\\${_md_escape(_fmt(abs(total_unrealized)))}",
        f"Net PnL: {_md_escape(_sign(total_net))}\\${_md_escape(_fmt(abs(total_net)))}",
        f"Rebate: \\+\\${_md_escape(_fmt(total_rebate))}",
        f"Net\\+Rebate: {_md_escape(_sign(net_after_rebate))}\\${_md_escape(_fmt(abs(net_after_rebate)))}",
        f"",
        f"⏳ *Hourly Rates*",
        f"Vol/hr: \\${_md_escape(_fmt(vol_hr))}",
        f"Realized/hr: {_md_escape(_sign(realized_hr))}\\${_md_escape(_fmt(abs(realized_hr)))}",
        f"Rebate/hr: \\+\\${_md_escape(_fmt(rebate_hr))}",
    ]

    if market_lines:
        msg1_parts += [f"", f"📈 *Market Share*"] + market_lines

    # ---- MSG 2: Controllers grouped by pair ----
    msg2_parts = [
        f"*📋 CONTROLLERS BY PAIR*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    pair_groups: dict[str, list[dict]] = defaultdict(list)
    for c in controllers:
        pair_groups[c["pair"] or "unknown"].append(c)

    sorted_pairs = sorted(pair_groups.items(), key=lambda x: sum(c["vol"] for c in x[1]), reverse=True)

    for pair, ctrls in sorted_pairs:
        grp_vol = sum(c["vol"] for c in ctrls) * to_usd
        grp_realized = sum(c["realized"] for c in ctrls) * to_usd
        grp_unrealized = sum(c["unrealized"] for c in ctrls) * to_usd
        grp_net = grp_realized + grp_unrealized
        grp_rebate = grp_vol * REBATE_RATE
        grp_adj = grp_net + grp_rebate

        pair_escaped = _md_escape(pair)
        msg2_parts.append(f"")
        msg2_parts.append(
            f"▸ *{pair_escaped}* \\({len(ctrls)} ctrls\\) "
            f"{_emoji(grp_adj)} {_md_escape(_sign(grp_adj))}\\${_md_escape(_fmt(abs(grp_adj)))}"
        )

        # Build controller table rows
        sorted_ctrls = sorted(ctrls, key=lambda c: c["vol"], reverse=True)

        def _short_name(ctrl_id: str, pair: str) -> str:
            name = ctrl_id
            if name.startswith("mm_"):
                name = name[3:]
            # Strip all variations of pmm_mister prefix
            name = re.sub(r"pmm_?mister_?", "", name)
            pair_frag = pair.replace("-", "_") if pair else ""
            if pair_frag:
                name = name.replace(pair_frag, "").replace(pair, "")
            while "__" in name:
                name = name.replace("__", "_")
            name = name.strip("_")
            name = re.sub(r"config[_]?(\d+)", r"c\1", name)
            return name or ctrl_id[:20]

        rows = []
        for c in sorted_ctrls:
            vol_usd = c["vol"] * to_usd
            net_usd = c["net"] * to_usd
            reb_usd = vol_usd * REBATE_RATE
            adj_usd = net_usd + reb_usd
            short = _short_name(c["id"], pair)
            rows.append((short, vol_usd, net_usd, adj_usd))

        # Calculate column widths
        name_w = max(len(r[0]) for r in rows)
        vol_strs = [f"${_fmt(r[1])}" for r in rows]
        pnl_strs = [f"{_sign(r[2])}${_fmt(abs(r[2]))}" for r in rows]
        adj_strs = [f"{_sign(r[3])}${_fmt(abs(r[3]))}" for r in rows]

        vol_w = max(len(s) for s in vol_strs)
        pnl_w = max(len(s) for s in pnl_strs)
        adj_w = max(len(s) for s in adj_strs)

        # Header + separator + rows inside a code block
        hdr = f"{'Name':<{name_w}}  {'Vol':>{vol_w}}  {'PnL':>{pnl_w}}  {'Adj':>{adj_w}}"
        sep = "─" * len(hdr)

        table_lines = [hdr, sep]
        for i, (short, vol_usd, net_usd, adj_usd) in enumerate(rows):
            e = "+" if adj_usd >= 0 else "-"
            table_lines.append(
                f"{short:<{name_w}}  {vol_strs[i]:>{vol_w}}  {pnl_strs[i]:>{pnl_w}}  {adj_strs[i]:>{adj_w}}"
            )

        # Pair subtotal row
        sub_vol = f"${_fmt(grp_vol)}"
        sub_pnl = f"{_sign(grp_net)}${_fmt(abs(grp_net))}"
        sub_adj = f"{_sign(grp_adj)}${_fmt(abs(grp_adj))}"
        table_lines.append(sep)
        table_lines.append(
            f"{'TOTAL':<{name_w}}  {sub_vol:>{vol_w}}  {sub_pnl:>{pnl_w}}  {sub_adj:>{adj_w}}"
        )

        msg2_parts.append("```")
        msg2_parts.extend(table_lines)
        msg2_parts.append("```")

    return ["\n".join(msg1_parts), "\n".join(msg2_parts)]


# ---------------------------------------------------------------------------
# Telegram HTTP fallback (when context.bot is unavailable)
# ---------------------------------------------------------------------------


async def _send_telegram_http(chat_id: int, text: str, parse_mode: str = "MarkdownV2") -> bool:
    """Send a message via Telegram HTTP API directly. Used as fallback."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set, cannot send via HTTP fallback")
        return False
    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        async with httpx.AsyncClient(timeout=10) as http_client:
            resp = await http_client.post(url, json=payload)
            data = resp.json()
            if data.get("ok"):
                return True
            # Retry without parse_mode if formatting fails
            if "can't parse" in data.get("description", "").lower():
                payload.pop("parse_mode")
                resp = await http_client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    return True
            logger.error(f"Telegram HTTP send failed: {data.get('description')}")
    except Exception as e:
        logger.error(f"Telegram HTTP fallback error: {e}")
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    if not chat_id:
        return "No chat_id available"

    # Auto-resolve target_chat_id from notes if not explicitly set
    send_to = config.target_chat_id
    if not send_to:
        try:
            from condor.preferences import get_note
            user_data = context.user_data if hasattr(context, "user_data") else {}
            # Look up note for the active server's group chat
            from config_manager import get_config_manager
            server_name = get_config_manager().get_chat_default_server(chat_id)
            if server_name:
                note_val = get_note(user_data, f"server.{server_name}.group_chat_id")
                if note_val:
                    send_to = int(note_val)
        except Exception:
            pass
    if not send_to:
        # Fallback to CONDOR_CHAT_ID env var (the invoking user's chat)
        env_chat = os.environ.get("CONDOR_CHAT_ID", "")
        send_to = int(env_chat) if env_chat else chat_id

    client = await get_client(chat_id, context=context)
    if not client:
        return "Could not connect to API server"

    bots_data, bot_runs, balances = await asyncio.gather(
        _get_bots_data(client),
        _get_bot_runs(client),
        _get_balances(client),
    )

    if not bots_data:
        await context.bot.send_message(chat_id=send_to, text="No active bots found.")
        return "No active bots"

    # Fetch controller configs for trading_pair
    ctrl_configs = {}
    for bot_name in bots_data:
        try:
            configs = await client.controllers.get_bot_controller_configs(bot_name)
            if isinstance(configs, list):
                for cfg in configs:
                    cid = cfg.get("id") or cfg.get("controller_id", "")
                    if cid:
                        ctrl_configs[cid] = cfg
        except Exception:
            pass

    # Determine trading pairs and connector
    trading_pairs = set()
    connector = "binance"
    for bot_data in bots_data.values():
        if not isinstance(bot_data, dict):
            continue
        for ctrl_name, ctrl_data in bot_data.get("performance", {}).items():
            if not isinstance(ctrl_data, dict):
                continue
            cfg = ctrl_configs.get(ctrl_name, {})
            perf = ctrl_data.get("performance", ctrl_data)
            pair = cfg.get("trading_pair", "") or perf.get("trading_pair", "")
            if pair:
                trading_pairs.add(pair)
            conn = cfg.get("connector_name", "") or perf.get("connector_name", "")
            if conn:
                connector = conn.split("_")[0]

    # Build price pairs for USD conversion
    price_pairs = list(trading_pairs)
    for bt in {p.split("-")[0] for p in trading_pairs if "-" in p}:
        if f"{bt}-USDT" not in price_pairs:
            price_pairs.append(f"{bt}-USDT")
    for qt in {p.split("-")[-1] for p in trading_pairs if "-" in p}:
        if qt not in ("USDT", "USD") and f"USDT-{qt}" not in price_pairs:
            price_pairs.append(f"USDT-{qt}")

    prices = await _get_prices(client, connector, price_pairs)

    # FX rate fallback
    qc = config.quote_currency.upper()
    fx_pair = f"USDT-{qc}" if qc not in ("USD", "USDT") else ""
    if fx_pair and not prices.get(fx_pair):
        fx_prices = await _get_prices(client, "binance", [fx_pair])
        prices.update(fx_prices)

    # Market volumes since deploy
    earliest_dt = None
    for bn in bots_data:
        ts = bot_runs.get(bn)
        if ts:
            try:
                ts_str = str(ts)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts_str)
                if earliest_dt is None or dt < earliest_dt:
                    earliest_dt = dt
            except (ValueError, TypeError):
                pass

    volumes_since = {}
    vol_tasks = {
        pair: _get_market_volume_since(client, connector, pair, earliest_dt)
        for pair in trading_pairs
    }
    vol_results = await asyncio.gather(*vol_tasks.values())
    for pair, vol_tuple in zip(vol_tasks.keys(), vol_results):
        volumes_since[pair] = vol_tuple

    # Quote → USD rate
    quote_usd_rate = 1.0
    if qc not in ("USD", "USDT"):
        usdt_quote = prices.get(f"USDT-{qc}", 0)
        if usdt_quote and usdt_quote > 0:
            quote_usd_rate = 1.0 / usdt_quote
        else:
            quote_usdt = prices.get(f"{qc}-USDT", 0)
            if quote_usdt and quote_usdt > 0:
                quote_usd_rate = quote_usdt

    # Build and send
    messages = _build_report(
        bots_data, bot_runs, prices, volumes_since, quote_usd_rate,
        quote_currency=qc, ctrl_configs=ctrl_configs, balances=balances,
    )

    sent = 0
    for i, msg in enumerate(messages):
        if i > 0:
            await asyncio.sleep(1)
        for attempt in range(3):
            try:
                await context.bot.send_message(
                    chat_id=send_to, text=msg, parse_mode="MarkdownV2",
                )
                sent += 1
                break
            except Exception as e:
                err = str(e).lower()
                if "flood control" in err or "retry in" in err:
                    match = re.search(r"retry in (\d+)", err)
                    wait = int(match.group(1)) + 1 if match else 10
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Failed to send msg {i} via bot: {e}")
                    # Fallback: send via HTTP directly to Telegram API
                    if await _send_telegram_http(send_to, msg):
                        sent += 1
                    break

    # Strip MarkdownV2 escaping for plain-text return
    def _strip_md(text: str) -> str:
        return text.replace("\\", "")

    plain_report = "\n\n".join(_strip_md(m) for m in messages)

    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder("Trading Report")
        builder.source("routine", "bot_report").tags(["bots", "performance"])
        builder.markdown(plain_report)
        builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return f"Report sent: {sent}/{len(messages)} messages\n\n{plain_report}"
