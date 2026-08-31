// ── Grid executor state machine (shared by CreateExecutor, GridConfigPanel and DexPool) ──

import { roundToPricePrecision } from "@/lib/formatters";
import { GRID_STORAGE_KEY, LAST_MARKET_KEY } from "@/lib/sessionState";

export interface GridState {
  connector: string;
  pair: string;
  interval: string;
  lookbackSeconds: number;
  side: 1 | 2;
  start_price: number;
  end_price: number;
  limit_price: number;
  total_amount_quote: number;
  min_order_amount_quote: number;
  min_spread_between_orders: number;
  max_open_orders: number;
  max_orders_per_batch: number;
  order_frequency: number;
  leverage: number;
  take_profit: number;
  open_order_type: number;
  take_profit_order_type: number;
  activation_bounds: number;
  keep_position: boolean;
  coerce_tp_to_step: boolean;
  activePickField: "start" | "end" | "limit" | null;
  showAdvanced: boolean;
}

export type GridAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string };

export const GRID_DEFAULTS: GridState = {
  connector: "binance_perpetual",
  pair: "BTC-USDT",
  interval: "5m",
  lookbackSeconds: 3 * 86400,
  side: 1,
  start_price: 0,
  end_price: 0,
  limit_price: 0,
  total_amount_quote: 300,
  min_order_amount_quote: 10,
  min_spread_between_orders: 0.0001,
  max_open_orders: 5,
  max_orders_per_batch: 2,
  order_frequency: 1,
  leverage: 10,
  take_profit: 0.0002,
  open_order_type: 2,
  take_profit_order_type: 2,
  activation_bounds: 0.05,
  keep_position: false,
  coerce_tp_to_step: false,
  activePickField: null,
  showAdvanced: false,
};

// Both are session state, cleared at the session boundary, so `sessionState`
// defines them and this module re-exports for its existing importers.
export { GRID_STORAGE_KEY, LAST_MARKET_KEY };

/** Fields persisted across sessions (no prices — those are per-trade). */
export const GRID_PERSISTED_FIELDS: (keyof GridState)[] = [
  "connector", "pair", "interval", "lookbackSeconds", "side",
  "total_amount_quote", "min_order_amount_quote", "min_spread_between_orders",
  "max_open_orders", "max_orders_per_batch", "order_frequency", "leverage",
  "take_profit", "open_order_type", "take_profit_order_type",
  "activation_bounds", "keep_position", "coerce_tp_to_step",
];

/**
 * Load persisted grid defaults, merged over the hard-coded defaults.
 *
 * @param applyLastMarket When true, the connector/pair are overridden by the
 *   last-used market (`condor_last_market`) if present. Used by the unified
 *   CreateExecutor page so the connector/pair persists across executor types.
 */
export function loadGridDefaults(applyLastMarket = false): GridState {
  try {
    const raw = localStorage.getItem(GRID_STORAGE_KEY);
    const merged = { ...GRID_DEFAULTS };
    if (raw) {
      const saved = JSON.parse(raw);
      for (const key of GRID_PERSISTED_FIELDS) {
        if (key in saved && saved[key] !== undefined) {
          (merged as Record<string, unknown>)[key] = saved[key];
        }
      }
    }
    if (applyLastMarket) {
      try {
        const market = localStorage.getItem(LAST_MARKET_KEY);
        if (market) {
          const { connector, pair } = JSON.parse(market);
          if (connector) merged.connector = connector;
          if (pair) merged.pair = pair;
        }
      } catch { /* ok */ }
    }
    return merged;
  } catch {
    return GRID_DEFAULTS;
  }
}

export function saveGridDefaults(state: GridState) {
  const toSave: Record<string, unknown> = {};
  for (const key of GRID_PERSISTED_FIELDS) {
    toSave[key] = state[key];
  }
  localStorage.setItem(GRID_STORAGE_KEY, JSON.stringify(toSave));
}

export function isSpotConnector(connector: string): boolean {
  return !connector.includes("perpetual");
}

export function gridReducer(state: GridState, action: GridAction): GridState {
  switch (action.type) {
    case "SET_FIELD": {
      const next = { ...state, [action.field]: action.value };
      // Force leverage=1 for spot connectors
      if (action.field === "leverage" && isSpotConnector(next.connector)) {
        next.leverage = 1;
      }
      return next;
    }
    case "SET_CONNECTOR": {
      const spot = isSpotConnector(action.value);
      return {
        ...state,
        connector: action.value,
        start_price: 0,
        end_price: 0,
        limit_price: 0,
        leverage: spot ? 1 : state.leverage,
      };
    }
    case "SET_PAIR":
      return { ...state, pair: action.value, start_price: 0, end_price: 0, limit_price: 0 };
    default:
      return state;
  }
}

// ── Intervals ──

export const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];

export const LOOKBACK_OPTIONS: { label: string; seconds: number }[] = [
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 6 * 3600 },
  { label: "1d", seconds: 86400 },
  { label: "3d", seconds: 3 * 86400 },
  { label: "7d", seconds: 7 * 86400 },
  { label: "14d", seconds: 14 * 86400 },
  { label: "30d", seconds: 30 * 86400 },
];

// ── Price rules ──
//
// One copy of the grid's ordering rules, read by the panel's error list, by the
// page's sticky footer / chat "blocked by" fact, by the per-field validity icons
// and by the chart write-back. They used to be written out three times in
// GridConfigPanel with drifting wording, so for the same state the footer could
// name a different rule than the panel's first red line.

/** Relative gap the clamp leaves, so the strict inequalities survive rounding. */
export const GRID_MIN_SEPARATION = 0.0001;

function tickOf(pricePrecision?: number | null): number {
  return pricePrecision != null ? 1 / 10 ** pricePrecision : 0;
}

/** The nearest price strictly below `bound`, on the tick grid when there is one. */
function justBelow(bound: number, pricePrecision?: number | null): number {
  const gap = Math.max(bound * GRID_MIN_SEPARATION, tickOf(pricePrecision));
  const rounded = roundToPricePrecision(bound - gap, pricePrecision);
  return rounded < bound ? rounded : bound - gap;
}

/** The nearest price strictly above `bound`, on the tick grid when there is one. */
function justAbove(bound: number, pricePrecision?: number | null): number {
  const gap = Math.max(bound * GRID_MIN_SEPARATION, tickOf(pricePrecision));
  const rounded = roundToPricePrecision(bound + gap, pricePrecision);
  return rounded > bound ? rounded : bound + gap;
}

/**
 * Bound a proposed price against the other two prices and the side, returning the
 * nearest legal price.
 *
 * Written for a gesture: a chart click today and a chart drag tomorrow hand over a
 * candidate price and get back one the form will accept, so the rules apply during
 * the gesture instead of turning into red text underneath it. A `0` neighbour means
 * "not set yet" and imposes no bound — picking `start` first must not clamp against
 * an `end` the user has not chosen.
 *
 * When both bounds could apply, the start/end ordering wins: that pair is the range
 * the user is drawing, and the limit trails it.
 */
export function clampGridPrice(
  field: "start" | "end" | "limit",
  price: number,
  state: GridState,
  pricePrecision?: number | null,
): number {
  if (!Number.isFinite(price) || price <= 0) return price;
  const { start_price: start, end_price: end, limit_price: limit, side } = state;

  switch (field) {
    case "start":
      if (end > 0 && price >= end) return justBelow(end, pricePrecision);
      if (side === 1 && limit > 0 && price <= limit) return justAbove(limit, pricePrecision);
      return price;
    case "end":
      if (start > 0 && price <= start) return justAbove(start, pricePrecision);
      if (side === 2 && limit > 0 && price >= limit) return justBelow(limit, pricePrecision);
      return price;
    case "limit":
      if (side === 1) return start > 0 && price >= start ? justBelow(start, pricePrecision) : price;
      return end > 0 && price <= end ? justAbove(end, pricePrecision) : price;
  }
}

/** The ordering rules, as the messages the user reads. */
export function gridPriceErrors(state: GridState): string[] {
  const errors: string[] = [];

  if (state.start_price > 0 && state.end_price > 0 && state.start_price >= state.end_price) {
    errors.push("Start price must be < end price");
  }
  if (state.side === 1 && state.limit_price > 0 && state.start_price > 0 && state.limit_price >= state.start_price) {
    errors.push("LONG: limit must be < start price");
  }
  if (state.side === 2 && state.limit_price > 0 && state.end_price > 0 && state.limit_price <= state.end_price) {
    errors.push("SHORT: limit must be > end price");
  }
  if (state.start_price <= 0 || state.end_price <= 0 || state.limit_price <= 0) {
    errors.push("All prices required");
  }

  return errors;
}

/** Everything that blocks a grid from being created: the price rules plus the amounts. */
export function gridConfigErrors(state: GridState): string[] {
  const errors = gridPriceErrors(state);

  if (state.total_amount_quote <= 0) {
    errors.push("Total amount required");
  }
  if (
    state.total_amount_quote > 0 &&
    state.min_order_amount_quote > 0 &&
    state.total_amount_quote < state.min_order_amount_quote
  ) {
    errors.push("Total must be >= min order amount");
  }

  return errors;
}

/**
 * Whether one price field is settled: set, and ordered against the price it is
 * measured from. An unset neighbour leaves it unsettled — this answers "show the
 * check or the warning", not "is this price legal", which is `clampGridPrice`.
 */
export function gridPriceFieldValid(field: "start" | "end" | "limit", state: GridState): boolean {
  switch (field) {
    case "start":
      return state.start_price > 0 && state.start_price < state.end_price;
    case "end":
      return state.end_price > 0 && state.end_price > state.start_price;
    case "limit":
      return (
        state.limit_price > 0 &&
        (state.side === 1
          ? state.limit_price < state.start_price
          : state.limit_price > state.end_price)
      );
  }
}
