import { useEffect, useMemo } from "react";
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
} from "./fields";
import type { ExecutorValidation } from "./types";
import { dcaBreakEven, levelSlot, type DCAAction, type DCAState } from "./dca-config";

// ── Panel Component ──

const MODE_OPTIONS = [
  { value: "MAKER", label: "Maker (Limit)" },
  { value: "TAKER", label: "Taker (Market)" },
];

interface Props {
  state: DCAState;
  dispatch: React.Dispatch<DCAAction>;
  validation: ExecutorValidation;
  currentPrice: number | null;
  isSpot?: boolean;
  pair?: string;
}

export function DCAConfigPanel({ state, dispatch, validation, currentPrice, isSpot = false, pair: _pair }: Props) {
  const totalQuote = state.amounts_quote.reduce((s, a) => s + a, 0);

  const bep = useMemo(
    () => dcaBreakEven(state.prices, state.amounts_quote),
    [state.prices, state.amounts_quote],
  );

  // Ladder the levels off this market's price once, so the panel opens with its
  // lines on the chart. Spent per market, so a set the user cleared stays clear.
  useEffect(() => {
    if (currentPrice && currentPrice > 0 && !state.anchored) {
      dispatch({ type: "ANCHOR", price: currentPrice });
    }
  }, [currentPrice, state.anchored, dispatch]);

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* Direction */}
      <SideSelector side={state.side} dispatch={dispatch} />

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
                field={levelSlot(i)}
                activePickField={state.activePickField}
                dispatch={dispatch}
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
        <LeverageField value={state.leverage} field="leverage" dispatch={dispatch} isSpot={isSpot} />
      </div>

      {/* Exit Strategy */}
      <div className="space-y-2.5">
        <SectionHeader>Exit Strategy</SectionHeader>
        <NumberField label="Take Profit" value={state.take_profit} field="take_profit" dispatch={dispatch} step={0.01} isPercent suffix="%" />
        <NumberField label="Stop Loss" value={state.stop_loss} field="stop_loss" dispatch={dispatch} step={0.01} isPercent suffix="%" />
        <NumberField label="Time Limit (0 = disabled)" value={state.time_limit} field="time_limit" dispatch={dispatch} step={60} min={0} suffix="sec" />
      </div>

      {/* Mode & Advanced */}
      <AdvancedSection
        open={state.showAdvanced}
        onToggle={() => dispatch({ type: "SET_FIELD", field: "showAdvanced", value: !state.showAdvanced })}
      >
        <SelectField label="Mode" value={state.mode} field="mode" dispatch={dispatch} options={MODE_OPTIONS} />
        <NumberField label="Activation Bounds (0 = disabled)" value={state.activation_bounds} field="activation_bounds" dispatch={dispatch} step={0.01} isPercent suffix="%" />
        <div className="space-y-2.5">
          <SectionHeader>Trailing Stop</SectionHeader>
          <NumberField label="Activation Price" value={state.trailing_stop_activation_price} field="trailing_stop_activation_price" dispatch={dispatch} step={0.01} isPercent suffix="%" />
          <NumberField label="Trailing Delta" value={state.trailing_stop_trailing_delta} field="trailing_stop_trailing_delta" dispatch={dispatch} step={0.01} isPercent suffix="%" />
        </div>
      </AdvancedSection>

      <ValidationMessages errors={validation.errors} />
    </div>
  );
}
