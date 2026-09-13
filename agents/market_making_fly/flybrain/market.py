"""What the fly loop reads from the world, and the one thing it writes.

``LiveMarket`` talks to Hummingbot (candles, bot performance, portfolio, config
updates, stop) and to Hyperliquid's public ``l2Book`` for the live book — the
hummingbot-api order-book endpoint 500s on HIP-3 pairs. ``FixtureMarket`` is a
deterministic offline stand-in for plumbing tests; it never applies anything.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import aiohttp
from flybrain.naming import pair_names
from flybrain.posture import MarketSpec
from flybrain.reinforcement import controller_net

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
QUOTE_TOKENS = ("USD", "USDC")


@dataclass(frozen=True)
class Observation:
    pair: str
    candles: list[dict]
    bid: float
    ask: float
    open: bool  # both sides of the live book present

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Book:
    bid: float | None
    ask: float | None

    @property
    def open(self) -> bool:
        return self.bid is not None and self.ask is not None


async def fetch_l2_book(session: aiohttp.ClientSession, coin: str) -> Book:
    async with session.post(
        HL_INFO_URL,
        json={"type": "l2Book", "coin": coin},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"l2Book {coin}: HTTP {resp.status}")
        book = await resp.json()
    levels = book.get("levels") if isinstance(book, dict) else None
    if not levels or len(levels) != 2:
        raise RuntimeError(f"l2Book {coin}: unexpected payload {str(book)[:120]}")
    bids, asks = levels
    if not bids or not asks:
        return Book(None, None)  # closed / empty book, a real state not an error
    return Book(float(bids[0]["px"]), float(asks[0]["px"]))


def normalize_candle_payload(result) -> list[dict]:
    records = (
        result
        if isinstance(result, list)
        else result.get("data", result.get("candles"))
    )
    if not records:
        raise RuntimeError("Candle feed returned no records")
    return list(records)


class LiveMarket:
    def __init__(
        self, client, connector_name: str, candle_interval: str, n_candles: int
    ):
        self.client = client
        self.connector_name = connector_name
        self.candle_interval = candle_interval
        self.n_candles = n_candles

    async def observe(self, pair: str) -> Observation:
        names = pair_names(pair)
        candles = normalize_candle_payload(
            await self.client.market_data.get_candles(
                self.connector_name,
                pair,
                interval=self.candle_interval,
                max_records=self.n_candles,
            )
        )
        async with aiohttp.ClientSession() as session:
            book = await fetch_l2_book(session, names.coin)
        if not book.open:
            # The chart still exists; the loop decides what a closed book means.
            last = float(candles[-1]["close"])
            return Observation(pair, candles, last, last, False)
        return Observation(pair, candles, book.bid, book.ask, True)

    async def fresh_mid(self, pair: str) -> float:
        async with aiohttp.ClientSession() as session:
            book = await fetch_l2_book(session, pair_names(pair).coin)
        if not book.open:
            raise RuntimeError(f"{pair}: book closed at apply time")
        return (book.bid + book.ask) / 2

    async def bots(self) -> dict:
        resp = await self.client.bot_orchestration.get_active_bots_status()
        raw = resp if isinstance(resp, dict) else {}
        data = raw.get("data", raw)
        return data if isinstance(data, dict) else {}

    async def equity(self, pairs: list[str]) -> tuple[float, float, dict]:
        """Combined ``realized + unrealized`` and volume across the fly's bots.

        A bot that is not running contributes nothing — there is no P&L to
        report. Returns ``(net, volume, per_pair)``."""
        bots = await self.bots()
        net = 0.0
        volume = 0.0
        per_pair: dict[str, dict] = {}
        for pair in pairs:
            names = pair_names(pair)
            bot = bots.get(names.bot_name)
            if not isinstance(bot, dict):
                per_pair[pair] = {"running": False}
                continue
            perf = (bot.get("performance") or {}).get(names.config_name)
            if not isinstance(perf, dict):
                raise RuntimeError(
                    f"bot {names.bot_name} is running without controller {names.config_name}"
                )
            inner = perf.get("performance", perf)
            pair_net = controller_net(inner)
            pair_volume = float(inner.get("volume_traded", 0) or 0)
            net += pair_net
            volume += pair_volume
            per_pair[pair] = {"running": True, "net": pair_net, "volume": pair_volume}
        if not math.isfinite(net) or not math.isfinite(volume):
            raise RuntimeError("Nonfinite bot performance")
        return net, volume, per_pair

    async def available_usd(self) -> float:
        state = await self.client.portfolio.get_portfolio_state()
        if not isinstance(state, dict):
            raise RuntimeError("Portfolio state unavailable")
        total = 0.0
        seen = False
        for account in state.values():
            if not isinstance(account, dict):
                continue
            for connector, tokens in account.items():
                if self.connector_name not in connector or not isinstance(tokens, list):
                    continue
                seen = True
                for token in tokens:
                    if isinstance(token, dict) and token.get("token") in QUOTE_TOKENS:
                        units = float(token.get("units", 0) or 0)
                        total += float(token.get("available_units", units) or 0)
        if not seen:
            raise RuntimeError(f"No {self.connector_name} balances in portfolio state")
        return total

    async def apply(self, pair: str, config: dict) -> None:
        """Update the live bot's controller and the saved config, both layers."""
        names = pair_names(pair)
        bots = await self.bots()
        if names.bot_name not in bots:
            raise RuntimeError(f"bot {names.bot_name} is not running")
        await self.client.controllers.update_bot_controller_config(
            names.bot_name, names.config_name, config
        )
        await self.client.controllers.create_or_update_controller_config(
            names.config_name, config
        )

    async def stop_bot(self, pair: str) -> bool:
        names = pair_names(pair)
        if names.bot_name not in await self.bots():
            return False
        await self.client.bot_orchestration.stop_and_archive_bot(names.bot_name)
        return True


class FixtureMarket:
    """Deterministic sine-wave candles; equity swings so both pulses fire."""

    BASE = {0: 60.0, 1: 120.0, 2: 30.0}

    def __init__(self, pairs: list[str], n_candles: int):
        self.pairs = pairs
        self.n_candles = n_candles
        self.tick = 0

    def _price(self, index: int, k: int) -> float:
        base = self.BASE[index % 3]
        return base * (1 + 0.02 * math.sin(k * 0.35 + index))

    async def observe(self, pair: str) -> Observation:
        index = self.pairs.index(pair)
        candles = []
        for k in range(self.tick, self.tick + self.n_candles):
            o, c = self._price(index, k), self._price(index, k + 1)
            candles.append(
                {
                    "timestamp": k * 300,
                    "open": o,
                    "high": max(o, c) * 1.001,
                    "low": min(o, c) * 0.999,
                    "close": c,
                    "volume": 100 + 50 * math.sin(k * 0.7),
                }
            )
        self.tick += 1
        mid = candles[-1]["close"]
        return Observation(pair, candles, mid * 0.9995, mid * 1.0005, True)

    async def fresh_mid(self, pair: str) -> float:
        index = self.pairs.index(pair)
        return self._price(index, self.tick + self.n_candles)

    async def equity(self, pairs: list[str]) -> tuple[float, float, dict]:
        # Swings so both pulses fire, drifts up so new highs recur, and stays
        # within ±2 bp of volume so the loss-rate breaker is not exercised here.
        net = 2.0 * math.sin(self.tick * 1.3) + 0.05 * self.tick
        volume = 10_000.0 * (self.tick + 1)
        return net, volume, {p: {"running": False, "fixture": True} for p in pairs}

    async def available_usd(self) -> float:
        return 1e9

    async def apply(self, pair: str, config: dict) -> None:
        raise RuntimeError("FixtureMarket never applies a config")

    async def stop_bot(self, pair: str) -> bool:
        return False


def required_collateral(specs: list[MarketSpec]) -> float:
    """Margin the three books could need at their inventory cap."""
    return sum(s.total_amount_quote * s.max_base_pct / s.leverage for s in specs)


def now() -> float:
    return time.time()
