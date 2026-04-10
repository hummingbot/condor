import type { ExecutorInfo } from "./api";

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
  /** Full-width price lines (only shown for ≤ 1 executor) */
  priceLines: PriceLine[];
  markers: ChartMarker[];
  /** Entry→exit segment line (position/generic executors) */
  segment?: ExecutorSegment;
  /** Grid range box (grid executors) */
  gridBox?: GridBox;
  timeRange: { start: number; end: number };
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

function pnlColor(pnl: number): string {
  return pnl >= 0 ? "#22c55e" : "#ef4444";
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
      color: "#ef4444",
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
      color: "#22c55e",
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
      color: pnlPositive ? "#22c55e" : "#ef4444",
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
      color: pnlColor(executor.pnl),
    };
  }

  // Entry marker
  if (entry > 0 && executor.timestamp > 0) {
    markers.push({
      time: executor.timestamp,
      price: entry,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: side === "buy" ? "#22c55e" : "#ef4444",
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
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
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
      color: profitable ? "#22c55e" : "#ef4444",
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
    priceLines: [],
    markers: [],
    gridBox,
    timeRange: { start, end },
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
    lines.push({ price: closePrice, label: "Close", color: pnlPositive ? "#22c55e" : "#ef4444", style: "dashed" });
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
      color: pnlColor(executor.pnl),
    };
  }

  if (entryPrice > 0 && executor.timestamp > 0) {
    markers.push({
      time: executor.timestamp,
      price: entryPrice,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: side === "buy" ? "#22c55e" : "#ef4444",
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
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
  };
}

// ── Public API ──

export function computeExecutorOverlay(executor: ExecutorInfo): ExecutorOverlay {
  switch (executor.type?.toLowerCase()) {
    case "position":
      return computePositionOverlay(executor);
    case "grid":
      return computeGridOverlay(executor);
    default:
      return computeGenericOverlay(executor);
  }
}

/** PnL-based color: green for profit, red for loss */
export function getExecutorColor(_index: number, pnl?: number): string {
  return (pnl ?? 0) >= 0 ? "#22c55e" : "#ef4444";
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
