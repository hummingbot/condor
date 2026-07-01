// ── Grid executor state machine (shared by CreateExecutor & CreateGridExecutor) ──

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

export const GRID_STORAGE_KEY = "condor_grid_defaults";

/** localStorage key for the last connector/pair selected on the unified create page. */
export const LAST_MARKET_KEY = "condor_last_market";

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
