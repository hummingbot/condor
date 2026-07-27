"""Generate a deterministic Hummingbot trading report component gallery."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from condor.reports import ReportBuilder
from condor.reports.footprint import build_estimated_footprint_figure

CATEGORY = "Developer Tools"

SIMULATION_START = datetime(2026, 6, 1, tzinfo=timezone.utc)


class Config(BaseModel):
    """Create a ReportBuilder component gallery from simulated trading data."""

    history_hours: int = Field(
        default=168,
        ge=72,
        le=336,
        description="Number of hourly BTC-USDT candles to include",
    )


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _rolling_mean(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rolling_std(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    return math.sqrt(sum((value - mean) ** 2 for value in window) / period)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * percentile / 100
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (index - lower) * (
        sorted_values[upper] - sorted_values[lower]
    )


def _return_distribution_stats(candles: list[dict]) -> dict[str, Any]:
    returns = [float(row["return_pct"]) for row in candles[1:]]
    sorted_returns = sorted(returns)
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = (
        sum((value - mean) ** 2 for value in returns) / len(returns) if returns else 0.0
    )
    return {
        "returns": returns,
        "count": len(returns),
        "p5": _percentile(sorted_returns, 5),
        "p95": _percentile(sorted_returns, 95),
        "std": math.sqrt(variance),
    }


def _make_candles(count: int) -> list[dict]:
    rows: list[dict] = []
    closes: list[float] = []
    returns: list[float] = []
    cumulative_base = 0.0
    cumulative_quote = 0.0
    ema = None
    previous_close = 98_000.0

    for index in range(count):
        timestamp = SIMULATION_START + timedelta(hours=index)
        center = (
            98_000
            + index * 13.4
            + math.sin(index / 6.5) * 260
            + math.sin(index / 17) * 130
        )
        open_price = previous_close
        close = center + math.sin(index * 1.7) * 45
        high = max(open_price, close) + 65 + (index % 7) * 11
        low = min(open_price, close) - 58 - (index % 5) * 13
        base_volume = 18 + (index * 7 % 31) + abs(math.sin(index / 4)) * 22
        quote_volume = base_volume * close
        cumulative_base += base_volume
        cumulative_quote += quote_volume
        vwap = cumulative_quote / cumulative_base
        ema = close if ema is None else close * (2 / 21) + ema * (19 / 21)
        return_pct = (close / previous_close - 1) * 100 if rows else 0.0
        closes.append(close)
        if rows:
            returns.append(return_pct)
        sma_50 = _rolling_mean(closes, 50)
        volatility = _rolling_std(returns, 24)
        if volatility is not None and volatility > 0.18:
            regime = "Volatile"
        elif sma_50 is not None and abs(close / sma_50 - 1) > 0.004:
            regime = "Trending"
        else:
            regime = "Ranging"
        rows.append(
            {
                "timestamp": _iso(timestamp),
                "pair": "BTC-USDT",
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "base_volume": round(base_volume, 4),
                "quote_volume": round(quote_volume, 2),
                "ema_20": round(ema, 2),
                "sma_50": round(sma_50, 2) if sma_50 is not None else None,
                "vwap": round(vwap, 2),
                "return_pct": round(return_pct, 4),
                "range_pct": round((high - low) / close * 100, 4),
                "rolling_volatility_pct": (
                    round(volatility, 4) if volatility is not None else None
                ),
                "regime": regime,
            }
        )
        previous_close = close
    return rows


def _make_bot_fleet() -> list[dict]:
    pairs = (
        ("BTC-USDT", "btc", 27_500, 74),
        ("ETH-USDT", "eth", 19_200, 61),
        ("SOL-USDT", "sol", 12_600, 45),
    )
    profiles = (
        ("Tight", "tight", 1.18, 1.25),
        ("Balanced", "alpha", 1.0, 1.0),
        ("Defensive", "defensive", 0.72, 0.58),
    )
    rows = []
    for pair_index, (pair, symbol, volume_base, pnl_base) in enumerate(pairs):
        for profile_index, (profile, suffix, volume_factor, risk_factor) in enumerate(
            profiles
        ):
            primary = pair == "BTC-USDT" and profile == "Balanced"
            bot_status = "RUNNING"
            controller_status = "RUNNING"
            if pair == "SOL-USDT" and profile == "Tight":
                controller_status = "PAUSED"
            elif pair == "ETH-USDT" and profile == "Defensive":
                bot_status = "STOPPED"
                controller_status = "STOPPED"
            realized = pnl_base * risk_factor + (profile_index - pair_index) * 8.5
            unrealized = 20.4 if primary else (pair_index - profile_index) * 4.7
            rows.append(
                {
                    "bot_name": f"{symbol}_mm_{suffix}",
                    "controller_config": f"pmm_{symbol}_{suffix}",
                    "profile": profile,
                    "pair": pair,
                    "connector": "binance_perpetual",
                    "bot_status": bot_status,
                    "controller_status": controller_status,
                    "active_executors": (
                        (4 if primary else 1 + (pair_index + profile_index) % 4)
                        if controller_status == "RUNNING"
                        else 0
                    ),
                    "closed_executors": 8 + pair_index * 2 + profile_index,
                    "trade_count": (
                        48 if primary else 22 + pair_index * 7 + profile_index * 5
                    ),
                    "session_volume_quote": round(volume_base * volume_factor, 2),
                    "realized_pnl_quote": round(realized, 2),
                    "unrealized_pnl_quote": round(unrealized, 2),
                    "net_pnl_quote": round(realized + unrealized, 2),
                    "max_drawdown_quote": round(
                        32 + risk_factor * 34 + pair_index * 11, 2
                    ),
                    "fill_rate_pct": round(
                        68 + profile_index * 7 - pair_index * 2.5, 1
                    ),
                    "inventory_skew_pct": round(
                        (pair_index - 1) * 5.2 + profile_index * 2.1, 1
                    ),
                }
            )
    return rows


def _distance_band(distance_bps: float) -> str:
    if distance_bps <= 5:
        return "0-5 bps"
    if distance_bps <= 10:
        return "5-10 bps"
    if distance_bps <= 25:
        return "10-25 bps"
    if distance_bps <= 50:
        return "25-50 bps"
    return "50+ bps"


def _size_bucket(quote_volume: float) -> str:
    if quote_volume < 2_000:
        return "Under $2k"
    if quote_volume < 5_000:
        return "$2k-$5k"
    if quote_volume < 10_000:
        return "$5k-$10k"
    return "$10k+"


def _make_order_book(mid: float) -> list[dict]:
    rows = []
    for side in ("Bid", "Ask"):
        cumulative_amount = 0.0
        cumulative_notional = 0.0
        direction = -1 if side == "Bid" else 1
        for level in range(1, 21):
            distance_bps = 0.8 + (level - 1) * 2.7
            price = mid * (1 + direction * distance_bps / 10_000)
            amount = 0.12 + ((level * 7 + (0 if side == "Bid" else 3)) % 13) * 0.045
            notional = price * amount
            cumulative_amount += amount
            cumulative_notional += notional
            rows.append(
                {
                    "side": side,
                    "level": level,
                    "price": round(price, 2),
                    "amount": round(amount, 4),
                    "notional": round(notional, 2),
                    "cumulative_amount": round(cumulative_amount, 4),
                    "cumulative_notional": round(cumulative_notional, 2),
                    "distance_bps": round(distance_bps, 2),
                    "distance_band": _distance_band(distance_bps),
                }
            )
    return rows


def _make_market_scan() -> list[dict]:
    markets = (
        ("BTC-USDT", "Mature", 0.102, 18_400_000_000, 0.22, 3.4),
        ("ETH-USDT", "Mature", 0.128, 9_700_000_000, 0.25, 4.1),
        ("SOL-USDT", "Mature", 0.214, 3_150_000_000, 0.31, 6.8),
        ("XRP-USDT", "Mature", 0.176, 2_480_000_000, 0.29, 5.7),
        ("DOGE-USDT", "Mature", 0.238, 1_920_000_000, 0.38, 7.9),
        ("BNB-USDT", "Mature", 0.119, 1_260_000_000, 0.27, 3.8),
        ("WIF-USDT", "Degen", 0.842, 385_000_000, 1.18, 22.6),
        ("PEPE-USDT", "Degen", 0.735, 610_000_000, 1.06, 19.8),
        ("BONK-USDT", "Degen", 0.914, 248_000_000, 1.31, 25.4),
        ("FLOKI-USDT", "Degen", 0.681, 172_000_000, 0.98, 17.7),
        ("POPCAT-USDT", "Degen", 1.126, 96_000_000, 1.46, 31.2),
        ("MEW-USDT", "Degen", 1.284, 74_000_000, 1.57, 35.8),
        ("AVAX-USDT", "Other", 0.342, 780_000_000, 0.58, 10.5),
        ("LINK-USDT", "Other", 0.297, 690_000_000, 0.49, 9.2),
        ("SUI-USDT", "Other", 0.468, 540_000_000, 0.71, 13.8),
        ("APT-USDT", "Other", 0.415, 315_000_000, 0.66, 12.4),
        ("ARB-USDT", "Other", 0.386, 286_000_000, 0.61, 11.7),
        ("OP-USDT", "Other", 0.358, 252_000_000, 0.55, 10.9),
    )
    rows = []
    for index, (
        pair,
        classification,
        natr,
        volume,
        bucket_cv,
        price_range,
    ) in enumerate(markets):
        rows.append(
            {
                "trading_pair": pair,
                "symbol_label": pair.removesuffix("-USDT"),
                "classification": classification,
                "natr_mean": natr,
                "volume_24h_usd": volume,
                "bucket_cv": bucket_cv,
                "marker_size": round(max(9, 25 - bucket_cv * 8), 1),
                "price_change_24h": round(math.sin(index * 1.3) * 8.5, 2),
                "natr_cv": round(0.2 + bucket_cv * 0.45, 3),
                "natr_spike_ratio": round(1.25 + bucket_cv * 1.7, 2),
                "price_range_pct": price_range,
            }
        )
    return rows


def _make_cross_exchange_books(mid: float) -> list[dict]:
    venues = (
        ("Binance", 0.0, 0.75),
        ("KuCoin", 1.9, 1.05),
        ("OKX", -1.3, 0.90),
    )
    rows = []
    reference_mid = mid
    for exchange_rank, (exchange, offset_bps, half_spread_bps) in enumerate(venues):
        venue_mid = mid * (1 + offset_bps / 10_000)
        for side_index, side in enumerate(("Bid", "Ask")):
            direction = -1 if side == "Bid" else 1
            cumulative_amount = 0.0
            for level in range(1, 13):
                distance_bps = half_spread_bps + (level - 1) * 1.8
                price = venue_mid * (1 + direction * distance_bps / 10_000)
                amount = (
                    0.14
                    + ((level * 5 + exchange_rank * 3 + side_index * 2) % 11) * 0.038
                )
                cumulative_amount += amount
                rows.append(
                    {
                        "exchange": exchange,
                        "exchange_rank": exchange_rank,
                        "side": side,
                        "level": level,
                        "price": round(price, 2),
                        "amount": round(amount, 4),
                        "cumulative_amount": round(cumulative_amount, 4),
                        "mid_price": round(venue_mid, 2),
                        "spread_bps": round(half_spread_bps * 2, 2),
                        "reference_exchange": venues[0][0],
                        "reference_mid": round(reference_mid, 2),
                    }
                )
    return rows


def _make_active_orders(order_book: list[dict], executors: list[dict]) -> list[dict]:
    rows = []
    chosen_levels = (2, 5, 9, 14)
    for side_index, (book_side, order_side) in enumerate(
        (("Bid", "BUY"), ("Ask", "SELL"))
    ):
        side_rows = [row for row in order_book if row["side"] == book_side]
        owners = [
            row
            for row in executors
            if row["status"] == "RUNNING" and row["side"] == order_side
        ]
        for offset, level in enumerate(chosen_levels):
            book = side_rows[level - 1]
            amount = round(0.08 + offset * 0.035, 4)
            order_number = side_index * len(chosen_levels) + offset + 1
            rows.append(
                {
                    "order_id": f"HB-ORD-{order_number:03d}",
                    "executor_id": owners[offset % len(owners)]["executor_id"],
                    "side": order_side,
                    "price": book["price"],
                    "amount": amount,
                    "notional": round(book["price"] * amount, 2),
                    "distance_bps": (
                        -book["distance_bps"]
                        if book_side == "Bid"
                        else book["distance_bps"]
                    ),
                    "age_seconds": 18 + order_number * 11,
                    "status": "OPEN",
                }
            )
    return rows


def _make_pnl_timeline(
    as_of: datetime,
    candles: list[dict],
    executors: list[dict],
    trades: list[dict],
) -> list[dict]:
    rows = []
    peak = 0.0
    for index in range(48):
        timestamp = as_of - timedelta(hours=47 - index)
        candle_index = round((timestamp - SIMULATION_START).total_seconds() / 3600)
        market_price = candles[max(0, min(len(candles) - 1, candle_index))]["close"]
        visible_trades = [
            trade
            for trade in trades
            if datetime.fromisoformat(trade["timestamp"].replace("Z", "+00:00"))
            <= timestamp
        ]
        accrued_fees = sum(trade["fee_quote"] for trade in visible_trades)
        realized = 0.0
        unrealized = 0.0
        signed_inventory = 0.0
        gross_inventory = 0.0
        for executor in executors:
            created_at = datetime.fromisoformat(
                executor["created_at"].replace("Z", "+00:00")
            )
            if created_at > timestamp:
                continue
            closed_at = (
                datetime.fromisoformat(executor["closed_at"].replace("Z", "+00:00"))
                if executor["closed_at"]
                else None
            )
            if closed_at is not None and closed_at <= timestamp:
                realized += executor["net_pnl_quote"]
                continue
            opening_trades = [
                trade
                for trade in visible_trades
                if trade["executor_id"] == executor["executor_id"]
                and trade["position_action"] == "OPEN"
            ]
            if not opening_trades:
                continue
            amount = sum(trade["amount"] for trade in opening_trades)
            entry_price = (
                sum(trade["price"] * trade["amount"] for trade in opening_trades)
                / amount
            )
            executor_fees = sum(trade["fee_quote"] for trade in opening_trades)
            direction = 1 if executor["side"] == "BUY" else -1
            unrealized += (
                market_price - entry_price
            ) * amount * direction - executor_fees
            signed_inventory += amount * direction
            gross_inventory += amount
        realized = round(realized, 2)
        unrealized = round(unrealized, 2)
        net = realized + unrealized
        peak = max(peak, net)
        rows.append(
            {
                "timestamp": _iso(timestamp),
                "realized_pnl_quote": realized,
                "unrealized_pnl_quote": unrealized,
                "fees_quote": round(accrued_fees, 2),
                "net_pnl_quote": round(net, 2),
                "drawdown_quote": round(max(0, peak - net), 2),
                "inventory_skew_pct": round(
                    signed_inventory / gross_inventory * 100 if gross_inventory else 0,
                    2,
                ),
            }
        )
    return rows


def _make_executors(mid: float, as_of: datetime) -> list[dict]:
    close_types = (
        "TAKE_PROFIT",
        "TIME_LIMIT",
        "STOP_LOSS",
        "TAKE_PROFIT",
        "EARLY_STOP",
        "TIME_LIMIT",
        "TAKE_PROFIT",
        "STOP_LOSS",
    )
    rows = []
    for index in range(12):
        running = index < 4
        side = "BUY" if index % 2 == 0 else "SELL"
        entry = mid * (1 + (index - 5) * 0.00045)
        move = (1 if side == "BUY" else -1) * (0.0007 + (index % 4) * 0.00035)
        close = None if running else entry * (1 + move)
        amount = 0.055 + index * 0.009
        created_at = as_of - timedelta(hours=48 - index * 2)
        duration_seconds = (
            int((as_of - created_at).total_seconds())
            if running
            else 8 * 3600 + (index - 4) * 1800
        )
        rows.append(
            {
                "executor_id": f"EXEC-{index + 1:03d}",
                "status": "RUNNING" if running else "TERMINATED",
                "side": side,
                "created_at": _iso(created_at),
                "closed_at": None,
                "entry_price": round(entry, 2),
                "close_price": round(close, 2) if close is not None else None,
                "amount": round(amount, 4),
                "filled_quote": 0.0,
                "net_pnl_quote": 0.0,
                "net_pnl_pct": 0.0,
                "fees_quote": 0.0,
                "duration_seconds": duration_seconds,
                "close_type": "" if running else close_types[index - 4],
                "trade_count": 0,
                "order_count": 0,
            }
        )
    return rows


def _make_trades(candles: list[dict], executors: list[dict]) -> list[dict]:
    rows = []
    for executor_index, executor in enumerate(executors):
        created_at = datetime.fromisoformat(
            executor["created_at"].replace("Z", "+00:00")
        )
        executor_trades = []
        position_amount = executor["amount"]
        for fill_index in range(4):
            trade_index = executor_index * 4 + fill_index
            closing = executor["status"] == "TERMINATED" and fill_index == 3
            fill_time = created_at + timedelta(
                seconds=(
                    executor["duration_seconds"]
                    if closing
                    else executor["duration_seconds"] * (fill_index + 1) / 5
                )
            )
            candle_index = round((fill_time - SIMULATION_START).total_seconds() / 3600)
            candle = candles[max(0, min(len(candles) - 1, candle_index))]
            trade_side = (
                ("SELL" if executor["side"] == "BUY" else "BUY")
                if closing
                else executor["side"]
            )
            direction = 1 if trade_side == "BUY" else -1
            if closing:
                opening_amount = sum(row["amount"] for row in executor_trades)
                opening_price = (
                    sum(row["price"] * row["amount"] for row in executor_trades)
                    / opening_amount
                )
                close_return = {
                    "TAKE_PROFIT": 0.0015,
                    "STOP_LOSS": -0.0012,
                    "EARLY_STOP": -0.0004,
                    "TIME_LIMIT": 0.00025 if executor_index % 2 == 0 else -0.00015,
                }[executor["close_type"]]
                position_direction = 1 if executor["side"] == "BUY" else -1
                reference_price = opening_price * (
                    1 + position_direction * close_return
                )
            else:
                reference_price = candle["close"]
            estimated_quote = reference_price * (
                position_amount
                if closing
                else position_amount / (3 if executor["status"] == "TERMINATED" else 4)
            )
            size_impact = max(0, estimated_quote - 1_000) / 9_000 * 0.65
            liquidity = "TAKER" if trade_index % 6 == 0 else "MAKER"
            liquidity_cost = 0.28 if liquidity == "TAKER" else -0.18
            noise = ((trade_index * 7) % 9 - 4) * 0.07
            execution_cost_bps = max(-0.45, size_impact + liquidity_cost + noise)
            price = reference_price * (1 + direction * execution_cost_bps / 10_000)
            amount = (
                position_amount
                if closing
                else position_amount / (3 if executor["status"] == "TERMINATED" else 4)
            )
            quote_volume = price * amount
            fee_rate = 0.00018 if liquidity == "MAKER" else 0.00045
            trade = {
                "trade_id": f"HB-TRD-{trade_index + 1:04d}",
                "order_id": f"HB-FILL-{trade_index + 1:04d}",
                "executor_id": executor["executor_id"],
                "timestamp": _iso(fill_time),
                "side": trade_side,
                "position_action": "CLOSE" if closing else "OPEN",
                "order_type": "LIMIT_MAKER" if liquidity == "MAKER" else "MARKET",
                "price": round(price, 2),
                "amount": round(amount, 6),
                "quote_volume": round(quote_volume, 2),
                "fee_quote": round(quote_volume * fee_rate, 4),
                "execution_cost_bps": round(execution_cost_bps, 3),
                "size_bucket": _size_bucket(quote_volume),
                "liquidity": liquidity,
                "connector": "binance_perpetual",
            }
            rows.append(trade)
            executor_trades.append(trade)

        opening_trades = [
            row for row in executor_trades if row["position_action"] == "OPEN"
        ]
        closing_trade = next(
            (row for row in executor_trades if row["position_action"] == "CLOSE"),
            None,
        )
        total_amount = sum(row["amount"] for row in opening_trades)
        total_quote = sum(row["quote_volume"] for row in executor_trades)
        entry_price = (
            sum(row["price"] * row["amount"] for row in opening_trades) / total_amount
        )
        fees = sum(row["fee_quote"] for row in executor_trades)
        exit_price = closing_trade["price"] if closing_trade else candles[-1]["close"]
        direction = 1 if executor["side"] == "BUY" else -1
        gross_pnl = (exit_price - entry_price) * total_amount * direction
        net_pnl = gross_pnl - fees
        executor.update(
            {
                "entry_price": round(entry_price, 2),
                "close_price": round(exit_price, 2) if closing_trade else None,
                "closed_at": closing_trade["timestamp"] if closing_trade else None,
                "amount": round(total_amount, 6),
                "filled_quote": round(total_quote, 2),
                "net_pnl_quote": round(net_pnl, 2),
                "net_pnl_pct": round(net_pnl / total_quote * 100, 4),
                "fees_quote": round(fees, 4),
                "trade_count": len(executor_trades),
                "order_count": len(executor_trades) + 2,
                "pnl_sign": "Profit" if net_pnl >= 0 else "Loss",
                "pnl_label": f"${net_pnl:+,.2f}",
            }
        )
    rows.sort(key=lambda row: row["timestamp"])
    return rows


def make_gallery_data(history_hours: int = 168) -> dict[str, list[dict]]:
    """Create one coherent, deterministic Hummingbot simulation."""
    candles = _make_candles(history_hours)
    mid = candles[-1]["close"]
    order_book = _make_order_book(mid)
    as_of = datetime.fromisoformat(candles[-1]["timestamp"].replace("Z", "+00:00"))
    bot_fleet = _make_bot_fleet()
    executors = _make_executors(mid, as_of)
    trades = _make_trades(candles, executors)
    running = [row for row in executors if row["status"] == "RUNNING"]
    terminated = [row for row in executors if row["status"] == "TERMINATED"]
    pnl_timeline = _make_pnl_timeline(as_of, candles, executors, trades)
    primary_bot = next(row for row in bot_fleet if row["bot_name"] == "btc_mm_alpha")
    final_pnl = pnl_timeline[-1]
    primary_bot.update(
        {
            "active_executors": len(running),
            "closed_executors": len(terminated),
            "trade_count": len(trades),
            "session_volume_quote": round(
                sum(row["quote_volume"] for row in trades), 2
            ),
            "realized_pnl_quote": round(
                sum(row["net_pnl_quote"] for row in terminated), 2
            ),
            "unrealized_pnl_quote": round(
                sum(row["net_pnl_quote"] for row in running), 2
            ),
            "net_pnl_quote": final_pnl["net_pnl_quote"],
            "max_drawdown_quote": max(row["drawdown_quote"] for row in pnl_timeline),
            "inventory_skew_pct": final_pnl["inventory_skew_pct"],
        }
    )
    for row in bot_fleet:
        symbol = row["pair"].split("-")[0]
        drawdown_limit = {"Tight": 150.0, "Balanced": 180.0, "Defensive": 220.0}[
            row["profile"]
        ]
        utilization = row["max_drawdown_quote"] / drawdown_limit * 100
        row.update(
            {
                "display_name": f"{symbol} - {row['profile']}",
                "pnl_sign": "Profit" if row["net_pnl_quote"] >= 0 else "Loss",
                "pnl_label": f"${row['net_pnl_quote']:+,.0f}",
                "drawdown_limit_quote": drawdown_limit,
                "drawdown_utilization_pct": round(utilization, 1),
                "risk_state": (
                    "Healthy"
                    if utilization < 50
                    else "Warning" if utilization < 80 else "At limit"
                ),
                "risk_label": (
                    f"{utilization:.0f}% - ${row['max_drawdown_quote']:,.0f} / "
                    f"${drawdown_limit:,.0f}"
                ),
            }
        )
    max_drawdown = primary_bot["max_drawdown_quote"]
    inventory = abs(primary_bot["inventory_skew_pct"])
    risk_limits = [
        {
            "Limit": "Global drawdown",
            "Current": f"${max_drawdown:,.2f}",
            "Threshold": "$500.00",
            "State": "Healthy" if max_drawdown < 500 else "Breached",
        },
        {
            "Limit": "Controller drawdown",
            "Current": f"${max_drawdown:,.2f}",
            "Threshold": "$180.00",
            "State": "Healthy" if max_drawdown < 180 else "Breached",
        },
        {
            "Limit": "Inventory skew",
            "Current": f"{inventory:.1f}%",
            "Threshold": "20.0%",
            "State": "Healthy" if inventory < 20 else "Breached",
        },
    ]
    return {
        "candles": candles,
        "raw_candles": [dict(row) for row in candles],
        "market_scan": _make_market_scan(),
        "bot_fleet": bot_fleet,
        "order_book": order_book,
        "cross_exchange_books": _make_cross_exchange_books(mid),
        "active_orders": _make_active_orders(order_book, executors),
        "bot_pnl_timeline": pnl_timeline,
        "executors": executors,
        "raw_executors": [dict(row) for row in executors],
        "trades": trades,
        "raw_trades": [dict(row) for row in trades],
        "risk_limits": risk_limits,
    }


def _build_return_distribution_figure(candles: list[dict]):
    import plotly.graph_objects as go

    stats = _return_distribution_stats(candles)
    figure = go.Figure()
    figure.add_trace(
        go.Histogram(
            x=stats["returns"],
            nbinsx=40,
            marker_color="#7c3aed",
            opacity=0.8,
            name="Hourly returns",
        )
    )
    figure.add_vline(
        x=stats["p5"],
        line_dash="dot",
        line_color="#f59e0b",
        annotation_text="p5",
    )
    figure.add_vline(
        x=stats["p95"],
        line_dash="dot",
        line_color="#f59e0b",
        annotation_text="p95",
    )
    figure.add_vline(x=0, line_color="#94a3b8", line_width=1)
    figure.update_layout(
        title="BTC-USDT Hourly Return Distribution",
        xaxis_title="Return per candle (%)",
        yaxis_title="Frequency",
        height=350,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e8e6e3"},
        legend={"orientation": "h", "y": -0.18, "x": 0.5, "xanchor": "center"},
        margin={"l": 65, "r": 35, "t": 65, "b": 85},
    )
    return figure


def _build_cross_exchange_figure(rows: list[dict], pair: str = "BTC-USDT"):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    exchanges = sorted(
        {row["exchange"] for row in rows},
        key=lambda exchange: next(
            row["exchange_rank"] for row in rows if row["exchange"] == exchange
        ),
    )
    reference_exchange = rows[0]["reference_exchange"]
    reference_mid = rows[0]["reference_mid"]
    figure = make_subplots(
        rows=len(exchanges),
        cols=2,
        shared_xaxes="columns",
        vertical_spacing=0.08,
        horizontal_spacing=0.10,
        column_widths=[0.58, 0.42],
    )
    bid_color = "rgba(34, 197, 94, 0.85)"
    ask_color = "rgba(239, 68, 68, 0.85)"
    bps_values = [0.0]

    for row_index, exchange in enumerate(exchanges, start=1):
        exchange_rows = [row for row in rows if row["exchange"] == exchange]
        bids = sorted(
            (row for row in exchange_rows if row["side"] == "Bid"),
            key=lambda row: row["level"],
        )
        asks = sorted(
            (row for row in exchange_rows if row["side"] == "Ask"),
            key=lambda row: row["level"],
        )
        for levels, side, color, fill in (
            (bids, "Bids", bid_color, "rgba(34, 197, 94, 0.12)"),
            (asks, "Asks", ask_color, "rgba(239, 68, 68, 0.12)"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=[level["price"] for level in levels],
                    y=[level["cumulative_amount"] for level in levels],
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=fill,
                    line={"color": color, "width": 2, "shape": "hv"},
                    name=side,
                    legendgroup=side.lower(),
                    showlegend=row_index == 1,
                    hovertemplate=(
                        f"{exchange}<br>Price: %{{x:,.2f}} USDT"
                        "<br>Cumulative: %{y:.4f} BTC<extra></extra>"
                    ),
                ),
                row=row_index,
                col=1,
            )

        bid_bps = (bids[0]["price"] / reference_mid - 1) * 10_000
        ask_bps = (asks[0]["price"] / reference_mid - 1) * 10_000
        mid_bps = (exchange_rows[0]["mid_price"] / reference_mid - 1) * 10_000
        bps_values.extend((bid_bps, ask_bps, mid_bps))
        figure.add_trace(
            go.Scatter(
                x=[bid_bps, ask_bps],
                y=[0, 0],
                mode="lines",
                line={"color": "rgba(148, 163, 184, 0.55)", "width": 4},
                showlegend=False,
                hoverinfo="skip",
            ),
            row=row_index,
            col=2,
        )
        for value, label, color, position, price in (
            (bid_bps, "L1 Bid", bid_color, "bottom center", bids[0]["price"]),
            (ask_bps, "L1 Ask", ask_color, "top center", asks[0]["price"]),
        ):
            figure.add_trace(
                go.Scatter(
                    x=[value],
                    y=[0],
                    mode="markers+text",
                    marker={"color": color, "size": 13},
                    text=[f"{value:+.1f}"],
                    textposition=position,
                    name=label,
                    legendgroup=label.lower().replace(" ", "-"),
                    showlegend=row_index == 1,
                    hovertemplate=(
                        f"{exchange} {label}<br>Price: {price:,.2f} USDT"
                        f"<br>Distance: {value:+.2f} bps<extra></extra>"
                    ),
                ),
                row=row_index,
                col=2,
            )
        if exchange != reference_exchange and abs(mid_bps) > 0.5:
            figure.add_trace(
                go.Scatter(
                    x=[mid_bps],
                    y=[0],
                    mode="markers+text",
                    marker={"color": "#56b4e9", "size": 9, "symbol": "diamond"},
                    text=[f"mid {mid_bps:+.1f}"],
                    textposition="bottom center",
                    showlegend=False,
                    hovertemplate=(
                        f"{exchange} mid: {exchange_rows[0]['mid_price']:,.2f} USDT"
                        f"<br>Distance: {mid_bps:+.2f} bps<extra></extra>"
                    ),
                ),
                row=row_index,
                col=2,
            )
        figure.add_vline(
            x=exchange_rows[0]["mid_price"],
            line={"color": "#94a3b8", "width": 1, "dash": "dot"},
            row=row_index,
            col=1,
        )
        figure.add_vline(
            x=0,
            line={"color": "#94a3b8", "width": 1, "dash": "dot"},
            row=row_index,
            col=2,
        )

    prices = [row["price"] for row in rows]
    price_margin = (max(prices) - min(prices)) * 0.03
    bps_limit = max(2.0, max(abs(value) for value in bps_values) * 1.35)
    for row_index, exchange in enumerate(exchanges, start=1):
        figure.update_xaxes(
            range=[min(prices) - price_margin, max(prices) + price_margin],
            row=row_index,
            col=1,
        )
        figure.update_xaxes(
            range=[-bps_limit, bps_limit],
            row=row_index,
            col=2,
        )
        figure.update_yaxes(
            title_text=exchange.upper(),
            rangemode="tozero",
            row=row_index,
            col=1,
        )
        figure.update_yaxes(
            showticklabels=False,
            showgrid=False,
            zeroline=False,
            range=[-1, 1],
            row=row_index,
            col=2,
        )
    figure.update_xaxes(title_text="Price (USDT)", row=len(exchanges), col=1)
    figure.update_xaxes(
        title_text=f"BPS from {reference_exchange} mid",
        row=len(exchanges),
        col=2,
    )
    figure.add_annotation(
        text="<b>Cumulative order-book depth (BTC)</b>",
        xref="paper",
        yref="paper",
        x=0.27,
        y=1.04,
        showarrow=False,
    )
    figure.add_annotation(
        text=f"<b>L1 quotes vs {reference_exchange} reference</b>",
        xref="paper",
        yref="paper",
        x=0.81,
        y=1.04,
        showarrow=False,
    )
    figure.update_layout(
        title=f"Cross-exchange {pair} order books",
        height=max(560, len(exchanges) * 210),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e8e6e3"},
        legend={
            "orientation": "h",
            "y": -0.10,
            "x": 0.5,
            "xanchor": "center",
        },
        margin={"l": 90, "r": 35, "t": 100, "b": 90},
    )
    return figure


def _price(value: float) -> str:
    return f"${value:,.2f}"


def _table_columns() -> list[str | dict[str, Any]]:
    return [
        "trade_id",
        "order_id",
        "executor_id",
        "timestamp",
        "side",
        "position_action",
        {"field": "price", "label": "Price", "format": ",.2f", "prefix": "$"},
        {"field": "amount", "label": "Amount", "format": ",.4f"},
        {
            "field": "quote_volume",
            "label": "Quote Volume",
            "format": ",.2f",
            "prefix": "$",
        },
        {"field": "fee_quote", "label": "Fee", "format": ",.4f", "prefix": "$"},
        {
            "field": "execution_cost_bps",
            "label": "Execution Cost",
            "format": ",.3f",
            "suffix": " bps",
        },
        "size_bucket",
        "liquidity",
    ]


def build_component_gallery(
    builder: ReportBuilder, datasets: dict[str, list[dict]]
) -> ReportBuilder:
    """Build the seven-section ReportBuilder component gallery."""
    builder.manual_order()
    for name, rows in datasets.items():
        builder.dataset(name, rows)

    candles = datasets["candles"]
    market_scan = datasets["market_scan"]
    fleet = datasets["bot_fleet"]
    order_book = datasets["order_book"]
    cross_exchange_books = datasets["cross_exchange_books"]
    active_orders = datasets["active_orders"]
    executors = datasets["executors"]
    trades = datasets["trades"]
    primary_bot = next(row for row in fleet if row["bot_name"] == "btc_mm_alpha")
    last = candles[-1]
    as_of = last["timestamp"].replace("T", " ")[:16] + " UTC"
    change_24h = (last["close"] / candles[-25]["close"] - 1) * 100
    best_bid = next(row for row in order_book if row["side"] == "Bid")
    best_ask = next(row for row in order_book if row["side"] == "Ask")
    book_mid = (best_bid["price"] + best_ask["price"]) / 2
    spread_bps = (best_ask["price"] - best_bid["price"]) / book_mid * 10_000
    bid_notional = sum(row["notional"] for row in order_book if row["side"] == "Bid")
    ask_notional = sum(row["notional"] for row in order_book if row["side"] == "Ask")
    imbalance = (bid_notional - ask_notional) / (bid_notional + ask_notional) * 100
    risk_summary = (
        "all configured risk limits remain healthy"
        if all(row["State"] == "Healthy" for row in datasets["risk_limits"])
        else "at least one configured risk limit is breached"
    )
    pnl_summary = "profitable" if primary_bot["net_pnl_quote"] >= 0 else "in a net loss"
    market_summary = {
        "Trending": f"BTC is trending {'higher' if change_24h >= 0 else 'lower'}",
        "Ranging": "BTC is trading in a range",
        "Volatile": "BTC is moving through a volatile period",
    }[last["regime"]]

    builder.section(
        "Market & Bot Snapshot",
        "A simulated BTC-USDT market-making session and bot fleet overview.",
    )
    builder.markdown(
        f"This component gallery uses **deterministic, simulated, and fully offline** data "
        f"timestamped at **{as_of}**. It must not be interpreted as current exchange or account data. "
        "The primary bot is `btc_mm_alpha`, running a balanced perpetual market-making "
        "controller while BTC, ETH, and SOL peers provide fleet context."
    )
    builder.kpi(
        "Latest BTC Price",
        _price(last["close"]),
        f"{change_24h:+.2f}% over 24h",
        "up" if change_24h >= 0 else "down",
    )
    builder.kpi(
        "Visible Spread",
        f"{spread_bps:.2f} bps",
        "Basis points: 1 bp = 0.01%",
    )
    builder.kpi("Market Regime", last["regime"], "Derived from hourly candles")
    builder.kpi(
        "Bot Status",
        primary_bot["bot_status"],
        f"Controller {primary_bot['controller_status'].lower()}",
    )
    builder.kpi(
        "Net PnL",
        f"${primary_bot['net_pnl_quote']:+,.2f}",
        "Realized plus unrealized",
        "up" if primary_bot["net_pnl_quote"] >= 0 else "down",
    )
    builder.kpi("Session Volume", f"${primary_bot['session_volume_quote']:,.0f}")
    builder.kpi(
        "Executors",
        f"{primary_bot['active_executors']} active",
        f"{primary_bot['closed_executors']} closed",
    )
    builder.kpi(
        "Filled Trades", str(primary_bot["trade_count"]), "Linked to executor IDs"
    )
    builder.kpi(
        "Inventory Skew",
        f"{primary_bot['inventory_skew_pct']:+.1f}%",
        "Difference from the target asset mix",
    )
    builder.markdown(
        "### Session reading\n"
        f"{market_summary} while visible liquidity remains balanced. The "
        f"primary bot is {pnl_summary}, {risk_summary}, and inventory is slightly below its "
        "base-asset target.\n\n"
        "| Dataset | Coverage | Rows | Role |\n"
        "| --- | --- | ---: | --- |\n"
        f"| BTC candles | {candles[0]['timestamp'][:10]} to {candles[-1]['timestamp'][:10]} | {len(candles)} | Historical |\n"
        f"| Market scan | Simulated 24h snapshot | {len(market_scan)} | Cross-market comparison |\n"
        f"| Order book | {as_of} | {len(order_book)} | Current snapshot |\n"
        f"| CEX books | {as_of} | {len(cross_exchange_books)} | Cross-exchange comparison |\n"
        f"| Bot fleet | Simulated session | {len(fleet)} | Fleet comparison |\n"
        f"| Executors | Simulated session | {len(executors)} | Lifecycle |\n"
        f"| Trades | Last 48 hours | {len(trades)} | Fill ledger |"
    )
    builder.table(
        [
            {
                "Bot": "btc_mm_alpha",
                "Controller": "pmm_btc_alpha",
                "Connector": "binance_perpetual",
                "Pair": "BTC-USDT",
                "Mode": "HEDGE",
                "Leverage": "3x",
                "Status": "RUNNING",
            }
        ]
    )
    builder.data_table(
        "risk_limits",
        ["Limit", "Current", "Threshold", "State"],
        title="Current risk limits",
        searchable=False,
        sortable=False,
        page_size=3,
        component_id="risk-limits-summary",
    )
    builder.section(
        "Market Screening",
        "Compare simulated perpetual markets by normalized volatility, liquidity, and activity consistency.",
    )
    builder.markdown(
        "The scanner classifies markets as **Mature**, **Degen**, or **Other**. "
        "Heatmap area represents 24h volume and color represents 24h price change. "
        "In the scatter view, larger points have steadier activity. Select markets in "
        "either chart to focus the linked metrics and table."
    )
    builder.select_filter(
        "scanner-classification",
        "market_scan",
        "classification",
        label="Market classification",
        multiple=True,
        width=12,
        help_text="Choose one or more scanner classifications.",
    )
    for label, field, aggregate, prefix, suffix in (
        ("Visible Markets", None, "count", "", ""),
        ("Combined 24h Volume", "volume_24h_usd", "sum", "$", ""),
        ("Median NATR", "natr_mean", "median", "", "%"),
        ("Average Price Range", "price_range_pct", "mean", "", "%"),
    ):
        builder.metric(
            label,
            "market_scan",
            field,
            aggregate=aggregate,
            format=",.2f",
            prefix=prefix,
            suffix=suffix,
            width=3,
            component_id=f"scanner-{aggregate}-{field or 'rows'}",
        )
    builder.chart(
        "treemap",
        "Market Heatmap: 24h Trend by Volume",
        "market_scan",
        "symbol_label",
        "volume_24h_usd",
        color="price_change_24h",
        value_label="24h Volume",
        value_prefix="$",
        value_format=",.0f",
        color_label="24h Change",
        color_format=".2f",
        color_suffix="%",
        selection_mode="filter",
        selection_label="market",
        selection_field="trading_pair",
        selection_help="Select a tile to update the scanner metrics, scatter chart, and table.",
        width=12,
        height=560,
        component_id="market-trend-heatmap",
    )
    builder.chart(
        "scatter",
        "Market Scanner: NATR vs 24h Volume",
        "market_scan",
        "natr_mean",
        "volume_24h_usd",
        color="classification",
        color_map={"Mature": "#22c55e", "Degen": "#ef4444", "Other": "#94a3b8"},
        text="symbol_label",
        text_position="top center",
        size="marker_size",
        y_scale="log",
        selection_mode="filter",
        selection_label="market",
        selection_field="trading_pair",
        selection_help="Select one or more markets to update the scanner metrics and table.",
        x_label="Mean normalized ATR (%)",
        y_label="24h volume (USDT, log scale)",
        width=12,
        height=520,
        component_id="market-scanner-scatter",
    )
    builder.data_table(
        "market_scan",
        [
            "trading_pair",
            "classification",
            {
                "field": "volume_24h_usd",
                "label": "24h Volume",
                "format": ",.0f",
                "prefix": "$",
            },
            {"field": "natr_mean", "label": "NATR", "format": ",.3f", "suffix": "%"},
            {"field": "bucket_cv", "label": "Volume CV", "format": ",.2f"},
            {"field": "natr_cv", "label": "NATR CV", "format": ",.2f"},
            {
                "field": "natr_spike_ratio",
                "label": "NATR Spike",
                "format": ",.2f",
                "suffix": "x",
            },
            {
                "field": "price_range_pct",
                "label": "Price Range",
                "format": ",.2f",
                "suffix": "%",
            },
            {
                "field": "price_change_24h",
                "label": "24h Change",
                "format": ",.2f",
                "suffix": "%",
            },
        ],
        title="Markets in scanner view",
        page_size=18,
        component_id="market-scanner-table",
    )
    builder.section(
        "Price & Volume History",
        "Date controls update linked metrics and charts; distribution and footprint plots show the full report window.",
    )
    builder.range_filter(
        "candle-date-range",
        "candles",
        "timestamp",
        label="BTC candle date range",
        value_type="date",
        width=12,
        help_text="Choose inclusive UTC dates; metrics and the three linked historical charts recalculate.",
    )
    for label, field, aggregate, suffix in (
        ("Start Price", "close", "first", ""),
        ("End Price", "close", "last", ""),
        ("Window Change", "close", "change_pct", "%"),
        ("Average Range", "range_pct", "mean", "%"),
        ("Period High", "high", "max", ""),
        ("Period Low", "low", "min", ""),
    ):
        builder.metric(
            label,
            "candles",
            field,
            aggregate=aggregate,
            format=",.2f",
            prefix="$" if "Price" in label or "High" in label or "Low" in label else "",
            suffix=suffix,
            width=2,
            component_id=f"history-{aggregate}-{field}",
        )
    builder.chart(
        "candlestick",
        "BTC-USDT with EMA 20, SMA 50, and session VWAP",
        "candles",
        "timestamp",
        open="open",
        high="high",
        low="low",
        close="close",
        overlays=[
            {
                "y": "ema_20",
                "label": "EMA 20",
                "line": {"color": "#d4a845", "width": 1.5},
            },
            {
                "y": "sma_50",
                "label": "SMA 50",
                "line": {"color": "#56b4e9", "width": 1.4},
            },
            {"y": "vwap", "label": "VWAP", "line": {"color": "#a78bfa", "width": 1.2}},
        ],
        cross_filter=False,
        x_label="Candle time (UTC)",
        y_label="BTC price (USDT)",
        height=500,
        component_id="btc-candles",
    )
    builder.chart(
        "line",
        "Rolling 24-hour return volatility",
        "candles",
        "timestamp",
        "rolling_volatility_pct",
        cross_filter=False,
        x_label="Candle time (UTC)",
        y_label="Volatility (%)",
        width=6,
        height=330,
        component_id="btc-volatility",
    )
    builder.chart(
        "area",
        "Hourly quote volume",
        "candles",
        "timestamp",
        "quote_volume",
        cross_filter=False,
        x_label="Candle time (UTC)",
        y_label="Quote volume (USDT)",
        width=6,
        height=330,
        component_id="btc-volume",
    )
    return_stats = _return_distribution_stats(candles)
    builder.plotly(_build_return_distribution_figure(candles))
    builder.markdown(
        f"The hourly return distribution excludes the first candle, which has no prior close. "
        f"Across **{return_stats['count']} returns**, p5 is **{return_stats['p5']:+.4f}%**, "
        f"p95 is **{return_stats['p95']:+.4f}%**, and standard deviation is "
        f"**{return_stats['std']:.4f}%**."
    )
    footprint = build_estimated_footprint_figure(
        candles,
        volume_field="base_volume",
        buckets=24,
        title=f"Estimated BTC volume footprint ({len(candles)} hourly candles)",
        candle_title="Hourly BTC-USDT candles",
        profile_title="Estimated volume by price",
        candle_name="BTC-USDT",
    )
    if footprint is not None:
        builder.plotly(footprint)
    builder.markdown(
        "The footprint uses the full report window. Buy and sell volume are estimates "
        "based on where each candle closed within its range, not actual trade-side records."
    )
    builder.section(
        "Bot Fleet Comparison",
        "Compare trading volume, profit, and risk across nine simulated bots.",
    )
    builder.select_filter(
        "fleet-pairs",
        "bot_fleet",
        "pair",
        label="Trading pairs",
        multiple=True,
        width=4,
        help_text="Choose the markets to compare.",
    )
    builder.select_filter(
        "fleet-profile",
        "bot_fleet",
        "profile",
        label="Market-making profile",
        multiple=False,
        width=4,
        help_text="Choose one profile or show all profiles.",
    )
    for label, field, aggregate, prefix, suffix in (
        ("Visible Bots", None, "count", "", ""),
        ("Fleet Net PnL", "net_pnl_quote", "sum", "$", ""),
        ("Session Volume", "session_volume_quote", "sum", "$", ""),
        ("Median Fill Rate", "fill_rate_pct", "median", "", "%"),
    ):
        builder.metric(
            label,
            "bot_fleet",
            field,
            aggregate=aggregate,
            format=",.2f",
            prefix=prefix,
            suffix=suffix,
            width=3,
            component_id=f"fleet-{aggregate}-{field or 'rows'}",
        )
    builder.chart(
        "bar",
        "Net PnL by bot",
        "bot_fleet",
        "display_name",
        "net_pnl_quote",
        color="pnl_sign",
        color_map={"Profit": "#22c55e", "Loss": "#ef4444"},
        cross_filter=False,
        text="pnl_label",
        x_label="Bot",
        y_label="Net PnL (USDT)",
        width=6,
        height=370,
        component_id="fleet-pnl",
    )
    builder.chart(
        "horizontal_bar",
        "Session volume by bot",
        "bot_fleet",
        "display_name",
        "session_volume_quote",
        color="pair",
        color_map={
            "BTC-USDT": "#d4a845",
            "ETH-USDT": "#56b4e9",
            "SOL-USDT": "#a78bfa",
        },
        cross_filter=False,
        x_label="Session volume (USDT)",
        y_label="Bot",
        width=6,
        height=370,
        component_id="fleet-volume",
    )
    builder.chart(
        "horizontal_bar",
        "Controller drawdown limit used",
        "bot_fleet",
        "display_name",
        "drawdown_utilization_pct",
        color="risk_state",
        color_map={
            "Healthy": "#22c55e",
            "Warning": "#d4a845",
            "At limit": "#ef4444",
        },
        cross_filter=False,
        text="risk_label",
        reference_lines=[
            {
                "axis": "x",
                "value": 80,
                "color": "#ef4444",
                "width": 2,
                "label": "At-limit threshold (80%)",
            }
        ],
        x_range=[0, 90],
        x_label="Drawdown limit used (%)",
        y_label="Bot",
        width=12,
        height=430,
        component_id="fleet-risk-utilization",
    )
    builder.data_table(
        "bot_fleet",
        [
            "bot_name",
            "controller_config",
            "pair",
            "profile",
            "bot_status",
            "controller_status",
            "active_executors",
            "trade_count",
            {
                "field": "session_volume_quote",
                "label": "Volume",
                "format": ",.2f",
                "prefix": "$",
            },
            {
                "field": "net_pnl_quote",
                "label": "Net PnL",
                "format": ",.2f",
                "prefix": "$",
            },
            {
                "field": "inventory_skew_pct",
                "label": "Inventory Skew",
                "format": ",.1f",
                "suffix": "%",
            },
            {
                "field": "drawdown_utilization_pct",
                "label": "Drawdown Limit Used",
                "format": ",.1f",
                "suffix": "%",
            },
        ],
        title="Filtered Hummingbot fleet",
        page_size=9,
        component_id="fleet-table",
    )
    builder.section(
        "Liquidity & Order Book",
        "Compare current BTC market depth with orders from active executors.",
    )
    builder.kpi("Mid Price", _price((best_bid["price"] + best_ask["price"]) / 2))
    builder.kpi("Top Spread", f"{spread_bps:.2f} bps", "1 bp = 0.01%")
    builder.kpi("Book Imbalance", f"{imbalance:+.1f}%", "Positive is bid-heavy")
    builder.kpi(
        "Visible Depth", f"${bid_notional + ask_notional:,.0f}", "20 levels per side"
    )
    builder.plotly(_build_cross_exchange_figure(cross_exchange_books))
    builder.markdown(
        "The cross-exchange chart is a simulated three-venue snapshot. The left column compares "
        "cumulative BTC depth, while the right column places each best bid, ask, and mid in "
        "basis points relative to the Binance mid. It does not respond to the single-venue "
        "filters below."
    )
    builder.select_filter(
        "book-side",
        "order_book",
        "side",
        label="Order-book side",
        multiple=False,
        width=4,
        help_text="Inspect bids, asks, or the complete visible snapshot.",
    )
    builder.range_filter(
        "book-distance",
        "order_book",
        "distance_bps",
        label="Distance from mid",
        value_type="number",
        width=8,
        help_text="Choose distance from the mid price. One basis point (bp) is 0.01%.",
    )
    for label, field, aggregate, prefix, suffix in (
        ("Visible Levels", None, "count", "", ""),
        ("Visible Notional", "notional", "sum", "$", ""),
        ("Average Level Size", "amount", "mean", "", " BTC"),
        ("Maximum Distance", "distance_bps", "max", "", " bps"),
    ):
        builder.metric(
            label,
            "order_book",
            field,
            aggregate=aggregate,
            format=",.2f",
            prefix=prefix,
            suffix=suffix,
            width=3,
            component_id=f"book-{aggregate}-{field or 'rows'}",
        )

    import plotly.graph_objects as go

    depth = go.Figure()
    for side, color in (("Bid", "#22c55e"), ("Ask", "#ef4444")):
        levels = [row for row in order_book if row["side"] == side]
        depth.add_trace(
            go.Scatter(
                x=[row["price"] for row in levels],
                y=[row["cumulative_notional"] for row in levels],
                mode="lines+markers",
                line={"color": color, "shape": "hv", "width": 2},
                marker={"size": 5},
                name=f"{side} depth",
                hovertemplate="Price: %{x:,.2f}<br>Cumulative: $%{y:,.0f}<extra></extra>",
            )
        )
    for side, color in (("BUY", "#22c55e"), ("SELL", "#ef4444")):
        orders = [row for row in active_orders if row["side"] == side]
        depth.add_trace(
            go.Bar(
                x=[row["price"] for row in orders],
                y=[row["amount"] for row in orders],
                yaxis="y2",
                width=book_mid * 0.00012,
                marker={
                    "color": color,
                    "opacity": 0.45,
                    "line": {"color": color, "width": 1.5},
                },
                name=f"Bot {side.lower()} amount",
                customdata=[
                    [row["executor_id"], row["notional"], row["distance_bps"]]
                    for row in orders
                ],
                hovertemplate=(
                    "Executor: %{customdata[0]}<br>Price: %{x:,.2f} USDT"
                    "<br>Amount: %{y:.4f} BTC<br>Notional: %{customdata[1]:,.0f} USDT"
                    "<br>Distance: %{customdata[2]:.2f} bps<extra></extra>"
                ),
            )
        )
    depth.update_layout(
        title="Market depth and bot order amounts",
        height=460,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e8e6e3"},
        xaxis={
            "title": "BTC price (USDT)",
            "zeroline": False,
            "showline": False,
        },
        yaxis={
            "title": "Cumulative visible notional (USDT)",
            "rangemode": "tozero",
            "zeroline": False,
            "showline": False,
        },
        yaxis2={
            "title": "Bot order amount (BTC)",
            "overlaying": "y",
            "side": "right",
            "rangemode": "tozero",
            "showgrid": False,
            "zeroline": False,
            "showline": False,
        },
        shapes=[
            {
                "type": "line",
                "x0": book_mid,
                "x1": book_mid,
                "y0": 0,
                "y1": 1,
                "xref": "x",
                "yref": "paper",
                "line": {"color": "#94a3b8", "width": 1, "dash": "dot"},
            }
        ],
        legend={"orientation": "h", "y": -0.18, "x": 0.5, "xanchor": "center"},
        margin={"l": 75, "r": 80, "t": 65, "b": 90},
    )
    builder.plotly(depth)
    builder.chart(
        "bar",
        "Order-book liquidity by distance from mid",
        "order_book",
        "distance_band",
        "notional",
        color="side",
        color_map={"Bid": "#22c55e", "Ask": "#ef4444"},
        aggregate="sum",
        cross_filter=False,
        x_label="Distance from mid",
        y_label="Displayed notional (USDT)",
        width=12,
        height=350,
        component_id="book-distance-bars",
    )
    builder.markdown(
        "Bars total the displayed book levels in each distance band. Use the side and "
        "distance controls above to narrow the view."
    )
    builder.markdown(
        "### Active maker orders\nThese orders are linked to the four active executors in the primary bot."
    )
    builder.table(
        active_orders,
        [
            "order_id",
            "executor_id",
            "side",
            "price",
            "amount",
            "distance_bps",
            "age_seconds",
            "status",
        ],
    )
    builder.section(
        "Bot, Executor & Trade Performance",
        "Review btc_mm_alpha profit and loss by executor and individual trade.",
    )
    pnl = datasets["bot_pnl_timeline"][-1]
    builder.kpi(
        "Realized PnL",
        f"${pnl['realized_pnl_quote']:+,.2f}",
        "Net of terminated-executor fees",
        "up" if pnl["realized_pnl_quote"] >= 0 else "down",
    )
    builder.kpi(
        "Unrealized PnL",
        f"${pnl['unrealized_pnl_quote']:+,.2f}",
        "Net of running-executor fees",
        "up" if pnl["unrealized_pnl_quote"] >= 0 else "down",
    )
    builder.kpi("Fees", f"${pnl['fees_quote']:,.2f}")
    builder.kpi("Current Drawdown", f"${pnl['drawdown_quote']:,.2f}")
    builder.kpi(
        "Inventory Skew",
        f"{pnl['inventory_skew_pct']:+.1f}%",
        "Difference from the target asset mix",
    )
    builder.kpi("Trade Volume", f"${sum(row['quote_volume'] for row in trades):,.0f}")
    builder.chart(
        "line",
        "Net PnL timeline",
        "bot_pnl_timeline",
        "timestamp",
        "net_pnl_quote",
        cross_filter=False,
        x_label="Session time (UTC)",
        y_label="Net PnL (USDT)",
        width=12,
        height=350,
        component_id="bot-pnl-line",
    )
    builder.markdown(
        "### Executor outcomes\nSelect an executor bar to update the PnL card and table."
    )
    builder.chart(
        "horizontal_bar",
        "Net PnL by executor",
        "executors",
        "executor_id",
        "net_pnl_quote",
        color="pnl_sign",
        color_map={"Profit": "#22c55e", "Loss": "#ef4444"},
        text="pnl_label",
        selection_mode="filter",
        selection_label="executor",
        selection_field="executor_id",
        selection_help="Select a bar to focus on that executor.",
        x_label="Net PnL (USDT)",
        y_label="Executor",
        width=8,
        height=460,
        component_id="executor-pnl-bars",
    )
    builder.metric(
        "Executor PnL in View",
        "executors",
        "net_pnl_quote",
        aggregate="sum",
        format=",.2f",
        prefix="$",
        width=4,
        component_id="selected-executor-pnl",
    )
    builder.data_table(
        "executors",
        [
            "executor_id",
            "status",
            "side",
            "close_type",
            {"field": "entry_price", "label": "Entry", "format": ",.2f", "prefix": "$"},
            {"field": "close_price", "label": "Close", "format": ",.2f", "prefix": "$"},
            {
                "field": "net_pnl_quote",
                "label": "Net PnL",
                "format": ",.2f",
                "prefix": "$",
            },
            {"field": "fees_quote", "label": "Fees", "format": ",.2f", "prefix": "$"},
            "trade_count",
            "order_count",
        ],
        title="Executors in view",
        page_size=8,
        component_id="executor-selection-table",
    )
    builder.markdown(
        "### Trade review\nUse the time and side controls to narrow the fills. Select a bar to view the trades in that size and side."
    )
    builder.range_filter(
        "trade-time-range",
        "trades",
        "timestamp",
        label="Trade time range (UTC)",
        value_type="datetime",
        width=8,
        help_text="Choose exact hour-and-minute endpoints for the fill investigation.",
    )
    builder.select_filter(
        "trade-side",
        "trades",
        "side",
        label="Trade side",
        multiple=False,
        width=4,
        help_text="Show buys, sells, or both.",
    )
    for label, field, aggregate, prefix, suffix in (
        ("Matching Trades", None, "count", "", ""),
        ("Quote Volume", "quote_volume", "sum", "$", ""),
        ("Trading Fees", "fee_quote", "sum", "$", ""),
        ("Mean Execution Cost", "execution_cost_bps", "mean", "", " bps"),
    ):
        builder.metric(
            label,
            "trades",
            field,
            aggregate=aggregate,
            format=",.3f",
            prefix=prefix,
            suffix=suffix,
            width=3,
            component_id=f"trade-{aggregate}-{field or 'rows'}",
        )
    builder.chart(
        "histogram",
        "Execution cost distribution",
        "trades",
        "execution_cost_bps",
        bins=12,
        cross_filter=False,
        x_label="Execution cost (bps)",
        y_label="Trade count",
        width=5,
        height=360,
        component_id="trade-cost-histogram",
    )
    builder.chart(
        "box",
        "Execution cost spread by liquidity and side",
        "trades",
        "liquidity",
        "execution_cost_bps",
        color="side",
        color_map={"BUY": "#22c55e", "SELL": "#ef4444"},
        selection_mode="drilldown",
        selection_label="trade",
        selection_field="trade_id",
        selection_help="Select a trade point to inspect its complete fill record.",
        x_label="Liquidity role",
        y_label="Execution cost vs mid (bps)",
        width=7,
        height=360,
        component_id="trade-cost-box",
    )
    builder.chart(
        "bar",
        "Mean execution cost by trade size and side",
        "trades",
        "size_bucket",
        "execution_cost_bps",
        color="side",
        color_map={"BUY": "#22c55e", "SELL": "#ef4444"},
        aggregate="mean",
        selection_mode="drilldown",
        selection_label="trade",
        selection_field="trade_id",
        selection_help="Select a bar to view all trades contributing to that mean.",
        category_order=["Under $2k", "$2k-$5k", "$5k-$10k", "$10k+"],
        reference_lines=[{"axis": "y", "value": 0, "color": "#94a3b8"}],
        hover_fields=[
            "trade_id",
            "order_id",
            "executor_id",
            "quote_volume",
            "side",
            "liquidity",
            "execution_cost_bps",
        ],
        x_label="Trade size",
        y_label="Execution cost vs mid (bps)",
        width=12,
        height=360,
        component_id="trade-cost-bars",
    )
    builder.drilldown(
        "trades",
        _table_columns(),
        title="Selected trade fills",
        page_size=10,
        component_id="trade-drilldown",
    )
    builder.section(
        "Market & Execution Data",
        "Search, sort, and export the records used in this report.",
    )
    builder.markdown(
        "These tables contain the records behind the analysis. Search narrows visible rows, "
        "column headers sort, and CSV export downloads the filtered data. All timestamps "
        "are UTC, and every component uses the same simulated datasets."
    )
    candle_columns: list[str | dict[str, Any]] = [
        "timestamp",
        "regime",
        *[
            {"field": field, "label": field.title(), "format": ",.2f", "prefix": "$"}
            for field in ("open", "high", "low", "close", "ema_20", "sma_50", "vwap")
        ],
        {"field": "base_volume", "label": "Base Volume", "format": ",.4f"},
        {
            "field": "quote_volume",
            "label": "Quote Volume",
            "format": ",.2f",
            "prefix": "$",
        },
        {"field": "return_pct", "label": "Return", "format": ",.4f", "suffix": "%"},
    ]
    builder.data_table(
        "raw_candles",
        candle_columns,
        title="BTC-USDT hourly candles",
        page_size=15,
        component_id="raw-candles",
    )
    builder.data_table(
        "raw_executors",
        [
            "executor_id",
            "status",
            "side",
            "created_at",
            "closed_at",
            "close_type",
            {"field": "entry_price", "label": "Entry", "format": ",.2f", "prefix": "$"},
            {"field": "close_price", "label": "Close", "format": ",.2f", "prefix": "$"},
            {"field": "amount", "label": "Amount", "format": ",.4f"},
            {
                "field": "net_pnl_quote",
                "label": "Net PnL",
                "format": ",.2f",
                "prefix": "$",
            },
            {"field": "fees_quote", "label": "Fees", "format": ",.2f", "prefix": "$"},
            "duration_seconds",
            "trade_count",
            "order_count",
        ],
        title="Complete executor lifecycle",
        page_size=8,
        component_id="raw-executors",
    )
    builder.data_table(
        "raw_trades",
        _table_columns(),
        title="Complete trade ledger",
        page_size=12,
        component_id="raw-trades",
    )
    builder.markdown(
        "The saved HTML includes its data, filters, charts, and tables. It also works when downloaded."
    )
    return builder


async def run(config: Config, context: Any) -> str:
    """Build and save the Hummingbot ReportBuilder component gallery."""
    builder = ReportBuilder("Report Component Gallery")
    builder.source("routine", "report_component_gallery").tags(
        ["components", "developer-tools", "gallery", "hummingbot", "simulated"]
    )
    build_component_gallery(builder, make_gallery_data(config.history_hours))
    report_id = await builder.save()
    return f"Report Component Gallery saved ({report_id})."
