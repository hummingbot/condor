// ── DCA executor state machine ──
//
// See position-config.ts: state and reducer live beside the panel, not inside
// it, so this module can export what the page and the tests reach for.

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
import { DCA_DEFAULTS_KEY } from "@/lib/sessionState";

// ── State ──

export interface DCAState {
  side: 1 | 2;
  leverage: number;
  amounts_quote: number[];
  prices: number[];
  take_profit: number;
  stop_loss: number;
  time_limit: number;
  trailing_stop_activation_price: number;
  trailing_stop_trailing_delta: number;
  mode: string;
  activation_bounds: number;
  activePickField: string | null;
  showAdvanced: boolean;
  /** Whether this market's price has already laddered the levels; see PositionState. */
  anchored: boolean;
}

export type DCAAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_LEVEL_AMOUNT"; index: number; value: number }
  | { type: "SET_LEVEL_PRICE"; index: number; value: number }
  | { type: "ADD_LEVEL" }
  | { type: "REMOVE_LEVEL"; index: number }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string }
  | { type: "AUTO_FILL"; currentPrice: number }
  | { type: "ANCHOR"; price: number };

export const DCA_DEFAULTS: DCAState = {
  side: 1,
  leverage: 1,
  amounts_quote: [100, 100, 150],
  prices: [0, 0, 0],
  take_profit: 0.03,
  stop_loss: 0.05,
  time_limit: 0,
  trailing_stop_activation_price: 0,
  trailing_stop_trailing_delta: 0,
  mode: "MAKER",
  activation_bounds: 0,
  activePickField: null,
  showAdvanced: false,
  anchored: false,
};

const STORAGE_KEY = DCA_DEFAULTS_KEY;

const PERSISTED_FIELDS: (keyof DCAState)[] = [
  "side", "leverage", "amounts_quote", "take_profit", "stop_loss",
  "time_limit", "trailing_stop_activation_price", "trailing_stop_trailing_delta",
  "mode", "activation_bounds",
];

export function loadSavedDefaults(): DCAState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DCA_DEFAULTS;
    const saved = JSON.parse(raw);
    // `prices` is not persisted, so the shallow copy would hand back the
    // constant's own array — and the resize below would push/pop DCA_DEFAULTS
    // itself, leaving every later load with more prices than amounts.
    const merged = { ...DCA_DEFAULTS, prices: [...DCA_DEFAULTS.prices] };
    for (const key of PERSISTED_FIELDS) {
      if (key in saved && saved[key] !== undefined) {
        (merged as Record<string, unknown>)[key] = saved[key];
      }
    }
    // Ensure prices array matches amounts length
    while (merged.prices.length < merged.amounts_quote.length) merged.prices.push(0);
    while (merged.prices.length > merged.amounts_quote.length) merged.prices.pop();
    return merged;
  } catch {
    return DCA_DEFAULTS;
  }
}

function saveDefaults(state: DCAState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

/**
 * The price of the `i`th rung of a ladder anchored at `price`: 2% further from
 * it per level, downwards to buy and upwards to sell — cheaper on the way down,
 * dearer on the way up, which is the ordering the validation asks for.
 */
function ladderPrice(price: number, side: 1 | 2, i: number): number {
  const step = 0.02 * (i + 1);
  return parseFloat((price * (side === 1 ? 1 - step : 1 + step)).toPrecision(6));
}

/**
 * The slot a level's line carries — the same id its `PriceField` uses, so the
 * chart's write-back and the field's own arming are one channel, not two.
 */
export function levelSlot(index: number): string {
  return `dca_price_${index}`;
}

/** The level a slot names, or `null` when the slot is not a level's. */
export function levelIndex(slot: string): number | null {
  if (!slot.startsWith("dca_price_")) return null;
  const index = parseInt(slot.slice("dca_price_".length), 10);
  return Number.isInteger(index) && index >= 0 ? index : null;
}

/** Break-even of the levels that have both a price and an amount; 0 when none do. */
export function dcaBreakEven(prices: number[], amounts: number[]): number {
  let totalQuote = 0;
  let totalBase = 0;
  for (let i = 0; i < prices.length; i++) {
    const price = prices[i];
    const amount = amounts[i] ?? 0;
    if (price > 0 && amount > 0) {
      totalQuote += amount;
      totalBase += amount / price;
    }
  }
  return totalBase > 0 ? totalQuote / totalBase : 0;
}

export function dcaReducer(state: DCAState, action: DCAAction): DCAState {
  switch (action.type) {
    case "SET_FIELD": {
      // Intercept a level's field → update the prices array
      const level = levelIndex(action.field);
      if (level !== null && level < state.prices.length) {
        const prices = [...state.prices];
        prices[level] = action.value as number;
        return { ...state, prices };
      }
      return { ...state, [action.field]: action.value };
    }
    case "SET_LEVEL_AMOUNT": {
      const amounts = [...state.amounts_quote];
      amounts[action.index] = action.value;
      return { ...state, amounts_quote: amounts };
    }
    case "SET_LEVEL_PRICE": {
      const prices = [...state.prices];
      prices[action.index] = action.value;
      return { ...state, prices };
    }
    case "ADD_LEVEL": {
      const lastAmount = state.amounts_quote[state.amounts_quote.length - 1] ?? 100;
      // The new rung continues the ladder rather than starting at 0, so it
      // arrives on the chart as a line to drag instead of an empty field.
      const lastPrice = state.prices[state.prices.length - 1] ?? 0;
      const next = lastPrice > 0 ? ladderPrice(lastPrice, state.side, 0) : 0;
      return {
        ...state,
        amounts_quote: [...state.amounts_quote, lastAmount],
        prices: [...state.prices, next],
      };
    }
    case "REMOVE_LEVEL": {
      if (state.amounts_quote.length <= 1) return state;
      return {
        ...state,
        amounts_quote: state.amounts_quote.filter((_, i) => i !== action.index),
        prices: state.prices.filter((_, i) => i !== action.index),
      };
    }
    case "SET_CONNECTOR":
    case "SET_PAIR":
      return { ...state, prices: state.prices.map(() => 0), anchored: false };
    case "AUTO_FILL":
      return {
        ...state,
        prices: state.prices.map((_, i) => ladderPrice(action.currentPrice, state.side, i)),
      };
    case "ANCHOR": {
      if (state.anchored) return state;
      // Only ladders a set nobody has touched: one edited price means the user
      // is placing them, and the rest are theirs to place too.
      const untouched = state.prices.every((p) => p <= 0);
      return {
        ...state,
        anchored: true,
        prices: untouched
          ? state.prices.map((_, i) => ladderPrice(action.price, state.side, i))
          : state.prices,
      };
    }
    default:
      return state;
  }
}

// ── Validation ──

export function useDCAValidation(state: DCAState): ExecutorValidation {
  return useMemo(() => {
    const errors: string[] = [];
    if (state.amounts_quote.length === 0) errors.push("At least one DCA level required");
    if (state.amounts_quote.some((a) => a <= 0)) errors.push("All amounts must be > 0");
    if (state.prices.some((p) => p <= 0)) errors.push("All prices must be set");
    if (state.amounts_quote.length !== state.prices.length) errors.push("Amounts and prices must have same length");
    if (state.take_profit === 0 && state.stop_loss === 0 && state.time_limit === 0) {
      errors.push("Set at least one exit: TP, SL, or time limit");
    }
    // Check price ordering
    if (state.prices.every((p) => p > 0)) {
      if (state.side === 1) {
        // BUY: prices should be decreasing
        for (let i = 1; i < state.prices.length; i++) {
          if (state.prices[i] >= state.prices[i - 1]) {
            errors.push("BUY: prices should be decreasing (lower levels buy cheaper)");
            break;
          }
        }
      } else {
        // SELL: prices should be increasing
        for (let i = 1; i < state.prices.length; i++) {
          if (state.prices[i] <= state.prices[i - 1]) {
            errors.push("SELL: prices should be increasing (higher levels sell dearer)");
            break;
          }
        }
      }
    }
    return { valid: errors.length === 0, errors };
  }, [state]);
}

// ── Hook ──

export function useDCAConfig() {
  const [state, dispatch] = useReducer(dcaReducer, undefined, loadSavedDefaults);
  const validation = useDCAValidation(state);

  const chartProps: ChartPriceMapping = useMemo(() => {
    const extras: ExtraLine[] = [];
    const lineLabels: Record<string, string> = {};

    // One grabbable line per level, named for the field behind it — the same id
    // the panel's own PriceField arms, so a click on the chart and a drag on the
    // line land in the same place without a translation table in between.
    state.prices.forEach((price, i) => {
      lineLabels[levelSlot(i)] = `Level ${i + 1}`;
      if (price <= 0) return;
      extras.push({
        price,
        label: `L${i + 1}`,
        color: "#3b82f6",
        lineStyle: "dotted",
        lineWidth: 1,
        slot: levelSlot(i),
      });
    });

    const bep = dcaBreakEven(state.prices, state.amounts_quote);
    if (bep > 0) {
      // Where the ladder averages out. Not grabbable: it is the levels' own
      // consequence, and moving it would not say which level should have moved.
      extras.push({
        price: bep,
        label: "BEP",
        color: "#f59e0b",
        lineStyle: "solid",
        lineWidth: 2,
      });

      // Both barriers are measured from the break-even, not from any one level.
      const tpPrice = barrierPrice(bep, state.take_profit, state.side, "tp");
      if (tpPrice > 0) {
        extras.push({
          price: tpPrice,
          label: barrierLabel("tp", state.take_profit),
          color: getThemeColors().green,
          lineStyle: "dashed",
          lineWidth: 2,
          slot: TAKE_PROFIT_SLOT,
        });
      }
      const slPrice = barrierPrice(bep, state.stop_loss, state.side, "sl");
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
    }

    lineLabels[TAKE_PROFIT_SLOT] = "Take profit";
    lineLabels[STOP_LOSS_SLOT] = "Stop loss";

    return {
      // Every DCA line is one the panel draws itself: there is no single range
      // for the chart's own three to stand for.
      startPrice: 0,
      endPrice: 0,
      limitPrice: 0,
      side: state.side,
      minSpread: 0,
      activePickField: state.activePickField,
      lineLabels,
      extraLines: extras,
    };
  }, [state.prices, state.amounts_quote, state.side, state.take_profit, state.stop_loss, state.activePickField]);

  const buildPayload = (connector: string, pair: string, isSpot: boolean) => {
    const config: Record<string, unknown> = {
      connector_name: connector,
      trading_pair: pair,
      side: state.side,
      leverage: isSpot ? 1 : state.leverage,
      amounts_quote: state.amounts_quote,
      prices: state.prices,
      mode: state.mode,
    };
    if (state.take_profit > 0) config.take_profit = state.take_profit;
    if (state.stop_loss > 0) config.stop_loss = state.stop_loss;
    if (state.time_limit > 0) config.time_limit = state.time_limit;
    if (state.trailing_stop_activation_price > 0 && state.trailing_stop_trailing_delta > 0) {
      config.trailing_stop = {
        activation_price: state.trailing_stop_activation_price,
        trailing_delta: state.trailing_stop_trailing_delta,
      };
    }
    if (state.activation_bounds > 0) config.activation_bounds = state.activation_bounds;

    return { executor_type: "dca_executor" as const, config };
  };

  const save = () => saveDefaults(state);

  const handleChartPriceSet = (field: PickSlot, price: number) => {
    const level = levelIndex(field);
    if (level !== null) {
      // Already rounded to the venue's tick by the chart, which knows the
      // precision; rounding again here would only coarsen it.
      dispatch({ type: "SET_LEVEL_PRICE", index: level, value: price });
    } else if (field === TAKE_PROFIT_SLOT || field === STOP_LOSS_SLOT) {
      // Both barriers are percentages off the break-even, so a dragged price
      // becomes the distance from it — and the break-even the drag is measured
      // against is the one the line was drawn from.
      const bep = dcaBreakEven(state.prices, state.amounts_quote);
      const kind = field === TAKE_PROFIT_SLOT ? "tp" : "sl";
      dispatch({
        type: "SET_FIELD",
        field: kind === "tp" ? "take_profit" : "stop_loss",
        value: barrierPct(bep, price, state.side, kind),
      });
    }
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return { state, dispatch, validation, chartProps, buildPayload, save, handleChartPriceSet };
}
