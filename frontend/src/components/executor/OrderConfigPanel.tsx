import { useEffect, useMemo, useReducer } from "react";
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
  type FieldDispatch,
} from "./fields";
import type { ChartPriceMapping, ExecutorValidation, PickSlot } from "./types";

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
}

type OrderAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string };

const DEFAULTS: OrderState = {
  side: 1,
  amount: 0,
  execution_strategy: "LIMIT",
  price: 0,
  leverage: 1,
  chaser_distance: 0.0005,
  chaser_refresh_threshold: 0.001,
  position_action: "OPEN",
  activePickField: null,
};

const STORAGE_KEY = "condor_order_defaults";

const PERSISTED_FIELDS: (keyof OrderState)[] = [
  "side", "amount", "execution_strategy", "leverage",
  "chaser_distance", "chaser_refresh_threshold", "position_action",
];

function loadSavedDefaults(): OrderState {
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

function saveDefaults(state: OrderState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

function orderReducer(state: OrderState, action: OrderAction): OrderState {
  switch (action.type) {
    case "SET_FIELD":
      return { ...state, [action.field]: action.value };
    case "SET_CONNECTOR":
    case "SET_PAIR":
      return { ...state, price: 0 };
    default:
      return state;
  }
}

// ── Validation ──

export function useOrderValidation(state: OrderState): ExecutorValidation {
  return useMemo(() => {
    const errors: string[] = [];
    if (state.amount <= 0) errors.push("Amount required (base currency)");
    const needsPrice = state.execution_strategy === "LIMIT" || state.execution_strategy === "LIMIT_MAKER";
    if (needsPrice && state.price <= 0) errors.push("Price required for limit orders");
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
    startPrice: state.price,
    endPrice: 0,
    limitPrice: 0,
    side: state.side,
    minSpread: 0,
    activePickField: state.activePickField === "price" ? "start" : null,
  }), [state.price, state.side, state.activePickField]);

  const buildPayload = (connector: string, pair: string, isSpot: boolean) => {
    const config: Record<string, unknown> = {
      connector_name: connector,
      trading_pair: pair,
      side: state.side,
      amount: state.amount,
      leverage: isSpot ? 1 : state.leverage,
      execution_strategy: state.execution_strategy,
    };

    if (state.execution_strategy === "LIMIT" || state.execution_strategy === "LIMIT_MAKER") {
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
  const d = dispatch as FieldDispatch;
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
      d({ type: "SET_FIELD", field: "execution_strategy", value: "MARKET" });
    }
  }, [allowed]); // eslint-disable-line react-hooks/exhaustive-deps

  const needsPrice =
    allowed &&
    (state.execution_strategy === "LIMIT" || state.execution_strategy === "LIMIT_MAKER");
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
      <SideSelector side={state.side} dispatch={d} isSpot={isSpot} />

      {/* Order Config */}
      <div className="space-y-2.5">
        <SectionHeader>Order</SectionHeader>
        <div>
          <AmountField
            value={state.amount}
            field="amount"
            dispatch={d}
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
          dispatch={d}
          options={options}
          // A DEX only swaps at market; a one-option dropdown reads as broken.
          disabled={options.length <= 1}
        />
        <LeverageField value={state.leverage} field="leverage" dispatch={d} isSpot={isSpot} />
        {!isSpot && (
          <SelectField
            label="Position Action"
            value={state.position_action}
            field="position_action"
            dispatch={d}
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
                onClick={() => d({ type: "SET_FIELD", field: "price", value: currentPrice })}
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
            dispatch={d}
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
            dispatch={d}
            step={0.01}
            isPercent
            suffix="%"
          />
          <NumberField
            label="Refresh Threshold"
            value={state.chaser_refresh_threshold}
            field="chaser_refresh_threshold"
            dispatch={d}
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
