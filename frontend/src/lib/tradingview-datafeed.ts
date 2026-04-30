/**
 * TradingView Charting Library Datafeed Adapter
 *
 * Connects the TradingView widget to the Condor backend API for candle data.
 * Implements the IBasicDataFeed interface required by the charting library.
 *
 * Uses WebSocket for real-time candle updates instead of REST polling.
 *
 * Setup: Place the TradingView `charting_library/` folder inside `frontend/public/`.
 */

import { api, type CandleData } from "./api";
import { candleStore } from "./candle-store";

// -- TradingView type stubs (from charting_library/charting_library.d.ts) --
// These keep us type-safe without importing the library at build time.

interface DatafeedConfiguration {
  supported_resolutions: string[];
  exchanges?: { value: string; name: string; desc: string }[];
  supports_marks?: boolean;
  supports_timescale_marks?: boolean;
  supports_time?: boolean;
}

interface LibrarySymbolInfo {
  name: string;
  full_name: string;
  description: string;
  type: string;
  session: string;
  timezone: string;
  exchange: string;
  listed_exchange: string;
  minmov: number;
  pricescale: number;
  has_intraday: boolean;
  has_daily: boolean;
  has_weekly_and_monthly: boolean;
  supported_resolutions: string[];
  volume_precision: number;
  data_status: string;
  format: string;
}

interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

interface PeriodParams {
  from: number;
  to: number;
  countBack?: number;
  firstDataRequest?: boolean;
}

// Map TradingView resolutions to our backend interval strings
const RESOLUTION_MAP: Record<string, string> = {
  "1": "1m",
  "5": "5m",
  "15": "15m",
  "60": "1h",
  "240": "4h",
  "1D": "1d",
  "D": "1d",
};

const SUPPORTED_RESOLUTIONS = ["1", "5", "15", "60", "240", "1D"];

function candleToBars(candles: CandleData[]): Bar[] {
  return candles.map((c) => ({
    time: (c.timestamp > 1e12 ? c.timestamp : c.timestamp * 1000),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
  }));
}

function candleToBar(candle: CandleData): Bar {
  return {
    time: candle.timestamp > 1e12 ? candle.timestamp : candle.timestamp * 1000,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
    volume: candle.volume,
  };
}

function guessPriceScale(price: number): number {
  if (price >= 1000) return 100;
  if (price >= 1) return 10000;
  if (price >= 0.01) return 1000000;
  return 100000000;
}

interface SubscriptionRecord {
  listenerGuid: string;
  resolution: string;
  symbolInfo: LibrarySymbolInfo;
  onTick: (bar: Bar) => void;
  channel: string;
  cleanup: () => void;
}

export function createCondorDatafeed(
  server: string,
  connector: string,
) {
  const subscriptions = new Map<string, SubscriptionRecord>();
  let lastPrice = 0;

  const datafeed = {
    onReady(callback: (config: DatafeedConfiguration) => void) {
      setTimeout(() => {
        callback({
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          supports_marks: false,
          supports_timescale_marks: false,
          supports_time: true,
        });
      }, 0);
    },

    searchSymbols(
      userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: (results: { symbol: string; full_name: string; description: string; exchange: string; type: string }[]) => void,
    ) {
      onResult([
        {
          symbol: userInput.toUpperCase(),
          full_name: `${connector}:${userInput.toUpperCase()}`,
          description: userInput.toUpperCase(),
          exchange: connector,
          type: "crypto",
        },
      ]);
    },

    async resolveSymbol(
      symbolName: string,
      onResolve: (info: LibrarySymbolInfo) => void,
      _onError: (reason: string) => void,
    ) {
      const pair = symbolName.includes(":") ? symbolName.split(":")[1] : symbolName;

      try {
        const price = await api.getPrice(server, connector, pair);
        lastPrice = price.mid_price;
      } catch {
        // Non-fatal: we just won't have ideal pricescale
      }

      const symbolInfo: LibrarySymbolInfo = {
        name: pair,
        full_name: `${connector}:${pair}`,
        description: pair.replace("-", " / "),
        type: "crypto",
        session: "24x7",
        timezone: "Etc/UTC",
        exchange: connector,
        listed_exchange: connector,
        minmov: 1,
        pricescale: guessPriceScale(lastPrice),
        has_intraday: true,
        has_daily: true,
        has_weekly_and_monthly: false,
        supported_resolutions: SUPPORTED_RESOLUTIONS,
        volume_precision: 2,
        data_status: "streaming",
        format: "price",
      };
      onResolve(symbolInfo);
    },

    async getBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: string,
      periodParams: PeriodParams,
      onResult: (bars: Bar[], meta: { noData?: boolean; nextTime?: number }) => void,
      onError: (reason: string) => void,
    ) {
      const interval = RESOLUTION_MAP[resolution] || "1m";
      const limit = periodParams.countBack || 300;

      try {
        const startTime = periodParams.from;
        const candles = await api.getCandles(
          server,
          connector,
          symbolInfo.name,
          interval,
          Math.min(limit, 5000),
          startTime,
        );

        if (!candles || candles.length === 0) {
          onResult([], { noData: true });
          return;
        }

        const bars = candleToBars(candles);

        // Filter bars within the requested range
        const fromMs = periodParams.from * 1000;
        const toMs = periodParams.to * 1000;
        const filtered = bars.filter((b) => b.time >= fromMs && b.time <= toMs);

        onResult(filtered.length > 0 ? filtered : bars, {
          noData: filtered.length === 0 && bars.length === 0,
        });
      } catch (err) {
        onError(err instanceof Error ? err.message : "Failed to fetch candles");
      }
    },

    subscribeBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: string,
      onTick: (bar: Bar) => void,
      listenerGuid: string,
      _onResetCacheNeeded: () => void,
    ) {
      const interval = RESOLUTION_MAP[resolution] || "1m";
      const channel = `candles:${server}:${connector}:${symbolInfo.name}:${interval}`;

      // Use candle store for subscription + updates
      candleStore.subscribe(channel);

      let lastBarTime = 0;
      const removeListener = candleStore.onUpdate(channel, (candles) => {
        if (!candles.length) return;
        const latest = candles[candles.length - 1];
        const bar = candleToBar(latest);
        // Only tick if bar time is >= last sent (avoid stale ticks)
        if (bar.time >= lastBarTime) {
          lastBarTime = bar.time;
          onTick(bar);
        }
      });

      subscriptions.set(listenerGuid, {
        listenerGuid,
        resolution,
        symbolInfo,
        onTick,
        channel,
        cleanup: removeListener,
      });
    },

    unsubscribeBars(listenerGuid: string) {
      const sub = subscriptions.get(listenerGuid);
      if (sub) {
        sub.cleanup();
        candleStore.unsubscribe(sub.channel);
        subscriptions.delete(listenerGuid);
      }
    },
  };

  return datafeed;
}
