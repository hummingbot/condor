import { useEffect } from "react";
import { Sparkles } from "lucide-react";

import {
  AdvancedSection,
  AmountField,
  LeverageField,
  NumberField,
  PriceField,
  SectionHeader,
  SelectField,
  SideSelector,
  ValidationMessages,
  type FieldDispatch,
} from "./fields";
import { ORDER_TYPE_OPTIONS } from "./field-options";
import type { ExecutorValidation } from "./types";
import type { PositionAction, PositionState } from "./position-config";

// ── Panel Component ──

interface Props {
  state: PositionState;
  dispatch: React.Dispatch<PositionAction>;
  validation: ExecutorValidation;
  currentPrice: number | null;
  isSpot?: boolean;
  pair?: string;
}

export function PositionConfigPanel({ state, dispatch, validation, currentPrice, isSpot = false, pair }: Props) {
  // Anchor the entry the first time this market has a price, so the panel opens
  // with its lines already on the chart: the two barriers hang off the entry and
  // there is nothing to drag until it exists. Clearing the entry back to 0 still
  // means "market order" — the reducer has spent this market's one anchor.
  useEffect(() => {
    if (currentPrice && currentPrice > 0 && !state.anchored) {
      dispatch({ type: "ANCHOR", price: currentPrice });
    }
  }, [currentPrice, state.anchored, dispatch]);

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
                const cp = currentPrice!;
                const entry = state.side === 1 ? cp * 0.999 : cp * 1.001;
                d({ type: "SET_FIELD", field: "entry_price", value: parseFloat(entry.toPrecision(6)) });
                d({ type: "SET_FIELD", field: "stop_loss", value: 0.03 });
                d({ type: "SET_FIELD", field: "take_profit", value: 0.02 });
                d({ type: "SET_FIELD", field: "open_order_type", value: 2 });
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
        <AmountField
          value={state.amount}
          field="amount"
          dispatch={d}
          currentPrice={currentPrice}
          step={0.001}
          pair={pair}
        />
        <LeverageField value={state.leverage} field="leverage" dispatch={d} isSpot={isSpot} />
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
