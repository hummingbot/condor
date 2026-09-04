// ── Position executor state machine ──
//
// State, reducer and chart mapping, kept out of the panel component so the file
// that owns them can also export the pieces the page and the tests need — the
// split `lp-config.ts` already makes for the LP panel.

import { useMemo, useReducer } from "react";

import type { ChartPriceMapping, ExecutorValidation, ExtraLine, PickSlot } from "./types";
import {
  STOP_LOSS_SLOT,
  TAKE_PROFIT_SLOT,
  barrierLabel,
  barrierPct,
  barrierPrice,
} from "./barriers";
import { getThemeColors } from "@/lib/theme-colors";
import { POSITION_DEFAULTS_KEY } from "@/lib/sessionState";

// ── State ──

export interface PositionState {
  side: 1 | 2;
  amount: number;
  entry_price: number; // 0 = market order
  leverage: number;
  stop_loss: number; // decimal e.g. 0.02 = 2%, 0 = disabled
  take_profit: number;
  time_limit: number; // seconds, 0 = disabled
  trailing_stop_activation_price: number; // 0 = disabled
  trailing_stop_trailing_delta: number;
  open_order_type: number;
  take_profit_order_type: number;
  stop_loss_order_type: number;
  time_limit_order_type: number;
  activation_bounds: number; // 0 = disabled
  activePickField: string | null;
  showAdvanced: boolean;
  /**
   * Whether the entry has already been offered the market's price.
   *
   * The anchoring has to happen once per market, not once per price tick: the
   * live price changes every second, and re-running the fill on each one would
   * overwrite the `0` a user typed to ask for a market order. So the fact that
   * it ran is state, cleared only when the market underneath it changes.
   */
  anchored: boolean;
}

export type PositionAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string }
  | { type: "ANCHOR"; price: number };

export const POSITION_DEFAULTS: PositionState = {
  side: 1,
  amount: 0,
  entry_price: 0,
  leverage: 10,
  stop_loss: 0.03,
  take_profit: 0.02,
  time_limit: 0,
  trailing_stop_activation_price: 0,
  trailing_stop_trailing_delta: 0,
  open_order_type: 1,
  take_profit_order_type: 1,
  stop_loss_order_type: 1,
  time_limit_order_type: 1,
  activation_bounds: 0,
  activePickField: null,
  showAdvanced: false,
  anchored: false,
};

const STORAGE_KEY = POSITION_DEFAULTS_KEY;

const PERSISTED_FIELDS: (keyof PositionState)[] = [
  "side", "amount", "leverage", "stop_loss", "take_profit",
  "time_limit", "trailing_stop_activation_price", "trailing_stop_trailing_delta",
  "open_order_type", "take_profit_order_type", "stop_loss_order_type",
  "time_limit_order_type", "activation_bounds",
];

function loadSavedDefaults(): PositionState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return POSITION_DEFAULTS;
    const saved = JSON.parse(raw);
    const merged = { ...POSITION_DEFAULTS };
    for (const key of PERSISTED_FIELDS) {
      if (key in saved && saved[key] !== undefined) {
        (merged as Record<string, unknown>)[key] = saved[key];
      }
    }
    return merged;
  } catch {
    return POSITION_DEFAULTS;
  }
}

function saveDefaults(state: PositionState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

export function positionReducer(state: PositionState, action: PositionAction): PositionState {
  switch (action.type) {
    case "SET_FIELD": {
      const next = { ...state, [action.field]: action.value };
      return next;
    }
    case "SET_CONNECTOR":
    case "SET_PAIR":
      return { ...state, entry_price: 0, anchored: false };
    case "ANCHOR": {
      if (state.anchored) return state;
      // Only fills an entry nobody has set. The flag flips either way — this
      // market has now had its chance, and the next tick must not try again.
      const entry = state.entry_price > 0
        ? state.entry_price
        : parseFloat(action.price.toPrecision(6));
      return { ...state, entry_price: entry, anchored: true };
    }
    default:
      return state;
  }
}

// ── Validation ──

export function usePositionValidation(state: PositionState): ExecutorValidation {
  return useMemo(() => {
    const errors: string[] = [];
    if (state.amount <= 0) errors.push("Amount required (base currency)");
    if (state.stop_loss === 0 && state.take_profit === 0 && state.time_limit === 0) {
      errors.push("Set at least one exit: SL, TP, or time limit");
    }
    if (state.stop_loss < 0) errors.push("Stop loss must be >= 0");
    if (state.take_profit < 0) errors.push("Take profit must be >= 0");
    if (state.stop_loss > 1) errors.push("Stop loss must be <= 100%");
    if (state.take_profit > 1) errors.push("Take profit must be <= 100%");
    return { valid: errors.length === 0, errors };
  }, [state]);
}

// ── Hook ──

export function usePositionConfig() {
  const [state, dispatch] = useReducer(positionReducer, undefined, loadSavedDefaults);
  const validation = usePositionValidation(state);

  const chartProps: ChartPriceMapping = useMemo(() => {
    const extras: ExtraLine[] = [];
    const entry = state.entry_price;

    // Both barriers hang off the entry, so a market entry (0) has nothing to
    // hang them from and draws neither — the same rule the payload follows.
    const tpPrice = barrierPrice(entry, state.take_profit, state.side, "tp");
    if (tpPrice > 0) {
      extras.push({
        price: tpPrice,
        label: barrierLabel("tp", state.take_profit),
        color: getThemeColors().green,
        lineStyle: "dashed",
        lineWidth: 2,
        // Grabbable: dragging it rewrites the percentage that put it there.
        slot: TAKE_PROFIT_SLOT,
      });
    }
    const slPrice = barrierPrice(entry, state.stop_loss, state.side, "sl");
    if (slPrice > 0) {
      extras.push({
        price: slPrice,
        label: barrierLabel("sl", state.stop_loss),
        color: getThemeColors().red,
        lineStyle: "dashed",
        lineWidth: 2,
        slot: STOP_LOSS_SLOT,
      });
    }

    return {
      startPrice: entry,
      endPrice: 0,
      limitPrice: 0,
      side: state.side,
      minSpread: 0,
      activePickField: state.activePickField === "entry_price" ? "start" : null,
      // The `start` slot carries the entry price here, so name it that rather
      // than letting the chart fall back to the grid's word for the slot.
      lineLabels: {
        start: "Entry",
        [TAKE_PROFIT_SLOT]: "Take profit",
        [STOP_LOSS_SLOT]: "Stop loss",
      },
      extraLines: extras,
    };
  }, [state.entry_price, state.side, state.activePickField, state.take_profit, state.stop_loss]);

  const buildPayload = (connector: string, pair: string, isSpot: boolean) => {
    const tripleBarrier: Record<string, unknown> = {
      open_order_type: state.open_order_type,
      take_profit_order_type: state.take_profit_order_type,
      stop_loss_order_type: state.stop_loss_order_type,
      time_limit_order_type: state.time_limit_order_type,
    };
    if (state.stop_loss > 0) tripleBarrier.stop_loss = state.stop_loss;
    if (state.take_profit > 0) tripleBarrier.take_profit = state.take_profit;
    if (state.time_limit > 0) tripleBarrier.time_limit = state.time_limit;
    if (state.trailing_stop_activation_price > 0 && state.trailing_stop_trailing_delta > 0) {
      tripleBarrier.trailing_stop = {
        activation_price: state.trailing_stop_activation_price,
        trailing_delta: state.trailing_stop_trailing_delta,
      };
    }

    const config: Record<string, unknown> = {
      connector_name: connector,
      trading_pair: pair,
      side: state.side,
      amount: state.amount,
      leverage: isSpot ? 1 : state.leverage,
      triple_barrier_config: tripleBarrier,
    };
    if (state.entry_price > 0) config.entry_price = state.entry_price;
    if (state.activation_bounds > 0) config.activation_bounds = state.activation_bounds;

    return { executor_type: "position_executor" as const, config };
  };

  const save = () => saveDefaults(state);

  const handleChartPriceSet = (field: PickSlot, price: number) => {
    if (field === "start") {
      dispatch({ type: "SET_FIELD", field: "entry_price", value: price });
    } else if (field === TAKE_PROFIT_SLOT) {
      // A barrier is stored as a percentage, so a dragged price only reaches
      // the form once turned back into the distance from the entry it implies.
      dispatch({
        type: "SET_FIELD",
        field: "take_profit",
        value: barrierPct(state.entry_price, price, state.side, "tp"),
      });
    } else if (field === STOP_LOSS_SLOT) {
      dispatch({
        type: "SET_FIELD",
        field: "stop_loss",
        value: barrierPct(state.entry_price, price, state.side, "sl"),
      });
    }
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return { state, dispatch, validation, chartProps, buildPayload, save, handleChartPriceSet };
}
