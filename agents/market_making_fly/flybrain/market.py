"""What the fly loop reads from the world, and the one thing it writes.

``LiveMarket`` talks to Hummingbot (candles, bot performance, portfolio, config
updates, stop) and reads the top of book from hummingbot-api, which serves any
CLOB connector it supports. HIP-3 pairs are the one exception: that endpoint
500s on them, so those fall back to Hyperliquid's public ``l2Book``.
``FixtureMarket`` is a deterministic offline stand-in for plumbing tests; it
never applies anything.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

import aiohttp
from flybrain.naming import pair_names
from flybrain.posture import MarketSpec
from flybrain.reinforcement import controller_net

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
# The deploy tool appends -YYYYMMDD-HHMMSS, and a redeploy that hands the
# running instance name back in stacks another one.
_SUFFIXED = re.compile(r"(?:-\d{8}-\d{6})+")


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
    bid, ask = float(bids[0]["px"]), float(asks[0]["px"])
    if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask < bid:
        raise RuntimeError(
            f"l2Book {coin}: invalid top of book bid={bid!r} ask={ask!r}"
        )
    return Book(bid, ask)


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

    async def book(self, pair: str) -> Book:
        """Top of book, from whichever source serves this market.

        hummingbot-api covers every CLOB connector it supports. HIP-3 pairs are
        the exception: its order-book endpoint 500s on them, so those go
        straight to Hyperliquid's public one.
        """
        names = pair_names(pair)
        if names.hl_coin and "hyperliquid" in self.connector_name.lower():
            async with aiohttp.ClientSession() as session:
                return await fetch_l2_book(session, names.hl_coin)
        raw = await self.client.market_data.get_order_book(
            self.connector_name, pair, depth=1
        )
        if not isinstance(raw, dict):
            raise RuntimeError(f"{pair}: unexpected order book payload")
        bids, asks = raw.get("bids") or [], raw.get("asks") or []
        if not bids or not asks:
            return Book(None, None)  # closed / empty book, a real state
        bid, ask = float(bids[0][0]), float(asks[0][0])
        if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask < bid:
            raise RuntimeError(f"{pair}: invalid top of book bid={bid!r} ask={ask!r}")
        return Book(bid, ask)

    async def observe(self, pair: str) -> Observation:
        candles = normalize_candle_payload(
            await self.client.market_data.get_candles(
                self.connector_name,
                pair,
                interval=self.candle_interval,
                max_records=self.n_candles,
            )
        )
        book = await self.book(pair)
        if not book.open:
            # The chart still exists; the loop decides what a closed book means.
            last = float(candles[-1]["close"])
            return Observation(pair, candles, last, last, False)
        return Observation(pair, candles, book.bid, book.ask, True)

    async def fresh_mid(self, pair: str) -> float:
        book = await self.book(pair)
        if not book.open:
            raise RuntimeError(f"{pair}: book closed at apply time")
        return (book.bid + book.ask) / 2

    async def bots(self) -> dict:
        resp = await self.client.bot_orchestration.get_active_bots_status()
        raw = resp if isinstance(resp, dict) else {}
        data = raw.get("data", raw)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def find_bot(bots: dict, bot_name: str) -> tuple[str | None, dict | None]:
        """The deploy tool suffixes instance names with ``-YYYYMMDD-HHMMSS``,
        so ``orcl-fly`` runs as ``orcl-fly-20260913-055821``, and a redeploy
        that hands the instance name back stacks another suffix. Match the
        exact name or any number of those suffixes, and refuse an ambiguous
        match: stopping the wrong bot is worse than stopping none."""
        matches = [
            name
            for name in bots
            if name == bot_name
            or (
                name.startswith(bot_name) and _SUFFIXED.fullmatch(name[len(bot_name) :])
            )
        ]
        if len(matches) > 1:
            raise RuntimeError(f"several bots match {bot_name!r}: {sorted(matches)}")
        if not matches:
            return None, None
        bot = bots[matches[0]]
        return matches[0], (bot if isinstance(bot, dict) else None)

    async def equity(
        self, pairs: list[str], carry: dict[str, dict] | None = None
    ) -> tuple[float, float, dict, dict]:
        """Combined ``realized + unrealized`` and volume across the fly's bots.

        ``carry`` holds the last figures each bot reported. A bot that has never
        reported contributes nothing (a shadow run with no bot). A bot that
        reported before and is now missing from the active list — stopped,
        archived, or dropped from one status response — keeps contributing its
        last known figures, frozen, so its result does not vanish from the
        combined book and produce a fake equity jump. Returns
        ``(net, volume, per_pair, carry)``; persist ``carry`` between ticks."""
        bots = await self.bots()
        carry = {k: dict(v) for k, v in (carry or {}).items()}
        net = 0.0
        volume = 0.0
        per_pair: dict[str, dict] = {}
        for pair in pairs:
            names = pair_names(pair)
            _, bot = self.find_bot(bots, names.bot_name)
            if bot is None:
                previous = carry.get(pair)
                if previous:
                    net += previous["net"]
                    volume += previous["volume"]
                    per_pair[pair] = {"running": False, "carried": True, **previous}
                else:
                    per_pair[pair] = {"running": False}
                continue
            perf = (bot.get("performance") or {}).get(names.config_name)
            if not isinstance(perf, dict):
                # The bot is up but the API has no performance report for its
                # controller (no MQTT report yet, or reporting broken). Keep the
                # last known figures; the loop decides what that permits.
                previous = carry.get(pair)
                if previous:
                    net += previous["net"]
                    volume += previous["volume"]
                per_pair[pair] = {
                    "running": True,
                    "reported": False,
                    **(previous or {}),
                }
                continue
            inner = perf.get("performance", perf)
            pair_net = controller_net(inner)
            pair_volume = float(inner.get("volume_traded", 0) or 0)
            net += pair_net
            volume += pair_volume
            previous = carry.get(pair)
            per_pair[pair] = {
                "running": True,
                "reported": True,
                "net": pair_net,
                "volume": pair_volume,
            }
            if previous and pair_volume < previous["volume"]:
                # Volume only accumulates within a controller instance, so this
                # is a fresh one: a redeploy, not a collapse in P&L.
                per_pair[pair]["restarted"] = True
            carry[pair] = {"net": pair_net, "volume": pair_volume}
        if not math.isfinite(net) or not math.isfinite(volume):
            raise RuntimeError("Nonfinite bot performance")
        return net, volume, per_pair, carry

    async def available_quote(self, quote_tokens: set[str]) -> float:
        """Available balance in the quote assets these books trade against.

        A perp draws margin from its collateral asset; a spot book spends the
        quote outright. Either way the figure that matters is what is free in
        the asset the pair is denominated in, so the caller names it rather
        than this assuming a venue's collateral token.
        """
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
                    if isinstance(token, dict) and token.get("token") in quote_tokens:
                        units = float(token.get("units", 0) or 0)
                        total += float(token.get("available_units", units) or 0)
        if not seen:
            raise RuntimeError(f"No {self.connector_name} balances in portfolio state")
        return total

    async def apply(self, pair: str, config: dict) -> None:
        """Update the saved config, then the live bot's controller.

        Durable layer first: if saving fails nothing has changed on the bot and
        the tick is a clean error. If the live update then fails, the saved
        config is ahead of the bot, the tick is an error, ``applied`` keeps the
        old posture, and the next material posture retries both — the bot is
        never left running a config the run state does not know about."""
        names = pair_names(pair)
        running, _ = self.find_bot(await self.bots(), names.bot_name)
        if running is None:
            raise RuntimeError(f"bot {names.bot_name} is not running")
        # The live update endpoint requires ``id`` equal to the config name.
        payload = {"id": names.config_name, **config}
        await self.client.controllers.create_or_update_controller_config(
            names.config_name, payload
        )
        await self.client.controllers.update_bot_controller_config(
            running, names.config_name, payload
        )

    async def stop_bot(self, pair: str) -> bool:
        names = pair_names(pair)
        running, _ = self.find_bot(await self.bots(), names.bot_name)
        if running is None:
            return False
        await self.client.bot_orchestration.stop_and_archive_bot(running)
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

    async def equity(
        self, pairs: list[str], carry: dict[str, dict] | None = None
    ) -> tuple[float, float, dict, dict]:
        """A fixture stands in for a full book that reports.

        Every pair is marked running and reported on purpose: an offline run
        exists to exercise the reward/aversive path end to end, and a book that
        never reports would pin the stimulus to ``none`` (see ``pnl_is_known``).
        Nothing is applied from here either way — ``apply`` refuses.

        The aggregate swings so both pulses fire, drifts up so new highs recur,
        and stays within ±2 bp of volume so the loss-rate breaker is not
        exercised here; each pair carries an equal share of it.
        """
        net = 2.0 * math.sin(self.tick * 1.3) + 0.05 * self.tick
        volume = 10_000.0 * (self.tick + 1)
        share_net, share_volume = net / len(pairs), volume / len(pairs)
        per_pair = {
            p: {
                "running": True,
                "reported": True,
                "fixture": True,
                "net": share_net,
                "volume": share_volume,
            }
            for p in pairs
        }
        carry = {p: {"net": share_net, "volume": share_volume} for p in pairs}
        return net, volume, per_pair, carry

    async def available_quote(self, quote_tokens: set[str]) -> float:
        return 1e9

    async def apply(self, pair: str, config: dict) -> None:
        raise RuntimeError("FixtureMarket never applies a config")

    async def stop_bot(self, pair: str) -> bool:
        return False


def book_restarted(per_pair: dict) -> list[str]:
    """Books whose reported volume went backwards since the last tick.

    Volume only accumulates within one controller instance, so a drop means a
    fresh instance reporting from zero — a redeploy. Its P&L is not a loss of
    the difference, and the previous deployment's high-water mark is not a
    height it has fallen from.
    """
    return sorted(p for p, info in per_pair.items() if info.get("restarted"))


def pnl_is_known(per_pair: dict) -> bool:
    """True when every running book reported its P&L this tick.

    The reinforcement pulse, the equity anchor and the financial breakers all
    hang off this: an unreported book is silence, not a result. No running book
    at all is also silence — a shadow run with no bot deployed has nothing to
    learn from. A market that stands in for a full book (the fixture) must
    therefore report, or it exercises none of that path.
    """
    running = [info for info in per_pair.values() if info.get("running")]
    return bool(running) and all(info.get("reported") for info in running)


def required_collateral(specs: list[MarketSpec]) -> float:
    """Quote the books could need at their inventory cap.

    On a perp that is margin, so leverage divides it. On spot leverage is 1 by
    construction, and the same expression is the quote actually spent.
    """
    return sum(s.total_amount_quote * s.max_base_pct / s.leverage for s in specs)


def quote_tokens(specs: list[MarketSpec]) -> set[str]:
    """The quote assets these books are denominated in."""
    return {pair_names(s.trading_pair).quote for s in specs}


def now() -> float:
    return time.time()
