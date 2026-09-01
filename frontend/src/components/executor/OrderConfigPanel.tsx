import { useEffect } from "react";
import { Sparkles } from "lucide-react";

import {
  AmountField,
  LeverageField,
  NumberField,
  PercentPresets,
  PriceField,
  SectionHeader,
  SelectField,
  SideSelector,
  ValidationMessages,
} from "./fields";
import type { ExecutorValidation } from "./types";
import { usesLimitPrice, type OrderAction, type OrderState } from "./order-config";

// ── Execution strategy options ──

const STRATEGY_OPTIONS = [
  { value: "MARKET", label: "Market" },
  { value: "LIMIT", label: "Limit" },
  { value: "LIMIT_MAKER", label: "Limit Maker" },
  { value: "LIMIT_CHASER", label: "Limit Chaser" },
];

const POSITION_ACTION_OPTIONS = [
  { value: "OPEN", label: "Open" },
  { value: "CLOSE", label: "Close" },
];

// ── Panel Component ──

interface Props {
  state: OrderState;
  dispatch: React.Dispatch<OrderAction>;
  validation: ExecutorValidation;
  currentPrice: number | null;
  isSpot?: boolean;
  pair?: string;
  /**
   * Execution strategies this venue allows. A gateway swap has no resting order
   * book, so a DEX passes `["MARKET"]`. Defaults to all of them.
   */
  strategies?: string[];
  /** Wallet balance behind the percentage presets; `null` when it is unknown. */
  baseAvailable?: number | null;
  quoteAvailable?: number | null;
  /**
   * Display symbols for the two tokens. A DEX `trading_pair` is
   * `<base_mint>-<quote_symbol>`, so its base half is an address, not a ticker.
   */
  baseSymbol?: string;
  quoteSymbol?: string;
}

export function OrderConfigPanel({
  state,
  dispatch,
  validation,
  currentPrice,
  isSpot = false,
  pair,
  strategies,
  baseAvailable = null,
  quoteAvailable = null,
  baseSymbol,
  quoteSymbol,
}: Props) {
  const displayBase = baseSymbol || pair?.split("-")[0] || "base";
  const displayQuote = quoteSymbol || pair?.split("-")[1] || "quote";
  const options = strategies
    ? STRATEGY_OPTIONS.filter((o) => strategies.includes(o.value))
    : STRATEGY_OPTIONS;

  // A strategy carried over from another venue (LIMIT on binance → solana) has to
  // fall back, or the panel would submit a strategy the venue cannot honor.
  const allowed = options.some((o) => o.value === state.execution_strategy);
  useEffect(() => {
    if (!allowed) {
      dispatch({ type: "SET_FIELD", field: "execution_strategy", value: "MARKET" });
    }
  }, [allowed]); // eslint-disable-line react-hooks/exhaustive-deps

  // Anchor the order price on this market once, so a limit order opens with its
  // line already on the chart and ready to be dragged rather than at 0.
  useEffect(() => {
    if (currentPrice && currentPrice > 0 && !state.anchored) {
      dispatch({ type: "ANCHOR", price: currentPrice });
    }
  }, [currentPrice, state.anchored, dispatch]);

  const needsPrice = allowed && usesLimitPrice(state.execution_strategy);
  const isChaser = allowed && state.execution_strategy === "LIMIT_CHASER";

  // The amount is always in base units, but which balance funds it is not: a spot
  // BUY spends quote and has to be divided by the price to become base, a spot
  // SELL spends the base itself. A perp buys margin with quote either way, and the
  // position it opens is that margin times the leverage.
  const sizingPrice = needsPrice && state.price > 0 ? state.price : currentPrice;
  const spendsBase = isSpot && state.side === 2;
  const spendable = spendsBase ? baseAvailable : quoteAvailable;
  const spendSymbol = spendsBase ? displayBase : displayQuote;
  const canSize = spendsBase || (!!sizingPrice && sizingPrice > 0);

  const pickPercent = (pct: number) => {
    if (spendable === null || spendable <= 0) return;
    const spend = spendable * pct;
    const amount = spendsBase
      ? spend
      : (spend * (isSpot ? 1 : state.leverage)) / (sizingPrice as number);
    dispatch({ type: "SET_FIELD", field: "amount", value: Number(amount.toPrecision(8)) });
  };

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* Direction — Buy/Sell on spot: Long/Short would suggest a perp, and a
          "short" without tokens to sell is not a thing a swap can do. */}
      <SideSelector side={state.side} dispatch={dispatch} isSpot={isSpot} />

      {/* Order Config */}
      <div className="space-y-2.5">
        <SectionHeader>Order</SectionHeader>
        <div>
          <AmountField
            value={state.amount}
            field="amount"
            dispatch={dispatch}
            currentPrice={currentPrice}
            step={0.001}
            baseSymbol={displayBase}
            quoteSymbol={displayQuote}
          />
          <PercentPresets
            available={canSize ? spendable : null}
            symbol={spendSymbol}
            onPick={pickPercent}
            label={`Available ${spendSymbol} to spend`}
          />
        </div>
        <SelectField
          label="Execution Strategy"
          value={state.execution_strategy}
          field="execution_strategy"
          dispatch={dispatch}
          options={options}
          // A DEX only swaps at market; a one-option dropdown reads as broken.
          disabled={options.length <= 1}
        />
        <LeverageField value={state.leverage} field="leverage" dispatch={dispatch} isSpot={isSpot} />
        {!isSpot && (
          <SelectField
            label="Position Action"
            value={state.position_action}
            field="position_action"
            dispatch={dispatch}
            options={POSITION_ACTION_OPTIONS}
          />
        )}
      </div>

      {/* Price (for LIMIT strategies) */}
      {needsPrice && (
        <div className="space-y-2.5">
          <div className="flex items-center justify-between">
            <SectionHeader>Price</SectionHeader>
            {currentPrice && currentPrice > 0 && (
              <button
                onClick={() => dispatch({ type: "SET_FIELD", field: "price", value: currentPrice })}
                className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
              >
                <Sparkles className="h-3 w-3" />
                Use current
              </button>
            )}
          </div>
          <PriceField
            label="Order Price"
            value={state.price}
            field="price"
            activePickField={state.activePickField}
            dispatch={dispatch}
            valid={state.price > 0}
          />
        </div>
      )}

      {/* Chaser config */}
      {isChaser && (
        <div className="space-y-2.5">
          <SectionHeader>Chaser Config</SectionHeader>
          <NumberField
            label="Distance"
            value={state.chaser_distance}
            field="chaser_distance"
            dispatch={dispatch}
            step={0.01}
            isPercent
            suffix="%"
          />
          <NumberField
            label="Refresh Threshold"
            value={state.chaser_refresh_threshold}
            field="chaser_refresh_threshold"
            dispatch={dispatch}
            step={0.01}
            isPercent
            suffix="%"
          />
          <p className="text-[10px] text-[var(--color-text-muted)]">
            Chaser continuously adjusts limit order to chase the best price.
          </p>
        </div>
      )}

      <ValidationMessages errors={validation.errors} />
    </div>
  );
}
