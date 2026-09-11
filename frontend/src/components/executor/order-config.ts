// ── Order executor state machine ──
//
// See position-config.ts: state and reducer live beside the panel, not inside
// it, so this module can export what the page and the tests reach for.

import { useMemo, useReducer } from "react";

import type { ChartPriceMapping, ExecutorValidation, PickSlot } from "./types";
import { ORDER_DEFAULTS_KEY } from "@/lib/sessionState";

// ── State ──

export interface OrderState {
  side: 1 | 2;
  amount: number;
  execution_strategy: string;
  price: number;
  leverage: number;
  chaser_distance: number;
  chaser_refresh_threshold: number;
  position_action: string;
  activePickField: string | null;
  /** Whether this market's price has already been offered to `price`; see PositionState. */
  anchored: boolean;
}

export type OrderAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string }
  | { type: "ANCHOR"; price: number };

export const ORDER_DEFAULTS: OrderState = {
  side: 1,
  amount: 0,
  execution_strategy: "LIMIT",
  price: 0,
  leverage: 1,
  chaser_distance: 0.0005,
  chaser_refresh_threshold: 0.001,
  position_action: "OPEN",
  activePickField: null,
  anchored: false,
};

const STORAGE_KEY = ORDER_DEFAULTS_KEY;

const PERSISTED_FIELDS: (keyof OrderState)[] = [
  "side", "amount", "execution_strategy", "leverage",
  "chaser_distance", "chaser_refresh_threshold", "position_action",
];

function loadSavedDefaults(): OrderState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return ORDER_DEFAULTS;
    const saved = JSON.parse(raw);
    const merged = { ...ORDER_DEFAULTS };
    for (const key of PERSISTED_FIELDS) {
      if (key in saved && saved[key] !== undefined) {
        (merged as Record<string, unknown>)[key] = saved[key];
      }
    }
    return merged;
  } catch {
    return ORDER_DEFAULTS;
  }
}

function saveDefaults(state: OrderState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

export function orderReducer(state: OrderState, action: OrderAction): OrderState {
  switch (action.type) {
    case "SET_FIELD":
      return { ...state, [action.field]: action.value };
    case "SET_CONNECTOR":
    case "SET_PAIR":
      return { ...state, price: 0, anchored: false };
    case "ANCHOR": {
      if (state.anchored) return state;
      // Filled whatever the strategy is: a market order hides the field, but
      // switching to LIMIT afterwards should find a price already on the chart.
      const price = state.price > 0 ? state.price : parseFloat(action.price.toPrecision(6));
      return { ...state, price, anchored: true };
    }
    default:
      return state;
  }
}

/** The strategies that rest an order at a price of the user's choosing. */
export function usesLimitPrice(strategy: string): boolean {
  return strategy === "LIMIT" || strategy === "LIMIT_MAKER";
}

// ── Validation ──

export function useOrderValidation(state: OrderState): ExecutorValidation {
  return useMemo(() => {
    const errors: string[] = [];
    if (state.amount <= 0) errors.push("Amount required (base currency)");
    if (usesLimitPrice(state.execution_strategy) && state.price <= 0) {
      errors.push("Price required for limit orders");
    }
    if (state.execution_strategy === "LIMIT_CHASER") {
      if (state.chaser_distance <= 0) errors.push("Chaser distance required");
      if (state.chaser_refresh_threshold <= 0) errors.push("Chaser refresh threshold required");
    }
    return { valid: errors.length === 0, errors };
  }, [state]);
}

// ── Hook ──

export function useOrderConfig() {
  const [state, dispatch] = useReducer(orderReducer, undefined, loadSavedDefaults);
  const validation = useOrderValidation(state);

  const chartProps: ChartPriceMapping = useMemo(() => ({
    // Only a resting order has a price to draw. A market order has none, and a
    // chaser's price is the book's rather than the form's — drawing the stale
    // number either one leaves behind would put a line on the chart that the
    // order will not be placed at.
    startPrice: usesLimitPrice(state.execution_strategy) ? state.price : 0,
    endPrice: 0,
    limitPrice: 0,
    side: state.side,
    minSpread: 0,
    activePickField: state.activePickField === "price" ? "start" : null,
    // The `start` slot carries the order price here.
    lineLabels: { start: "Price" },
  }), [state.price, state.side, state.execution_strategy, state.activePickField]);

  const buildPayload = (connector: string, pair: string, isSpot: boolean) => {
    const config: Record<string, unknown> = {
      connector_name: connector,
      trading_pair: pair,
      side: state.side,
      amount: state.amount,
      leverage: isSpot ? 1 : state.leverage,
      execution_strategy: state.execution_strategy,
    };

    if (usesLimitPrice(state.execution_strategy)) {
      config.price = state.price;
    }
    if (state.execution_strategy === "LIMIT_CHASER") {
      config.chaser_config = {
        distance: state.chaser_distance,
        refresh_threshold: state.chaser_refresh_threshold,
      };
    }
    if (state.position_action !== "OPEN") {
      config.position_action = state.position_action;
    }

    return { executor_type: "order_executor" as const, config };
  };

  const save = () => saveDefaults(state);

  const handleChartPriceSet = (field: PickSlot, price: number) => {
    if (field === "start") {
      dispatch({ type: "SET_FIELD", field: "price", value: price });
    }
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return { state, dispatch, validation, chartProps, buildPayload, save, handleChartPriceSet };
}
