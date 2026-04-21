import { useMemo, useReducer } from "react";
import { Minus, Plus, Sparkles } from "lucide-react";

import {
  AdvancedSection,
  LeverageField,
  NumberField,
  PriceField,
  SectionHeader,
  SelectField,
  SideSelector,
  ValidationMessages,
  type FieldDispatch,
} from "./fields";
import type { ChartPriceMapping, ExecutorValidation, ExtraLine } from "./types";

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
}

type DCAAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_LEVEL_AMOUNT"; index: number; value: number }
  | { type: "SET_LEVEL_PRICE"; index: number; value: number }
  | { type: "ADD_LEVEL" }
  | { type: "REMOVE_LEVEL"; index: number }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string }
  | { type: "AUTO_FILL"; currentPrice: number };

const DEFAULTS: DCAState = {
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
};

const STORAGE_KEY = "condor_dca_defaults";

const PERSISTED_FIELDS: (keyof DCAState)[] = [
  "side", "leverage", "amounts_quote", "take_profit", "stop_loss",
  "time_limit", "trailing_stop_activation_price", "trailing_stop_trailing_delta",
  "mode", "activation_bounds",
];

function loadSavedDefaults(): DCAState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const saved = JSON.parse(raw);
    const merged = { ...DEFAULTS };
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
    return DEFAULTS;
  }
}

function saveDefaults(state: DCAState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

function dcaReducer(state: DCAState, action: DCAAction): DCAState {
  switch (action.type) {
    case "SET_FIELD": {
      // Intercept dca_price_N fields → update prices array
      if (typeof action.field === "string" && action.field.startsWith("dca_price_")) {
        const idx = parseInt(action.field.replace("dca_price_", ""), 10);
        if (!isNaN(idx) && idx >= 0 && idx < state.prices.length) {
          const prices = [...state.prices];
          prices[idx] = action.value as number;
          return { ...state, prices };
        }
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
      return {
        ...state,
        amounts_quote: [...state.amounts_quote, lastAmount],
        prices: [...state.prices, 0],
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
      return { ...state, prices: state.prices.map(() => 0) };
    case "AUTO_FILL": {
      const p = action.currentPrice;
      const prices = state.prices.map((_, i) => {
        if (state.side === 1) {
          // BUY: decreasing prices from current
          return parseFloat((p * (1 - 0.02 * (i + 1))).toPrecision(6));
        } else {
          // SELL: increasing prices from current
          return parseFloat((p * (1 + 0.02 * (i + 1))).toPrecision(6));
        }
      });
      return { ...state, prices };
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
    const isLong = state.side === 1;

    // Level lines
    state.prices.forEach((p, i) => {
      if (p > 0) {
        extras.push({
          price: p,
          label: `L${i + 1}`,
          color: "#3b82f6",
          lineStyle: "dotted",
          lineWidth: 1,
        });
      }
    });

    // BEP calculation
    let bep = 0;
    let totalQuote = 0;
    let totalBase = 0;
    for (let i = 0; i < state.prices.length; i++) {
      const p = state.prices[i];
      const a = state.amounts_quote[i] ?? 0;
      if (p > 0 && a > 0) {
        totalQuote += a;
        totalBase += a / p;
      }
    }
    if (totalBase > 0) {
      bep = totalQuote / totalBase;
      extras.push({
        price: bep,
        label: "BEP",
        color: "#f59e0b",
        lineStyle: "solid",
        lineWidth: 2,
      });

      // TP from BEP
      if (state.take_profit > 0) {
        const tpPrice = isLong ? bep * (1 + state.take_profit) : bep * (1 - state.take_profit);
        extras.push({
          price: tpPrice,
          label: `TP (${(state.take_profit * 100).toFixed(1)}%)`,
          color: "#22c55e",
          lineStyle: "dashed",
          lineWidth: 2,
        });
      }

      // SL from BEP
      if (state.stop_loss > 0) {
        const slPrice = isLong ? bep * (1 - state.stop_loss) : bep * (1 + state.stop_loss);
        extras.push({
          price: slPrice,
          label: `SL (${(state.stop_loss * 100).toFixed(1)}%)`,
          color: "#ef4444",
          lineStyle: "dashed",
          lineWidth: 2,
        });
      }
    }

    return {
      startPrice: 0,
      endPrice: 0,
      limitPrice: 0,
      side: state.side,
      minSpread: 0,
      activePickField: state.activePickField ? "start" as const : null,
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

  const handleChartPriceSet = (_field: "start" | "end" | "limit", price: number) => {
    const pick = state.activePickField;
    if (pick && pick.startsWith("dca_price_")) {
      const idx = parseInt(pick.replace("dca_price_", ""), 10);
      if (!isNaN(idx)) {
        dispatch({ type: "SET_LEVEL_PRICE", index: idx, value: parseFloat(price.toPrecision(6)) });
      }
    }
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return { state, dispatch, validation, chartProps, buildPayload, save, handleChartPriceSet };
}

// ── Panel Component ──

const MODE_OPTIONS = [
  { value: "MAKER", label: "Maker (Limit)" },
  { value: "TAKER", label: "Taker (Market)" },
];

interface Props {
  state: DCAState;
  dispatch: React.Dispatch<DCAAction>;
  currentPrice: number | null;
  isSpot?: boolean;
  pair?: string;
}

export function DCAConfigPanel({ state, dispatch, currentPrice, isSpot = false, pair: _pair }: Props) {
  const validation = useDCAValidation(state);
  const d = dispatch as unknown as FieldDispatch;
  const totalQuote = state.amounts_quote.reduce((s, a) => s + a, 0);

  // BEP for display
  const bep = useMemo(() => {
    let tq = 0, tb = 0;
    for (let i = 0; i < state.prices.length; i++) {
      const p = state.prices[i];
      const a = state.amounts_quote[i] ?? 0;
      if (p > 0 && a > 0) { tq += a; tb += a / p; }
    }
    return tb > 0 ? tq / tb : 0;
  }, [state.prices, state.amounts_quote]);

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* Direction */}
      <SideSelector side={state.side} dispatch={d} />

      {/* DCA Levels */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <SectionHeader>DCA Levels</SectionHeader>
          <div className="flex items-center gap-1">
            {currentPrice && currentPrice > 0 && (
              <button
                onClick={() => dispatch({ type: "AUTO_FILL", currentPrice })}
                className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
              >
                <Sparkles className="h-3 w-3" />
                Auto-fill
              </button>
            )}
            <button
              onClick={() => dispatch({ type: "ADD_LEVEL" })}
              className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <Plus className="h-3 w-3" />
              Add
            </button>
          </div>
        </div>

        {state.amounts_quote.map((amount, i) => (
          <div key={i} className="flex items-end gap-1.5">
            <div className="flex-1">
              <label className="mb-1 block text-[10px] text-[var(--color-text-muted)]">
                Level {i + 1} Amount
              </label>
              <input
                type="number"
                step="10"
                min="0"
                value={amount || ""}
                onChange={(e) => dispatch({ type: "SET_LEVEL_AMOUNT", index: i, value: parseFloat(e.target.value) || 0 })}
                placeholder="100"
                className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:border-[var(--color-primary)] focus:outline-none"
              />
            </div>
            <div className="flex-1">
              <PriceField
                label="Price"
                value={state.prices[i]}
                field={`dca_price_${i}`}
                activePickField={state.activePickField}
                dispatch={d}
                valid={state.prices[i] > 0}
              />
            </div>
            {state.amounts_quote.length > 1 && (
              <button
                onClick={() => dispatch({ type: "REMOVE_LEVEL", index: i })}
                className="mb-0.5 rounded border border-[var(--color-border)] p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
              >
                <Minus className="h-3 w-3" />
              </button>
            )}
          </div>
        ))}

        <p className="rounded bg-[var(--color-bg)] px-2.5 py-1.5 text-[10px] text-[var(--color-text-muted)]">
          {state.amounts_quote.length} levels &middot; Total: ${totalQuote.toFixed(2)}
          {bep > 0 && <> &middot; BEP: <span className="font-mono text-amber-400">{bep.toPrecision(6)}</span></>}
        </p>
        <LeverageField value={state.leverage} field="leverage" dispatch={d} isSpot={isSpot} />
      </div>

      {/* Exit Strategy */}
      <div className="space-y-2.5">
        <SectionHeader>Exit Strategy</SectionHeader>
        <NumberField label="Take Profit" value={state.take_profit} field="take_profit" dispatch={d} step={0.01} isPercent suffix="%" />
        <NumberField label="Stop Loss" value={state.stop_loss} field="stop_loss" dispatch={d} step={0.01} isPercent suffix="%" />
        <NumberField label="Time Limit (0 = disabled)" value={state.time_limit} field="time_limit" dispatch={d} step={60} min={0} suffix="sec" />
      </div>

      {/* Mode & Advanced */}
      <AdvancedSection
        open={state.showAdvanced}
        onToggle={() => d({ type: "SET_FIELD", field: "showAdvanced", value: !state.showAdvanced })}
      >
        <SelectField label="Mode" value={state.mode} field="mode" dispatch={d} options={MODE_OPTIONS} />
        <NumberField label="Activation Bounds (0 = disabled)" value={state.activation_bounds} field="activation_bounds" dispatch={d} step={0.01} isPercent suffix="%" />
        <div className="space-y-2.5">
          <SectionHeader>Trailing Stop</SectionHeader>
          <NumberField label="Activation Price" value={state.trailing_stop_activation_price} field="trailing_stop_activation_price" dispatch={d} step={0.01} isPercent suffix="%" />
          <NumberField label="Trailing Delta" value={state.trailing_stop_trailing_delta} field="trailing_stop_trailing_delta" dispatch={d} step={0.01} isPercent suffix="%" />
        </div>
      </AdvancedSection>

      <ValidationMessages errors={validation.errors} />
    </div>
  );
}
