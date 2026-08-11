import type { ExecutorInfo } from "./api";
import { getThemeColors, pnlHexColor, sideColor } from "./theme-colors";

// ── Overlay Model ──

export interface PriceLine {
  price: number;
  label: string;
  color: string;
  style: "solid" | "dashed" | "dotted";
  lineWidth?: number;
}

export interface ChartMarker {
  time: number;
  price: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  color: string;
  text: string;
}

/** A line segment connecting entry → exit on the chart */
export interface ExecutorSegment {
  entryTime: number;
  entryPrice: number;
  exitTime: number;
  exitPrice: number;
  color: string;
}

/** A box representing a grid executor's price range over time */
export interface GridBox {
  startTime: number;
  endTime: number;
  startPrice: number;
  endPrice: number;
  limitPrice?: number;
  color: string;
}

export interface ExecutorOverlay {
  executorId: string;
  type: string;
  side: "buy" | "sell";
  status: string;
  closeType: string;
  pnl: number;
  pnlPct: number;
  volume: number;
  fees: number;
  /** Full-width price lines (only shown for ≤ 1 executor) */
  priceLines: PriceLine[];
  markers: ChartMarker[];
  /** Entry→exit segment line (position/generic executors) */
  segment?: ExecutorSegment;
  /** Grid range box (grid executors) */
  gridBox?: GridBox;
  timeRange: { start: number; end: number };
  /** Original executor config for rich tooltips */
  config?: Record<string, unknown>;
  /** Entry price for display */
  entryPrice?: number;
  /** Exit/current price for display */
  exitPrice?: number;
}

// ── Helpers ──

function normSide(side: string): "buy" | "sell" {
  const s = side.toLowerCase();
  return s === "buy" || s === "1" ? "buy" : "sell";
}

function closeTypeLabel(closeType: string): string {
  const ct = closeType?.toLowerCase() ?? "";
  if (ct.includes("take_profit") || ct.includes("tp")) return "TP";
  if (ct.includes("stop_loss") || ct.includes("sl")) return "SL";
  if (ct.includes("trailing")) return "TS";
  if (ct.includes("time_limit")) return "TL";
  if (ct.includes("early_stop")) return "ES";
  return ct ? ct.replace(/_/g, " ") : "closed";
}

function isActiveStatus(status: string): boolean {
  const s = status?.toLowerCase() ?? "";
  return s === "running" || s === "active_position" || s === "active";
}

// ── Position Executor Overlay ──

function computePositionOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const side = normSide(String(customInfo.side || executor.side));
  const config = executor.config || {};
  const entry =
    Number(customInfo.current_position_average_price) ||
    executor.entry_price ||
    0;
  const closePrice =
    Number(customInfo.close_price) ||
    executor.current_price ||
    0;
  const lines: PriceLine[] = [];
  const markers: ChartMarker[] = [];

  // Entry price line (shown only for single-executor view)
  if (entry > 0) {
    lines.push({
      price: entry,
      label: "Entry",
      color: "#ffffff",
      style: "solid",
      lineWidth: 2,
    });
  }

  // Stop Loss
  const slPct = Number(config.stop_loss);
  if (entry > 0 && slPct > 0 && slPct !== -1) {
    const slPrice = side === "buy" ? entry * (1 - slPct) : entry * (1 + slPct);
    lines.push({
      price: slPrice,
      label: `SL (${(slPct * 100).toFixed(1)}%)`,
      color: getThemeColors().red,
      style: "dashed",
    });
  }

  // Take Profit
  const tpPct = Number(config.take_profit);
  if (entry > 0 && tpPct > 0 && tpPct !== -1) {
    const tpPrice = side === "buy" ? entry * (1 + tpPct) : entry * (1 - tpPct);
    lines.push({
      price: tpPrice,
      label: `TP (${(tpPct * 100).toFixed(1)}%)`,
      color: getThemeColors().green,
      style: "dashed",
    });
  }

  // Trailing stop
  const tsActivation = Number(config.trailing_stop_activation_price_delta);
  if (entry > 0 && tsActivation > 0) {
    const activationPrice =
      side === "buy" ? entry * (1 + tsActivation) : entry * (1 - tsActivation);
    lines.push({
      price: activationPrice,
      label: "TS Activation",
      color: "#f59e0b",
      style: "dotted",
    });
  }

  // Break-even
  const breakEven = Number(customInfo.break_even_price ?? customInfo.breakeven_price);
  if (breakEven > 0) {
    lines.push({
      price: breakEven,
      label: "Break-even",
      color: "#eab308",
      style: "dotted",
    });
  }

  // Close price line
  if (closePrice > 0 && closePrice !== entry) {
    const pnlPositive = side === "buy" ? closePrice > entry : closePrice < entry;
    lines.push({
      price: closePrice,
      label: "Close",
      color: pnlHexColor(pnlPositive ? 1 : -1),
      style: "dashed",
    });
  }

  // Segment: entry → exit
  let segment: ExecutorSegment | undefined;
  if (entry > 0 && executor.timestamp > 0) {
    const exitP = closePrice > 0 ? closePrice : entry;
    const exitT = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);
    segment = {
      entryTime: executor.timestamp,
      entryPrice: entry,
      exitTime: exitT,
      exitPrice: exitP,
      color: pnlHexColor(executor.pnl),
    };
  }

  // Entry marker
  if (entry > 0 && executor.timestamp > 0) {
    markers.push({
      time: executor.timestamp,
      price: entry,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: sideColor(side),
      text: side === "buy" ? "BUY" : "SELL",
    });
  }

  // Close marker
  if (executor.close_timestamp > 0 && (entry > 0 || closePrice > 0)) {
    const markerPrice = closePrice > 0 ? closePrice : entry;
    markers.push({
      time: executor.close_timestamp,
      price: markerPrice,
      position: side === "buy" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: segment?.color ?? "#6b7280",
      text: closeTypeLabel(executor.close_type),
    });
  }

  const start = executor.timestamp > 0 ? executor.timestamp : Math.floor(Date.now() / 1000);
  const end = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);

  return {
    executorId: executor.id,
    type: "position",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: entry,
    exitPrice: closePrice,
  };
}

// ── Grid Executor Overlay ──

function computeGridOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const side = normSide(executor.side);
  const config = executor.config || {};

  const startPrice = Number(config.start_price);
  const endPrice = Number(config.end_price);
  const limitPrice = Number(config.limit_price);

  const start = executor.timestamp > 0 ? executor.timestamp : Math.floor(Date.now() / 1000);
  const end = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);

  // Grid box: rectangle from start_price to end_price over the executor lifetime
  let gridBox: GridBox | undefined;
  if (startPrice > 0 && endPrice > 0 && start > 0) {
    const profitable = executor.pnl >= 0;
    gridBox = {
      startTime: start,
      endTime: end,
      startPrice,
      endPrice,
      limitPrice: limitPrice > 0 ? limitPrice : undefined,
      color: pnlHexColor(profitable ? 1 : -1),
    };
  }

  return {
    executorId: executor.id,
    type: "grid",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: [],
    markers: [],
    gridBox,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: startPrice,
    exitPrice: endPrice,
  };
}

// ── LP Executor Overlay ──

/**
 * A CLMM liquidity position, drawn as the grid it structurally is.
 *
 * `lower_price` / `upper_price` are the range the position earns fees in, and the
 * schema itself describes `upper_limit_price` / `lower_limit_price` as
 * "grid-executor style" auto-close triggers. That is a one-to-one match with
 * `GridBox`, and `TradeChart` draws boxes without ever reading `type` — so no new
 * drawing code is involved, only a second producer of the same struct.
 */
function computeLpOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const config = executor.config || {};
  const side = normSide(String(customInfo.side || executor.side || config.side));

  // custom_info wins: a CLMM position is snapped to the venue's bins, so the
  // on-chain bounds are not the requested ones, and the box has to show where the
  // liquidity actually sits. Same precedence handlers/dex/liquidity.py applies when
  // it reads positions back.
  const num = (v: unknown) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const lower = num(customInfo.lower_price ?? customInfo.price_lower ?? config.lower_price);
  const upper = num(customInfo.upper_price ?? customInfo.price_upper ?? config.upper_price);
  const upperLimit = num(customInfo.upper_limit_price ?? config.upper_limit_price);
  const lowerLimit = num(customInfo.lower_limit_price ?? config.lower_limit_price);

  const start = executor.timestamp > 0 ? executor.timestamp : Math.floor(Date.now() / 1000);
  const end = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);

  let gridBox: GridBox | undefined;
  if (lower > 0 && upper > 0 && start > 0) {
    gridBox = {
      startTime: start,
      endTime: end,
      // startPrice is the box's dashed edge and endPrice its solid one; the grid
      // overlay puts start_price (the far bound) first, so upper goes first here.
      startPrice: upper,
      endPrice: lower,
      limitPrice: upperLimit > 0 ? upperLimit : undefined,
      color: pnlHexColor(executor.pnl >= 0 ? 1 : -1),
    };
  }

  // Both triggers, as full-width lines. TradeChart only draws these for a running
  // or selected executor, which is exactly when they are actionable.
  const lines: PriceLine[] = [];
  if (upperLimit > 0) {
    lines.push({ price: upperLimit, label: "Upper limit", color: getThemeColors().red, style: "dotted" });
  }
  if (lowerLimit > 0) {
    lines.push({ price: lowerLimit, label: "Lower limit", color: getThemeColors().red, style: "dotted" });
  }

  return {
    executorId: executor.id,
    type: "lp",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers: [],
    gridBox,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: lower,
    exitPrice: upper,
  };
}

// ── Order Executor Overlay ──

function computeOrderOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const config = executor.config || {};
  const side = normSide(String(customInfo.side || executor.side || config.side));
  const lines: PriceLine[] = [];
  const markers: ChartMarker[] = [];

  const isChaser = String(config.execution_strategy ?? "").toUpperCase() === "LIMIT_CHASER";

  const orderPrice =
    (executor.entry_price > 0 ? executor.entry_price : 0) ||
    (Number(config.price) > 0 ? Number(config.price) : 0) ||
    (executor.current_price > 0 ? executor.current_price : 0) ||
    0;
  const closePrice =
    Number(customInfo.close_price) ||
    executor.current_price ||
    0;

  // Build descriptive label: "BUY 0.5" or "SELL 1.2 chasing"
  const amount = Number(config.amount);
  const sideLabel = side.toUpperCase();
  const amountStr = amount > 0 ? ` ${amount}` : "";
  const chaserSuffix = isChaser ? " chasing" : "";
  const descriptiveLabel = `${sideLabel}${amountStr}${chaserSuffix}`;

  const active = isActiveStatus(executor.status);
  const start = executor.timestamp > 0 ? executor.timestamp : Math.floor(Date.now() / 1000);
  const end = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);

  let segment: ExecutorSegment | undefined;

  if (active && orderPrice > 0) {
    // Active/running: horizontal line at order price
    segment = {
      entryTime: start,
      entryPrice: orderPrice,
      exitTime: Math.floor(Date.now() / 1000),
      exitPrice: orderPrice,
      color: sideColor(side),
    };

    if (orderPrice > 0) {
      lines.push({
        price: orderPrice,
        label: descriptiveLabel,
        color: sideColor(side),
        style: isChaser ? "dotted" : "solid",
        lineWidth: 2,
      });
    }
  } else if (!active && orderPrice > 0) {
    // Finished: triangle marker at execution point
    const fillPrice = closePrice > 0 ? closePrice : orderPrice;

    // Entry marker
    markers.push({
      time: start,
      price: orderPrice,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: sideColor(side),
      text: side === "buy" ? "BUY" : "SELL",
    });

    // Close marker (triangle)
    if (executor.close_timestamp > 0) {
      markers.push({
        time: executor.close_timestamp,
        price: fillPrice,
        position: side === "buy" ? "aboveBar" : "belowBar",
        shape: side === "buy" ? "arrowDown" : "arrowUp",
        color: pnlHexColor(executor.pnl),
        text: closeTypeLabel(executor.close_type),
      });
    }

    // Short segment from entry to close
    if (executor.close_timestamp > 0) {
      segment = {
        entryTime: start,
        entryPrice: orderPrice,
        exitTime: executor.close_timestamp,
        exitPrice: fillPrice,
        color: pnlHexColor(executor.pnl),
      };
    }
  }

  return {
    executorId: executor.id,
    type: "order",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: orderPrice,
    exitPrice: closePrice,
  };
}

// ── Generic Executor Overlay (fallback) ──

function computeGenericOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const side = normSide(String(customInfo.side || executor.side));
  const lines: PriceLine[] = [];
  const markers: ChartMarker[] = [];
  const entryPrice =
    executor.entry_price ||
    Number(customInfo.current_position_average_price) ||
    0;
  const closePrice =
    executor.current_price ||
    Number(customInfo.close_price) ||
    0;

  if (entryPrice > 0) {
    lines.push({ price: entryPrice, label: "Entry", color: "#ffffff", style: "solid", lineWidth: 2 });
  }
  if (closePrice > 0 && closePrice !== entryPrice) {
    const pnlPositive = side === "buy" ? closePrice > entryPrice : closePrice < entryPrice;
    lines.push({ price: closePrice, label: "Close", color: pnlHexColor(pnlPositive ? 1 : -1), style: "dashed" });
  }

  // Segment
  let segment: ExecutorSegment | undefined;
  if (entryPrice > 0 && executor.timestamp > 0) {
    const exitP = closePrice > 0 ? closePrice : entryPrice;
    const exitT = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);
    segment = {
      entryTime: executor.timestamp,
      entryPrice: entryPrice,
      exitTime: exitT,
      exitPrice: exitP,
      color: pnlHexColor(executor.pnl),
    };
  }

  if (entryPrice > 0 && executor.timestamp > 0) {
    markers.push({
      time: executor.timestamp,
      price: entryPrice,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: sideColor(side),
      text: side.toUpperCase(),
    });
  }

  if (executor.close_timestamp > 0 && (entryPrice > 0 || closePrice > 0)) {
    markers.push({
      time: executor.close_timestamp,
      price: closePrice > 0 ? closePrice : entryPrice,
      position: side === "buy" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: segment?.color ?? "#6b7280",
      text: closeTypeLabel(executor.close_type),
    });
  }

  const start = executor.timestamp > 0 ? executor.timestamp : Math.floor(Date.now() / 1000);
  const end = executor.close_timestamp > 0 ? executor.close_timestamp : Math.floor(Date.now() / 1000);

  return {
    executorId: executor.id,
    type: executor.type?.toLowerCase() || "unknown",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: entryPrice,
    exitPrice: closePrice,
  };
}

// ── Public API ──

export function computeExecutorOverlay(executor: ExecutorInfo): ExecutorOverlay {
  switch (executor.type?.toLowerCase()) {
    case "position":
      return computePositionOverlay(executor);
    case "grid":
      return computeGridOverlay(executor);
    case "order":
      return computeOrderOverlay(executor);
    case "lp":
      return computeLpOverlay(executor);
    default:
      return computeGenericOverlay(executor);
  }
}

/** PnL-based color: green for profit, red for loss */
export function getExecutorColor(_index: number, pnl?: number): string {
  return pnlHexColor(pnl ?? 0);
}

export function computeMultiOverlays(executors: ExecutorInfo[]): ExecutorOverlay[] {
  return executors.map((ex) => computeExecutorOverlay(ex));
}

function toSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

export function getOverlayTimeRange(overlays: ExecutorOverlay[]): { start: number; end: number } {
  if (overlays.length === 0) {
    const now = Math.floor(Date.now() / 1000);
    return { start: now - 3600, end: now };
  }
  let start = Infinity;
  let end = -Infinity;
  for (const o of overlays) {
    const s = toSeconds(o.timeRange.start);
    const e = toSeconds(o.timeRange.end);
    if (s < start) start = s;
    if (e > end) end = e;
  }
  return { start, end };
}

/**
 * The pool a group of DEX/LP executors traded in, from whichever records one.
 *
 * Passing it to the candles endpoint charts the exact pool the position traded in
 * rather than the token's current top pool. CEX executors carry none, so the
 * result is `undefined` and the normal candle path applies.
 */
export function getPoolAddress(executors: ExecutorInfo[]): string | undefined {
  for (const ex of executors) {
    const pool =
      (ex.config?.pool_address as string | undefined) ??
      (ex.custom_info?.pool_address as string | undefined);
    if (pool) return pool;
  }
  return undefined;
}

/**
 * Group executors by `connector:trading_pair` for per-market charts.
 * Executors without a `trading_pair` are skipped. Insertion order is preserved.
 */
export function groupExecutorsByMarket(
  executors: ExecutorInfo[],
): [string, ExecutorInfo[]][] {
  const groups = new Map<string, ExecutorInfo[]>();
  for (const ex of executors) {
    if (!ex.trading_pair) continue;
    const key = `${ex.connector}:${ex.trading_pair}`;
    const arr = groups.get(key);
    if (arr) arr.push(ex);
    else groups.set(key, [ex]);
  }
  return Array.from(groups.entries());
}
