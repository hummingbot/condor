import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Crosshair,
  Sparkles,
} from "lucide-react";

import { SelectField, ORDER_TYPE_OPTIONS, type FieldDispatch } from "@/components/executor/fields";
import type { GridState, GridAction } from "@/pages/CreateGridExecutor";

interface GridConfigPanelProps {
  state: GridState;
  dispatch: React.Dispatch<GridAction>;
  currentPrice: number | null;
  isSpot?: boolean;
}

function PriceField({
  label,
  value,
  field,
  activePickField,
  dispatch,
  valid,
  hint,
}: {
  label: string;
  value: number;
  field: "start" | "end" | "limit";
  activePickField: "start" | "end" | "limit" | null;
  dispatch: React.Dispatch<GridAction>;
  valid: boolean;
  hint?: string;
}) {
  const isActive = activePickField === field;
  const inputRef = useRef<HTMLInputElement>(null);
  const [localValue, setLocalValue] = useState(value === 0 ? "" : String(value));

  // Sync from parent when value changes externally (e.g. auto-fill, chart pick)
  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setLocalValue(value === 0 ? "" : String(value));
    }
  }, [value]);

  return (
    <div>
      <label className="mb-1 flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
        {label}
        {value > 0 && (
          valid
            ? <Check className="h-3 w-3 text-[var(--color-green)]" />
            : <AlertTriangle className="h-3 w-3 text-[var(--color-red)]" />
        )}
      </label>
      <div className="flex gap-1">
        <input
          ref={inputRef}
          type="number"
          step="any"
          value={localValue}
          onChange={(e) => {
            setLocalValue(e.target.value);
            const num = parseFloat(e.target.value);
            dispatch({ type: "SET_FIELD", field: `${field}_price`, value: isNaN(num) ? 0 : num });
          }}
          onBlur={() => setLocalValue(value === 0 ? "" : String(value))}
          placeholder="0.00"
          className={`flex-1 rounded border bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:outline-none ${
            isActive
              ? "border-[var(--color-primary)] ring-1 ring-[var(--color-primary)]"
              : "border-[var(--color-border)] focus:border-[var(--color-primary)]"
          }`}
        />
        <button
          onClick={() =>
            dispatch({
              type: "SET_FIELD",
              field: "activePickField",
              value: isActive ? null : field,
            })
          }
          className={`flex items-center rounded border px-2 transition-colors ${
            isActive
              ? "border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]"
              : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          }`}
          title="Pick from chart"
        >
          <Crosshair className="h-3.5 w-3.5" />
        </button>
      </div>
      {hint && <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}

function NumberField({
  label,
  value,
  field,
  dispatch,
  step = 1,
  min,
  suffix,
  isPercent = false,
}: {
  label: string;
  value: number;
  field: string;
  dispatch: React.Dispatch<GridAction>;
  step?: number;
  min?: number;
  suffix?: string;
  isPercent?: boolean;
}) {
  const displayValue = isPercent ? value * 100 : value;
  const inputRef = useRef<HTMLInputElement>(null);
  const [localValue, setLocalValue] = useState(displayValue === 0 ? "" : String(displayValue));

  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setLocalValue(displayValue === 0 ? "" : String(displayValue));
    }
  }, [displayValue]);

  return (
    <div>
      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">{label}</label>
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          type="number"
          step={isPercent ? step * 100 : step}
          min={min !== undefined ? (isPercent ? min * 100 : min) : undefined}
          value={localValue}
          onChange={(e) => {
            setLocalValue(e.target.value);
            const raw = parseFloat(e.target.value);
            dispatch({ type: "SET_FIELD", field, value: isPercent ? (isNaN(raw) ? 0 : raw / 100) : (isNaN(raw) ? 0 : raw) });
          }}
          onBlur={() => setLocalValue(displayValue === 0 ? "" : String(displayValue))}
          placeholder="0"
          className="flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:border-[var(--color-primary)] focus:outline-none"
        />
        {suffix && (
          <span className="text-[10px] text-[var(--color-text-muted)]">{suffix}</span>
        )}
      </div>
    </div>
  );
}


function ToggleField({
  label,
  value,
  field,
  dispatch,
}: {
  label: string;
  value: boolean;
  field: string;
  dispatch: React.Dispatch<GridAction>;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => dispatch({ type: "SET_FIELD", field, value: !value })}
        className={`relative h-5 w-9 rounded-full transition-colors ${
          value ? "bg-[var(--color-primary)]" : "bg-[var(--color-border)]"
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
            value ? "left-[18px]" : "left-0.5"
          }`}
        />
      </button>
      <span className="text-xs text-[var(--color-text)]">{label}</span>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
      {children}
    </span>
  );
}

export function GridConfigPanel({ state, dispatch, currentPrice, isSpot = false }: GridConfigPanelProps) {
  const validation = useMemo(() => {
    const errors: string[] = [];
    const warnings: string[] = [];

    if (state.start_price > 0 && state.end_price > 0) {
      if (state.start_price >= state.end_price) {
        errors.push("Start price must be < end price");
      }
    }

    if (state.side === 1 && state.limit_price > 0 && state.start_price > 0) {
      if (state.limit_price >= state.start_price) {
        errors.push("LONG: limit must be < start price");
      }
    }

    if (state.side === 2 && state.limit_price > 0 && state.end_price > 0) {
      if (state.limit_price <= state.end_price) {
        errors.push("SHORT: limit must be > end price");
      }
    }

    if (state.start_price <= 0 || state.end_price <= 0 || state.limit_price <= 0) {
      errors.push("All prices required");
    }

    if (state.total_amount_quote <= 0) {
      errors.push("Total amount required");
    }

    if (state.total_amount_quote > 0 && state.min_order_amount_quote > 0) {
      if (state.total_amount_quote < state.min_order_amount_quote) {
        errors.push("Total must be >= min order amount");
      }
    }

    // Compute estimated levels
    let levels = 0;
    if (state.start_price > 0 && state.end_price > 0 && state.min_spread_between_orders > 0) {
      const range = state.end_price - state.start_price;
      const stepSize = state.start_price * state.min_spread_between_orders;
      if (stepSize > 0) {
        levels = Math.floor(range / stepSize);
      }
    }

    if (levels > 0 && levels < 3) {
      warnings.push("Fewer than 3 grid levels");
    }

    return { errors, warnings, levels, valid: errors.length === 0 };
  }, [state]);

  const handleAutoFill = () => {
    if (!currentPrice || currentPrice <= 0) return;
    const p = currentPrice;
    if (state.side === 1) {
      const start = p * 0.99;
      const end = p * 1.03;
      const limit = start * 0.995;
      dispatch({ type: "SET_FIELD", field: "start_price", value: parseFloat(start.toPrecision(6)) });
      dispatch({ type: "SET_FIELD", field: "end_price", value: parseFloat(end.toPrecision(6)) });
      dispatch({ type: "SET_FIELD", field: "limit_price", value: parseFloat(limit.toPrecision(6)) });
    } else {
      const start = p * 0.97;
      const end = p * 1.01;
      const limit = end * 1.005;
      dispatch({ type: "SET_FIELD", field: "start_price", value: parseFloat(start.toPrecision(6)) });
      dispatch({ type: "SET_FIELD", field: "end_price", value: parseFloat(end.toPrecision(6)) });
      dispatch({ type: "SET_FIELD", field: "limit_price", value: parseFloat(limit.toPrecision(6)) });
    }
    dispatch({ type: "SET_FIELD", field: "open_order_type", value: 1 });
  };

  const perLevel = validation.levels > 0 ? state.total_amount_quote / validation.levels : 0;

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* ── Direction ── */}
      <div>
        <SectionHeader>Direction</SectionHeader>
        <div className="mt-1.5 flex gap-1">
          <button
            onClick={() => dispatch({ type: "SET_FIELD", field: "side", value: 1 })}
            className={`flex-1 rounded py-2 text-xs font-bold transition-colors ${
              state.side === 1
                ? "bg-[var(--color-green)] text-white"
                : "bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            }`}
          >
            LONG
          </button>
          <button
            onClick={() => dispatch({ type: "SET_FIELD", field: "side", value: 2 })}
            className={`flex-1 rounded py-2 text-xs font-bold transition-colors ${
              state.side === 2
                ? "bg-[var(--color-red)] text-white"
                : "bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            }`}
          >
            SHORT
          </button>
        </div>
      </div>

      {/* ── Prices ── */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <SectionHeader>Prices</SectionHeader>
          <button
            onClick={handleAutoFill}
            disabled={!currentPrice}
            className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-40"
            title="Auto-fill from current price"
          >
            <Sparkles className="h-3 w-3" />
            Auto-fill
          </button>
        </div>

        <PriceField
          label="Start Price (lower boundary)"
          value={state.start_price}
          field="start"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={state.start_price > 0 && state.start_price < state.end_price}
        />
        <PriceField
          label="End Price (upper boundary)"
          value={state.end_price}
          field="end"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={state.end_price > 0 && state.end_price > state.start_price}
        />
        <PriceField
          label={`Limit Price (${state.side === 1 ? "stop-loss below" : "stop-loss above"})`}
          value={state.limit_price}
          field="limit"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={
            state.limit_price > 0 &&
            (state.side === 1
              ? state.limit_price < state.start_price
              : state.limit_price > state.end_price)
          }
          hint={state.side === 1 ? "Must be below start price" : "Must be above end price"}
        />
      </div>

      {/* ── Grid Structure ── */}
      <div className="space-y-2.5">
        <SectionHeader>Grid Structure</SectionHeader>
        <NumberField
          label="Total Amount (quote)"
          value={state.total_amount_quote}
          field="total_amount_quote"
          dispatch={dispatch}
          step={10}
          min={0}
          suffix="USDT"
        />
        <NumberField
          label="Min Order Amount"
          value={state.min_order_amount_quote}
          field="min_order_amount_quote"
          dispatch={dispatch}
          step={1}
          min={0}
          suffix="USDT"
        />
        <NumberField
          label="Min Spread Between Orders"
          value={state.min_spread_between_orders}
          field="min_spread_between_orders"
          dispatch={dispatch}
          step={0.01}
          isPercent
          suffix="%"
        />
        {validation.levels > 0 && (
          <p className="rounded bg-[var(--color-bg)] px-2.5 py-1.5 text-[10px] text-[var(--color-text-muted)]">
            ~{validation.levels} levels &middot; ~${perLevel.toFixed(2)} per level
          </p>
        )}
      </div>

      {/* ── Take Profit ── */}
      <div className="space-y-2.5">
        <SectionHeader>Take Profit</SectionHeader>
        <NumberField
          label="Take Profit"
          value={state.take_profit}
          field="take_profit"
          dispatch={dispatch}
          step={0.01}
          isPercent
          suffix="%"
        />
        <ToggleField
          label="Keep Position"
          value={state.keep_position}
          field="keep_position"
          dispatch={dispatch}
        />
        <ToggleField
          label="Coerce TP to Step"
          value={state.coerce_tp_to_step}
          field="coerce_tp_to_step"
          dispatch={dispatch}
        />
      </div>

      {/* ── Advanced (collapsible) ── */}
      <div>
        <button
          onClick={() => dispatch({ type: "SET_FIELD", field: "showAdvanced", value: !state.showAdvanced })}
          className="flex w-full items-center justify-between rounded px-1 py-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Advanced
          {state.showAdvanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>

        {state.showAdvanced && (
          <div className="mt-2 space-y-2.5">
            {isSpot ? (
              <div>
                <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Leverage</label>
                <div className="flex items-center gap-1">
                  <input
                    type="text"
                    value="1"
                    disabled
                    className="flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text-muted)] opacity-60"
                  />
                  <span className="text-[10px] text-[var(--color-text-muted)]">x (spot)</span>
                </div>
              </div>
            ) : (
              <NumberField
                label="Leverage"
                value={state.leverage}
                field="leverage"
                dispatch={dispatch}
                step={1}
                min={1}
                suffix="x"
              />
            )}
            <div className="grid grid-cols-2 gap-2">
              <NumberField
                label="Max Open Orders"
                value={state.max_open_orders}
                field="max_open_orders"
                dispatch={dispatch}
                step={1}
                min={1}
              />
              <NumberField
                label="Max Orders/Batch"
                value={state.max_orders_per_batch}
                field="max_orders_per_batch"
                dispatch={dispatch}
                step={1}
                min={1}
              />
            </div>
            <NumberField
              label="Order Frequency"
              value={state.order_frequency}
              field="order_frequency"
              dispatch={dispatch}
              step={1}
              min={1}
              suffix="s"
            />
            <NumberField
              label="Activation Bounds"
              value={state.activation_bounds}
              field="activation_bounds"
              dispatch={dispatch}
              step={0.01}
              isPercent
              suffix="%"
            />
            <SelectField
              label="Open Order Type"
              value={state.open_order_type}
              field="open_order_type"
              dispatch={dispatch as unknown as FieldDispatch}
              options={ORDER_TYPE_OPTIONS}
            />
            <SelectField
              label="Take Profit Order Type"
              value={state.take_profit_order_type}
              field="take_profit_order_type"
              dispatch={dispatch as unknown as FieldDispatch}
              options={ORDER_TYPE_OPTIONS}
            />
          </div>
        )}
      </div>

      {/* ── Validation ── */}
      {(validation.errors.length > 0 || validation.warnings.length > 0) && (
        <div className="space-y-1">
          {validation.errors.map((err, i) => (
            <p key={i} className="flex items-center gap-1 text-[10px] text-[var(--color-red)]">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              {err}
            </p>
          ))}
          {validation.warnings.map((warn, i) => (
            <p key={i} className="flex items-center gap-1 text-[10px] text-amber-400">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              {warn}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export function useGridValidation(state: GridState) {
  return useMemo(() => {
    const errors: string[] = [];

    if (state.start_price <= 0 || state.end_price <= 0 || state.limit_price <= 0) {
      errors.push("All prices required");
    }
    if (state.start_price > 0 && state.end_price > 0 && state.start_price >= state.end_price) {
      errors.push("Start must be < end");
    }
    if (state.side === 1 && state.limit_price > 0 && state.start_price > 0 && state.limit_price >= state.start_price) {
      errors.push("LONG: limit < start");
    }
    if (state.side === 2 && state.limit_price > 0 && state.end_price > 0 && state.limit_price <= state.end_price) {
      errors.push("SHORT: limit > end");
    }
    if (state.total_amount_quote <= 0) {
      errors.push("Total amount required");
    }
    if (state.total_amount_quote > 0 && state.min_order_amount_quote > 0 && state.total_amount_quote < state.min_order_amount_quote) {
      errors.push("Total >= min order");
    }

    return { valid: errors.length === 0, errors };
  }, [state]);
}
