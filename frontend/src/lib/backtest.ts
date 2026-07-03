// -- Backtest result parsing --
//
// Pure normalization layer (no React) that absorbs the variability of the
// backend backtest result shapes: metrics under `results` vs the root, metric
// lookup by multiple aliases, and candle reconstruction from `processed_data`
// in both array-of-objects and columnar formats.

// -- Data Types --

export interface CandleData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ExecutorData {
  id: string;
  timestamp: number;
  closeTimestamp: number;
  side: string;
  closeType: string;
  netPnlQuote: number;
  filledAmountQuote: number;
  entryPrice: number;
  closePrice: number;
}

export interface PnlTimeseriesPoint {
  time: number;
  totalPnl: number;
  executorRealizedPnl: number;
  positionRealizedPnl: number;
  positionUnrealizedPnl: number;
}

export interface PositionHeldPoint {
  time: number;
  longAmount: number;
  shortAmount: number;
  netAmount: number;
  unrealizedPnl: number;
}

export interface BacktestData {
  netPnlQuote: number;
  netPnlPct: number;
  maxDrawdownUsd: number;
  maxDrawdownPct: number;
  totalVolume: number;
  sharpeRatio: number;
  profitFactor: number;
  totalExecutors: number;
  accuracyLong: number;
  accuracyShort: number;
  totalFees: number;
  closeTypes: Record<string, number>;
  candles: CandleData[];
  pnlTimeseries: PnlTimeseriesPoint[];
  positionHeldTimeseries: PositionHeldPoint[];
  executors: ExecutorData[];
  raw: Record<string, unknown>;
}

// -- Results Extraction --

export function extractResults(taskResults: Record<string, unknown>): BacktestData | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = taskResults as any;

  const metrics: Record<string, unknown> =
    (raw.results && typeof raw.results === "object" && !Array.isArray(raw.results))
      ? raw.results
      : raw;

  const num = (obj: Record<string, unknown>, ...keys: string[]): number => {
    for (const k of keys) {
      const v = obj[k];
      if (typeof v === "number") return v;
    }
    return 0;
  };

  const netPnlQuote = num(metrics, "net_pnl_quote", "net_pnl", "total_pnl", "pnl");
  const netPnlPct = num(metrics, "net_pnl", "net_pnl_pct", "return_pct");
  const maxDrawdownUsd = num(metrics, "max_drawdown_usd", "max_drawdown");
  const maxDrawdownPct = num(metrics, "max_drawdown_pct");
  const totalVolume = num(metrics, "total_volume");
  const sharpeRatio = num(metrics, "sharpe_ratio", "sharpe");
  const profitFactor = num(metrics, "profit_factor");
  const totalExecutors = num(metrics, "total_executors", "total_trades", "trade_count");
  const accuracyLong = num(metrics, "accuracy_long");
  const accuracyShort = num(metrics, "accuracy_short");
  const totalFees = num(metrics, "total_fees_quote", "total_fees");

  let closeTypes: Record<string, number> = {};
  const rawCT = metrics.close_types;
  if (rawCT && typeof rawCT === "object") {
    closeTypes = rawCT as Record<string, number>;
  }

  let candles: CandleData[] = [];
  const processedData = raw.processed_data;
  if (processedData) {
    const features = processedData.features ?? processedData;
    if (Array.isArray(features)) {
      candles = features.map((f: Record<string, unknown>) => ({
        time: f.timestamp as number,
        open: f.open as number,
        high: f.high as number,
        low: f.low as number,
        close: f.close as number,
      }));
    } else if (features && typeof features === "object" && features.timestamp) {
      const tsObj = features.timestamp as Record<string, number>;
      if (Array.isArray(tsObj)) {
        const timestamps = tsObj as unknown as number[];
        const opens = features.open as unknown as number[];
        const highs = features.high as unknown as number[];
        const lows = features.low as unknown as number[];
        const closes = features.close as unknown as number[];
        candles = timestamps.map((t: number, i: number) => ({
          time: t,
          open: opens[i],
          high: highs[i],
          low: lows[i],
          close: closes[i],
        }));
      } else {
        const keys = Object.keys(tsObj).sort((a, b) => Number(a) - Number(b));
        const opensObj = (features.open ?? {}) as Record<string, number>;
        const highsObj = (features.high ?? {}) as Record<string, number>;
        const lowsObj = (features.low ?? {}) as Record<string, number>;
        const closesObj = (features.close ?? {}) as Record<string, number>;
        candles = keys.map((k) => ({
          time: tsObj[k],
          open: opensObj[k],
          high: highsObj[k],
          low: lowsObj[k],
          close: closesObj[k],
        }));
      }
    }
  }

  let pnlTimeseries: PnlTimeseriesPoint[] = [];
  const rawPnlTs = raw.pnl_timeseries;
  if (Array.isArray(rawPnlTs) && rawPnlTs.length > 0) {
    pnlTimeseries = rawPnlTs.map((p: Record<string, unknown>) => ({
      time: p.timestamp as number,
      totalPnl: (p.total_pnl ?? 0) as number,
      executorRealizedPnl: (p.executor_realized_pnl ?? 0) as number,
      positionRealizedPnl: (p.position_realized_pnl ?? 0) as number,
      positionUnrealizedPnl: (p.position_unrealized_pnl ?? 0) as number,
    }));
  }

  let executors: ExecutorData[] = [];
  const rawExecutors = raw.executors;
  if (Array.isArray(rawExecutors)) {
    executors = rawExecutors
      .filter((e: Record<string, unknown>) => e.timestamp != null)
      .map((e: Record<string, unknown>) => {
        const config = (e.config ?? {}) as Record<string, unknown>;
        const customInfo = (e.custom_info ?? {}) as Record<string, unknown>;
        return {
          id: String(e.id ?? e.executor_id ?? ""),
          timestamp: (e.timestamp ?? 0) as number,
          closeTimestamp: (e.close_timestamp ?? 0) as number,
          side: normalizeSide(e.side),
          closeType: String(e.close_type ?? ""),
          netPnlQuote: (e.net_pnl_quote ?? 0) as number,
          filledAmountQuote: (e.filled_amount_quote ?? 0) as number,
          entryPrice: (customInfo.current_position_average_price ?? config.entry_price ?? e.entry_price ?? 0) as number,
          closePrice: (customInfo.close_price ?? e.close_price ?? 0) as number,
        };
      });
  }

  let positionHeldTimeseries: PositionHeldPoint[] = [];
  const rawPosTs = raw.position_held_timeseries;
  if (Array.isArray(rawPosTs) && rawPosTs.length > 0) {
    positionHeldTimeseries = rawPosTs.map((p: Record<string, unknown>) => ({
      time: (p.timestamp ?? 0) as number,
      longAmount: (p.long_amount ?? 0) as number,
      shortAmount: (p.short_amount ?? 0) as number,
      netAmount: (p.net_amount ?? 0) as number,
      unrealizedPnl: (p.unrealized_pnl ?? 0) as number,
    }));
  }

  if (netPnlQuote === 0 && totalExecutors === 0 && candles.length === 0 && executors.length === 0 && pnlTimeseries.length === 0) {
    const hasAnything = Object.keys(metrics).length > 0 || Object.keys(raw).length > 1;
    if (!hasAnything) return null;
  }

  return {
    netPnlQuote,
    netPnlPct,
    maxDrawdownUsd,
    maxDrawdownPct,
    totalVolume,
    sharpeRatio,
    profitFactor,
    totalExecutors,
    accuracyLong,
    accuracyShort,
    totalFees,
    closeTypes,
    candles,
    pnlTimeseries,
    positionHeldTimeseries,
    executors,
    raw: taskResults,
  };
}

export function normalizeSide(side: unknown): string {
  if (typeof side === "string") {
    if (side === "1" || side.toUpperCase() === "BUY" || side === "TradeType.BUY") return "BUY";
    if (side === "2" || side.toUpperCase() === "SELL" || side === "TradeType.SELL") return "SELL";
    return side;
  }
  if (typeof side === "number") return side === 1 ? "BUY" : "SELL";
  return String(side ?? "");
}
