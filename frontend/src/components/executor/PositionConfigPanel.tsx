import { useEffect, useMemo, useReducer } from "react";
import { Sparkles } from "lucide-react";

import {
  AdvancedSection,
  NumberField,
  PriceField,
  SectionHeader,
  SelectField,
  SideSelector,
  ValidationMessages,
  ORDER_TYPE_OPTIONS,
  type FieldDispatch,
} from "./fields";
import type { ChartPriceMapping, ExecutorValidation, ExtraLine } from "./types";

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
}

type PositionAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string };

const DEFAULTS: PositionState = {
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
};

const STORAGE_KEY = "condor_position_defaults";

const PERSISTED_FIELDS: (keyof PositionState)[] = [
  "side", "amount", "leverage", "stop_loss", "take_profit",
  "time_limit", "trailing_stop_activation_price", "trailing_stop_trailing_delta",
  "open_order_type", "take_profit_order_type", "stop_loss_order_type",
  "time_limit_order_type", "activation_bounds",
];

function loadSavedDefaults(): PositionState {
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
    return merged;
  } catch {
    return DEFAULTS;
  }
}

function saveDefaults(state: PositionState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

function positionReducer(state: PositionState, action: PositionAction): PositionState {
  switch (action.type) {
    case "SET_FIELD": {
      const next = { ...state, [action.field]: action.value };
      return next;
    }
    case "SET_CONNECTOR":
      return { ...state, entry_price: 0 };
    case "SET_PAIR":
      return { ...state, entry_price: 0 };
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
    const isLong = state.side === 1;

    if (entry > 0 && state.take_profit > 0) {
      const tpPrice = isLong ? entry * (1 + state.take_profit) : entry * (1 - state.take_profit);
      extras.push({
        price: tpPrice,
        label: `TP (${(state.take_profit * 100).toFixed(1)}%)`,
        color: "#22c55e",
        lineStyle: "dashed",
        lineWidth: 2,
      });
    }
    if (entry > 0 && state.stop_loss > 0) {
      const slPrice = isLong ? entry * (1 - state.stop_loss) : entry * (1 + state.stop_loss);
      extras.push({
        price: slPrice,
        label: `SL (${(state.stop_loss * 100).toFixed(1)}%)`,
        color: "#ef4444",
        lineStyle: "dashed",
        lineWidth: 2,
      });
    }

    return {
      startPrice: entry,
      endPrice: 0,
      limitPrice: 0,
      side: state.side,
      minSpread: 0,
      activePickField: state.activePickField === "entry_price" ? "start" : null,
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

  const handleChartPriceSet = (field: "start" | "end" | "limit", price: number) => {
    if (field === "start") {
      dispatch({ type: "SET_FIELD", field: "entry_price", value: price });
    }
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return { state, dispatch, validation, chartProps, buildPayload, save, handleChartPriceSet };
}

// ── Panel Component ──

interface Props {
  state: PositionState;
  dispatch: React.Dispatch<PositionAction>;
  currentPrice: number | null;
  isSpot?: boolean;
}

export function PositionConfigPanel({ state, dispatch, currentPrice, isSpot = false }: Props) {
  const validation = usePositionValidation(state);

  // Auto-fill entry price from current price on first load (if zero)
  useEffect(() => {
    if (state.entry_price === 0 && currentPrice && currentPrice > 0) {
      // Don't auto-set — let user pick or leave as market order
    }
  }, [currentPrice, state.entry_price]);

  const d = dispatch as FieldDispatch;

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* Direction */}
      <SideSelector side={state.side} dispatch={d} />

      {/* Entry */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <SectionHeader>Entry</SectionHeader>
          {currentPrice && currentPrice > 0 && (
            <button
              onClick={() => {
                d({ type: "SET_FIELD", field: "entry_price", value: 0 });
                d({ type: "SET_FIELD", field: "stop_loss", value: 0.03 });
                d({ type: "SET_FIELD", field: "take_profit", value: 0.02 });
                d({ type: "SET_FIELD", field: "open_order_type", value: 1 });
              }}
              className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <Sparkles className="h-3 w-3" />
              Auto-fill
            </button>
          )}
        </div>
        <PriceField
          label="Entry Price (0 = market order)"
          value={state.entry_price}
          field="entry_price"
          activePickField={state.activePickField}
          dispatch={d}
          valid={true}
          hint="Leave at 0 for market entry"
        />
        <NumberField
          label="Amount (base currency)"
          value={state.amount}
          field="amount"
          dispatch={d}
          step={0.001}
          min={0}
        />
      </div>

      {/* Triple Barrier */}
      <div className="space-y-2.5">
        <SectionHeader>Exit Strategy</SectionHeader>
        <NumberField
          label="Stop Loss"
          value={state.stop_loss}
          field="stop_loss"
          dispatch={d}
          step={0.01}
          isPercent
          suffix="%"
        />
        <NumberField
          label="Take Profit"
          value={state.take_profit}
          field="take_profit"
          dispatch={d}
          step={0.01}
          isPercent
          suffix="%"
        />
        <NumberField
          label="Time Limit (0 = disabled)"
          value={state.time_limit}
          field="time_limit"
          dispatch={d}
          step={60}
          min={0}
          suffix="sec"
        />
      </div>

      {/* Trailing Stop */}
      <div className="space-y-2.5">
        <SectionHeader>Trailing Stop</SectionHeader>
        <NumberField
          label="Activation Price"
          value={state.trailing_stop_activation_price}
          field="trailing_stop_activation_price"
          dispatch={d}
          step={0.01}
          isPercent
          suffix="%"
        />
        <NumberField
          label="Trailing Delta"
          value={state.trailing_stop_trailing_delta}
          field="trailing_stop_trailing_delta"
          dispatch={d}
          step={0.01}
          isPercent
          suffix="%"
        />
        <p className="text-[10px] text-[var(--color-text-muted)]">Set both to enable trailing stop. 0 = disabled.</p>
      </div>

      {/* Advanced */}
      <AdvancedSection
        open={state.showAdvanced}
        onToggle={() => d({ type: "SET_FIELD", field: "showAdvanced", value: !state.showAdvanced })}
      >
        {isSpot ? (
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Leverage</label>
            <div className="flex items-center gap-1">
              <input type="text" value="1" disabled className="flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text-muted)] opacity-60" />
              <span className="text-[10px] text-[var(--color-text-muted)]">x (spot)</span>
            </div>
          </div>
        ) : (
          <NumberField label="Leverage" value={state.leverage} field="leverage" dispatch={d} step={1} min={1} suffix="x" />
        )}
        <NumberField label="Activation Bounds (0 = disabled)" value={state.activation_bounds} field="activation_bounds" dispatch={d} step={0.01} isPercent suffix="%" />
        <SelectField label="Open Order Type" value={state.open_order_type} field="open_order_type" dispatch={d} options={ORDER_TYPE_OPTIONS} />
        <SelectField label="Take Profit Order Type" value={state.take_profit_order_type} field="take_profit_order_type" dispatch={d} options={ORDER_TYPE_OPTIONS} />
        <SelectField label="Stop Loss Order Type" value={state.stop_loss_order_type} field="stop_loss_order_type" dispatch={d} options={ORDER_TYPE_OPTIONS} />
        <SelectField label="Time Limit Order Type" value={state.time_limit_order_type} field="time_limit_order_type" dispatch={d} options={ORDER_TYPE_OPTIONS} />
      </AdvancedSection>

      <ValidationMessages errors={validation.errors} />
    </div>
  );
}
