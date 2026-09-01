import type { ExecutorInfo } from "./api";
import { escapeHtml, formatPriceSig } from "./formatters";
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
 * `lower_price` / `upper_price` are the range the position earns fees in, which is
 * a one-to-one match with `GridBox`, and `TradeChart` draws boxes without ever
 * reading `type` — so no new drawing code is involved, only a second producer of
 * the same struct.
 *
 * The `*_limit_price` auto-close triggers are deliberately left off an open
 * position: they are a decision made once, while the range is being drawn, and on
 * the chart afterwards they are two more red lines competing with the bounds that
 * actually say whether the position is still earning. LPConfigPanel still draws
 * them while you set them.
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
      color: pnlHexColor(executor.pnl >= 0 ? 1 : -1),
    };
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
    priceLines: [],
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

// ── Hover Card ──

/**
 * How the card spells money.
 *
 * The two charts that draw it read different currencies — the trade pane
 * converts the pair's quote asset into the reader's display currency
 * (ARCH-207), the executor chart converts its own pair's — so the card takes
 * the formatters instead of picking one. `formatValue` is for magnitudes
 * (volume, fees, notional), `formatPnl` for the signed number.
 */
export interface OverlayTooltipFormatters {
  formatValue: (val: number) => string;
  formatPnl: (val: number) => string;
}

/**
 * The hover card describing one overlay, as an HTML string.
 *
 * It lived twice — once in TradeChart and once in ExecutorChart — and the two
 * copies drifted in every direction they could: one was theme-aware and the
 * other hardcoded the dark palette into markup sitting on `--color-surface`
 * (white on white in the light theme), one drew LP bounds and the other did
 * not, one used the canonical `formatPriceSig` and the other re-hand-rolled
 * that ladder, one converted currency and the other said dollars. This is the
 * union of what each got right, in the module that already owns the overlay
 * vocabulary it describes.
 *
 * Every value that comes off the backend — ids, sides, statuses, close types,
 * and every config field a detail row prints — is passed through `escapeHtml`
 * (SEC-018), which is the fix this duplication previously cost two edits.
 * Colours come from `getThemeColors()` rather than a local `getComputedStyle`,
 * which is CORR-057's rule.
 */
export function renderOverlayTooltipHtml(
  o: ExecutorOverlay,
  { formatValue, formatPnl }: OverlayTooltipFormatters,
): string {
  const { textMuted, border, green, red } = getThemeColors();

  const pnlClr = pnlHexColor(o.pnl);
  const pnlStr = escapeHtml(formatPnl(o.pnl));
  const pctStr = o.pnlPct !== 0 ? `${o.pnlPct > 0 ? "+" : ""}${(o.pnlPct * 100).toFixed(2)}%` : "";
  const volStr = escapeHtml(formatValue(o.volume));
  const feesStr = o.fees ? escapeHtml(formatValue(o.fees)) : "";

  // An LP position has no direction -- it is `RANGE` -- and the buy/sell
  // normalization files everything that is not a buy under "sell", which
  // labelled a live two-sided range position `SELL`. Neutral for that type.
  const isRangeSide = o.type === "lp";
  const sideLabel = isRangeSide ? "range" : o.side;
  const sideClr = isRangeSide ? "#9ca3af" : sideColor(o.side);
  const sideBg = isRangeSide
    ? "rgba(156,163,175,0.15)"
    : o.side === "buy"
      ? "rgba(34,197,94,0.15)"
      : "rgba(239,68,68,0.15)";
  const active = isActiveStatus(o.status);
  const statusBg = active ? "rgba(34,197,94,0.15)" : "rgba(156,163,175,0.15)";
  const statusClr = active ? green : "#9ca3af";

  // Build config detail rows
  const cfg = o.config || {};
  const tripleBarrier: Record<string, unknown> = (() => {
    const raw = cfg.triple_barrier_config;
    if (!raw) return {};
    if (typeof raw === "string") { try { return JSON.parse(raw); } catch { return {}; } }
    return typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  })();

  let detailRows = "";
  const addRow = (label: string, value: string, color?: string) => {
    detailRows += `<div style="display:flex;justify-content:space-between;gap:12px"><span style="color:${textMuted}">${escapeHtml(label)}</span><span style="font-family:monospace;${color ? `color:${color}` : ""}">${escapeHtml(value)}</span></div>`;
  };

  // Range-box details. Any executor drawn as a box describes itself by its
  // bounds, not by an entry→exit pair; only the labels differ per type.
  const entryPrice = o.entryPrice ?? o.segment?.entryPrice;
  const exitPrice = o.exitPrice ?? o.segment?.exitPrice;
  if (o.gridBox) {
    if (o.type === "lp") {
      // startPrice is the box's upper edge (see computeLpOverlay).
      addRow("Upper Price", formatPriceSig(o.gridBox.startPrice));
      addRow("Lower Price", formatPriceSig(o.gridBox.endPrice));
      if (cfg.lp_provider != null) addRow("Provider", String(cfg.lp_provider));
    } else {
      addRow("Start Price", formatPriceSig(o.gridBox.startPrice));
      addRow("End Price", formatPriceSig(o.gridBox.endPrice));
      if (o.gridBox.limitPrice) addRow("Limit Price", formatPriceSig(o.gridBox.limitPrice));
    }
  } else if (entryPrice && entryPrice > 0) {
    addRow("Entry", formatPriceSig(entryPrice));
    if (exitPrice && exitPrice > 0 && exitPrice !== entryPrice) {
      addRow(active ? "Current" : "Close", formatPriceSig(exitPrice));
    }
  }

  if (cfg.leverage != null && Number(cfg.leverage) > 1) addRow("Leverage", `${cfg.leverage}x`);
  if (cfg.total_amount_quote != null) addRow("Amount", formatValue(Number(cfg.total_amount_quote)));
  else if (cfg.amount != null && Number(cfg.amount) > 0) addRow("Amount", String(cfg.amount));

  const tp = Number(tripleBarrier.take_profit || cfg.take_profit);
  if (tp > 0 && tp !== -1) addRow("Take Profit", `${(tp * 100).toFixed(2)}%`, green);
  const sl = Number(cfg.stop_loss);
  if (sl > 0 && sl !== -1) addRow("Stop Loss", `${(sl * 100).toFixed(2)}%`, red);
  if (cfg.keep_position != null) addRow("Keep Position", String(cfg.keep_position) === "true" ? "Yes" : "No");

  return `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
            <span style="font-weight:700;font-size:12px;font-family:monospace">${escapeHtml(o.executorId.slice(0, 10))}…</span>
            <span style="background:${sideBg};color:${sideClr};font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;text-transform:uppercase">${escapeHtml(sideLabel)}</span>
            <span style="background:${statusBg};color:${statusClr};font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px">${escapeHtml(o.status)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:4px;margin-bottom:2px">
            <span style="background:${border};padding:1px 5px;border-radius:3px;font-size:10px;border:1px solid ${border}">${escapeHtml(o.type.toUpperCase())}</span>
            ${o.closeType ? `<span style="font-size:10px;color:${textMuted}">${escapeHtml(o.closeType)}</span>` : ""}
          </div>
          <div style="border-top:1px solid ${border};margin:6px 0;padding-top:6px;display:grid;grid-template-columns:1fr 1fr;gap:4px 16px">
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">Net PnL</div><div style="font-weight:600;font-size:13px;color:${pnlClr};font-family:monospace">${pnlStr}</div></div>
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">PnL %</div><div style="font-weight:600;font-size:13px;color:${pnlClr};font-family:monospace">${pctStr || "—"}</div></div>
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">Volume</div><div style="font-family:monospace;font-size:11px">${volStr}</div></div>
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">Fees</div><div style="font-family:monospace;font-size:11px">${feesStr || "—"}</div></div>
          </div>
          ${detailRows ? `<div style="border-top:1px solid ${border};margin-top:4px;padding-top:6px;font-size:11px;display:flex;flex-direction:column;gap:3px">${detailRows}</div>` : ""}
        `;
}
