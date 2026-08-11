import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useReducer } from "react";
import { AlertTriangle, Sparkles } from "lucide-react";

import {
  AdvancedSection,
  NumberField,
  PriceField,
  SectionHeader,
  SelectField,
  ToggleField,
  ValidationMessages,
  type FieldDispatch,
} from "./fields";
import type { ChartPriceMapping, ExecutorValidation } from "./types";
import { api, type DexPoolInfo } from "@/lib/api";
import { getThemeColors } from "@/lib/theme-colors";

// ── Sides ──
// `side` is a TradeType enum, not a direction: it says which token(s) you are
// putting in, which in turn dictates where the range sits relative to the price.

export const LP_SIDE_BUY = 1; // quote-only, range below the price
export const LP_SIDE_SELL = 2; // base-only, range above the price
export const LP_SIDE_RANGE = 3; // both tokens, range centered

export type LpSide = 1 | 2 | 3;

/** The buffer between a range bound and its auto-close trigger. */
const LIMIT_BUFFER = 0.1;

// ── State ──

export interface LPState {
  pool_address: string;
  lp_provider: string;
  lower_price: number;
  upper_price: number;
  lower_limit_price: number;
  upper_limit_price: number;
  side: LpSide;
  base_amount: number;
  quote_amount: number;
  keep_position: boolean;
  /** Meteora only: 0 Spot / 1 Curve / 2 Bid-Ask. Held as a string for SelectField. */
  strategy_type: string;
  /** Half-width of the auto-filled range, as a fraction of the current price. */
  range_pct: number;
  activePickField: string | null;
  showAdvanced: boolean;
  /**
   * Whether the user has typed a pool or provider. A manual entry is the real
   * answer for anyone who cares about fee tier or bin step, so it must survive
   * every re-resolve of the auto-resolved pool.
   */
  poolTouched: boolean;
}

type LPAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string }
  | { type: "RESOLVED"; pool: DexPoolInfo }
  | { type: "AUTO_RANGE"; price: number }
  | { type: "SET_SIDE"; value: LpSide; price: number | null }
  | { type: "SET_RANGE_PCT"; value: number; price: number | null };

const DEFAULTS: LPState = {
  pool_address: "",
  lp_provider: "",
  lower_price: 0,
  upper_price: 0,
  lower_limit_price: 0,
  upper_limit_price: 0,
  side: LP_SIDE_RANGE,
  base_amount: 0,
  quote_amount: 0,
  keep_position: true,
  strategy_type: "0",
  range_pct: 0.05,
  activePickField: null,
  showAdvanced: false,
  poolTouched: false,
};

const STORAGE_KEY = "condor_lp_defaults";

// The pool and the range belong to a pair, not to the user's habits, so neither is
// persisted — only the shape of position they tend to open.
const PERSISTED_FIELDS: (keyof LPState)[] = [
  "side",
  "base_amount",
  "quote_amount",
  "keep_position",
  "strategy_type",
  "range_pct",
];

function loadSavedDefaults(): LPState {
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

function saveDefaults(state: LPState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

/**
 * Range bounds and auto-close triggers for a side, anchored on the current price.
 *
 * A single-sided position has to start out of range for its conversion to happen
 * in the direction the tokens allow: quote-only (BUY) converts to base as the
 * price *falls* through a range below it, base-only (SELL) converts to quote as
 * the price *rises* through a range above it. Centering either one would leave
 * half the range unusable.
 */
export function rangeForSide(side: LpSide, price: number, pct: number) {
  let lower: number;
  let upper: number;
  if (side === LP_SIDE_BUY) {
    lower = price * (1 - pct);
    upper = price;
  } else if (side === LP_SIDE_SELL) {
    lower = price;
    upper = price * (1 + pct);
  } else {
    lower = price * (1 - pct);
    upper = price * (1 + pct);
  }
  return {
    lower_price: lower,
    upper_price: upper,
    lower_limit_price: lower * (1 - LIMIT_BUFFER),
    upper_limit_price: upper * (1 + LIMIT_BUFFER),
  };
}

function lpReducer(state: LPState, action: LPAction): LPState {
  switch (action.type) {
    case "SET_FIELD": {
      const touched =
        action.field === "pool_address" || action.field === "lp_provider";
      return {
        ...state,
        [action.field]: action.value,
        ...(touched ? { poolTouched: true } : {}),
      };
    }
    case "SET_CONNECTOR":
    case "SET_PAIR":
      // A pool address belongs to one pair on one chain. Carrying it across would
      // submit a position in a pool that trades something else entirely.
      return {
        ...state,
        pool_address: "",
        lp_provider: "",
        poolTouched: false,
        lower_price: 0,
        upper_price: 0,
        lower_limit_price: 0,
        upper_limit_price: 0,
      };
    case "RESOLVED": {
      // Only a pool that can actually take a position is filled in. Handing over
      // the address of a router pool would just build a payload the API rejects.
      if (state.poolTouched || !action.pool.lp_supported) return state;
      return {
        ...state,
        pool_address: action.pool.pool_address ?? "",
        lp_provider: action.pool.lp_provider ?? "",
      };
    }
    case "AUTO_RANGE":
      return { ...state, ...rangeForSide(state.side, action.price, state.range_pct) };
    case "SET_SIDE":
      return {
        ...state,
        side: action.value,
        ...(action.price && action.price > 0
          ? rangeForSide(action.value, action.price, state.range_pct)
          : {}),
      };
    case "SET_RANGE_PCT":
      return {
        ...state,
        range_pct: action.value,
        ...(action.price && action.price > 0
          ? rangeForSide(state.side, action.price, action.value)
          : {}),
      };
    default:
      return state;
  }
}

// ── Validation ──

export function useLpValidation(state: LPState): ExecutorValidation {
  return useMemo(() => {
    const errors: string[] = [];
    if (!state.pool_address) errors.push("Pool address required");
    if (!state.lp_provider) errors.push("LP provider required (e.g. meteora/clmm)");
    if (state.lower_price <= 0) errors.push("Lower price required");
    if (state.upper_price <= 0) errors.push("Upper price required");
    if (
      state.lower_price > 0 &&
      state.upper_price > 0 &&
      state.upper_price <= state.lower_price
    ) {
      errors.push("Upper price must be above lower price");
    }
    if (state.base_amount <= 0 && state.quote_amount <= 0) {
      errors.push("At least one amount required");
    }
    return { valid: errors.length === 0, errors };
  }, [state]);
}

/**
 * Things the schema permits but that are rarely meant. Kept apart from validation
 * because none of them should block a create — a deliberately odd position is
 * still a position.
 */
function rangeWarnings(state: LPState, price: number | null): string[] {
  const warnings: string[] = [];
  const bothAmounts = state.base_amount > 0 && state.quote_amount > 0;

  if (state.side === LP_SIDE_RANGE && !bothAmounts) {
    warnings.push("RANGE is double-sided; only one amount is set");
  }
  if (state.side !== LP_SIDE_RANGE && bothAmounts) {
    warnings.push("Single-sided side with both amounts set — one will be unused");
  }
  if (price && price > 0) {
    if (state.side === LP_SIDE_BUY && state.lower_price > price) {
      warnings.push("BUY (quote-only) usually ranges below the current price");
    }
    if (state.side === LP_SIDE_SELL && state.upper_price < price) {
      warnings.push("SELL (base-only) usually ranges above the current price");
    }
  }
  if (state.upper_limit_price > 0 && state.upper_limit_price <= state.upper_price) {
    warnings.push("Upper limit sits inside the range");
  }
  if (state.lower_limit_price > 0 && state.lower_limit_price >= state.lower_price) {
    warnings.push("Lower limit sits inside the range");
  }
  return warnings;
}

// ── Chart pick slots ──
// The chart carries three pick slots. Upper/lower bounds and the upper limit get
// them; the lower limit is typed, and its PriceField offers no crosshair.
const PICK_SLOT: Record<string, "start" | "end" | "limit"> = {
  upper_price: "start",
  lower_price: "end",
  upper_limit_price: "limit",
};

const SLOT_FIELD: Record<"start" | "end" | "limit", keyof LPState> = {
  start: "upper_price",
  end: "lower_price",
  limit: "upper_limit_price",
};

export function isMeteoraProvider(provider: string): boolean {
  return provider.toLowerCase().startsWith("meteora/");
}

// ── Hook ──

export function useLpConfig(
  server: string | null,
  connector: string,
  pair: string,
  enabled: boolean,
) {
  const [state, dispatch] = useReducer(lpReducer, undefined, loadSavedDefaults);
  const validation = useLpValidation(state);

  const { data: pool, isFetching: poolFetching } = useQuery({
    queryKey: ["dex-pool", server, connector, pair],
    queryFn: () => api.getDexPool(server!, connector, pair),
    enabled: enabled && !!server && !!connector && !!pair,
    staleTime: 60 * 1000,
  });

  useEffect(() => {
    if (pool) dispatch({ type: "RESOLVED", pool });
  }, [pool]);

  const chartProps: ChartPriceMapping = useMemo(
    () => ({
      startPrice: state.upper_price,
      endPrice: state.lower_price,
      limitPrice: state.upper_limit_price,
      // ChartPriceMapping.side is 1 | 2 and only selects the picker's color, so
      // RANGE collapses onto the BUY color rather than growing the union.
      side: state.side === LP_SIDE_SELL ? 2 : 1,
      minSpread: 0,
      activePickField: PICK_SLOT[state.activePickField ?? ""] ?? null,
      extraLines:
        state.lower_limit_price > 0
          ? [
              {
                price: state.lower_limit_price,
                label: "Lower limit",
                color: getThemeColors().red,
                lineStyle: "dotted" as const,
                lineWidth: 1,
              },
            ]
          : undefined,
    }),
    [
      state.upper_price,
      state.lower_price,
      state.upper_limit_price,
      state.lower_limit_price,
      state.side,
      state.activePickField,
    ],
  );

  const buildPayload = (connectorName: string, tradingPair: string) => {
    const config: Record<string, unknown> = {
      // The NETWORK (solana-mainnet-beta), not the DEX — the API rejects
      // `meteora/clmm` here with "Invalid network format". The DEX is lp_provider.
      connector_name: connectorName,
      lp_provider: state.lp_provider,
      trading_pair: tradingPair,
      pool_address: state.pool_address,
      lower_price: state.lower_price,
      upper_price: state.upper_price,
      side: state.side,
      base_amount: state.base_amount,
      quote_amount: state.quote_amount,
      keep_position: state.keep_position,
    };
    // Both default to null (no trigger); 0 is not a way to say "unset".
    if (state.upper_limit_price > 0) config.upper_limit_price = state.upper_limit_price;
    if (state.lower_limit_price > 0) config.lower_limit_price = state.lower_limit_price;
    // strategyType is Meteora's alone — any other provider rejects extra_params.
    if (isMeteoraProvider(state.lp_provider)) {
      config.extra_params = { strategyType: Number(state.strategy_type) };
    }
    return { executor_type: "lp_executor" as const, config };
  };

  const save = () => saveDefaults(state);

  const handleChartPriceSet = (field: "start" | "end" | "limit", price: number) => {
    dispatch({ type: "SET_FIELD", field: SLOT_FIELD[field], value: price });
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return {
    state,
    dispatch,
    validation,
    chartProps,
    buildPayload,
    save,
    handleChartPriceSet,
    pool,
    poolFetching,
  };
}

// ── Options ──

const SIDE_OPTIONS: { value: LpSide; label: string; hint: string }[] = [
  { value: LP_SIDE_RANGE, label: "Range", hint: "Both tokens, range around price" },
  { value: LP_SIDE_BUY, label: "Buy", hint: "Quote only, range below price" },
  { value: LP_SIDE_SELL, label: "Sell", hint: "Base only, range above price" },
];

const PROVIDER_OPTIONS = [
  { value: "", label: "Select provider…" },
  { value: "meteora/clmm", label: "Meteora (DLMM)" },
  { value: "raydium/clmm", label: "Raydium (CLMM)" },
  { value: "orca/clmm", label: "Orca (Whirlpools)" },
  { value: "uniswap/clmm", label: "Uniswap V3" },
  { value: "pancakeswap/clmm", label: "PancakeSwap V3" },
];

const STRATEGY_TYPE_OPTIONS = [
  { value: "0", label: "Spot (uniform)" },
  { value: "1", label: "Curve (concentrated)" },
  { value: "2", label: "Bid-Ask (edges)" },
];

const RANGE_PCT_PRESETS = [0.01, 0.02, 0.05, 0.1, 0.2];

function truncateAddress(address: string): string {
  return address.length > 16
    ? `${address.slice(0, 6)}…${address.slice(-6)}`
    : address;
}

function formatPoolPrice(price: number): string {
  if (price >= 1000) return price.toFixed(2);
  if (price >= 1) return price.toFixed(4);
  return price.toPrecision(6);
}

// ── Panel Component ──

interface Props {
  state: LPState;
  dispatch: React.Dispatch<LPAction>;
  validation: ExecutorValidation;
  currentPrice: number | null;
  pair?: string;
  pool?: DexPoolInfo;
  poolFetching?: boolean;
}

export function LPConfigPanel({
  state,
  dispatch,
  validation,
  currentPrice,
  pair,
  pool,
  poolFetching,
}: Props) {
  const d = dispatch as FieldDispatch;

  // A DEX pair's chart can be blank (no candles for the pool) while the pool's own
  // price is known, so the resolved pool is a second source for the anchor.
  const price = currentPrice && currentPrice > 0 ? currentPrice : pool?.current_price ?? null;

  const baseAsset = pair?.split("-")[0] || pool?.base_symbol || "base";
  const quoteAsset = pair?.split("-")[1] || pool?.quote_symbol || "quote";

  // Anchor the range the first time a price is known. Not a reset: once the bounds
  // are non-zero they are the user's, and only an explicit action moves them.
  const unanchored = state.lower_price === 0 && state.upper_price === 0;
  useEffect(() => {
    if (price && price > 0 && unanchored) {
      dispatch({ type: "AUTO_RANGE", price });
    }
  }, [price, unanchored]); // eslint-disable-line react-hooks/exhaustive-deps

  const warnings = rangeWarnings(state, price);
  const unsupported = !!pool && !pool.lp_supported;

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* Resolved pool */}
      <div className="space-y-2">
        <SectionHeader>Pool</SectionHeader>
        {poolFetching && !pool ? (
          <p className="text-[10px] text-[var(--color-text-muted)]">Resolving pool…</p>
        ) : unsupported ? (
          <div className="rounded border border-amber-400/30 bg-amber-400/10 p-2">
            <p className="flex items-start gap-1 text-[10px] text-amber-400">
              <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
              <span>
                The deepest pool for this pair is on{" "}
                <span className="font-mono">{pool?.dex_id ?? "an unknown venue"}</span>, which
                is not a CLMM venue. Enter a pool address and provider by hand.
              </span>
            </p>
          </div>
        ) : pool?.pool_address ? (
          <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
            <span className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 font-medium text-[var(--color-text)]">
              {pool.dex_id}
            </span>
            <span className="font-mono" title={pool.pool_address}>
              {truncateAddress(pool.pool_address)}
            </span>
            {pool.current_price != null && (
              <span className="font-mono">
                {formatPoolPrice(pool.current_price)} {quoteAsset}
              </span>
            )}
            {state.poolTouched && (
              <span className="rounded bg-[var(--color-primary)]/15 px-1.5 py-0.5 text-[var(--color-primary)]">
                overridden
              </span>
            )}
          </div>
        ) : (
          <p className="text-[10px] text-[var(--color-text-muted)]">
            No pool found for this pair. Enter one by hand.
          </p>
        )}

        <div>
          <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
            Pool Address
          </label>
          <input
            type="text"
            value={state.pool_address}
            onChange={(e) =>
              d({ type: "SET_FIELD", field: "pool_address", value: e.target.value.trim() })
            }
            placeholder="Pool contract address"
            spellCheck={false}
            className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-[11px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:border-[var(--color-primary)] focus:outline-none"
          />
        </div>
        <SelectField
          label="LP Provider"
          value={state.lp_provider}
          field="lp_provider"
          dispatch={d}
          options={PROVIDER_OPTIONS}
        />
      </div>

      {/* Side */}
      <div className="space-y-2">
        <SectionHeader>Position Side</SectionHeader>
        <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
          {SIDE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => dispatch({ type: "SET_SIDE", value: opt.value, price })}
              title={opt.hint}
              className={`flex-1 px-2 py-1.5 text-[11px] font-medium transition-colors ${
                state.side === opt.value
                  ? "bg-[var(--color-primary)] text-white"
                  : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-[var(--color-text-muted)]">
          {SIDE_OPTIONS.find((o) => o.value === state.side)?.hint}
        </p>
      </div>

      {/* Range */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <SectionHeader>Range</SectionHeader>
          {price != null && price > 0 && (
            <button
              onClick={() => dispatch({ type: "AUTO_RANGE", price })}
              className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <Sparkles className="h-3 w-3" />
              Auto
            </button>
          )}
        </div>
        <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
          {RANGE_PCT_PRESETS.map((pct) => (
            <button
              key={pct}
              onClick={() => dispatch({ type: "SET_RANGE_PCT", value: pct, price })}
              className={`flex-1 px-1.5 py-1 text-[10px] transition-colors ${
                state.range_pct === pct
                  ? "bg-[var(--color-primary)] text-white"
                  : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              {pct * 100}%
            </button>
          ))}
        </div>
        <PriceField
          label="Upper Price"
          value={state.upper_price}
          field="upper_price"
          activePickField={state.activePickField}
          dispatch={d}
          valid={state.upper_price > state.lower_price}
        />
        <PriceField
          label="Lower Price"
          value={state.lower_price}
          field="lower_price"
          activePickField={state.activePickField}
          dispatch={d}
          valid={state.lower_price > 0 && state.lower_price < state.upper_price}
        />
      </div>

      {/* Amounts */}
      <div className="space-y-2.5">
        <SectionHeader>Amounts</SectionHeader>
        <NumberField
          label={`Base Amount (${baseAsset})`}
          value={state.base_amount}
          field="base_amount"
          dispatch={d}
          step={0.001}
          min={0}
        />
        <NumberField
          label={`Quote Amount (${quoteAsset})`}
          value={state.quote_amount}
          field="quote_amount"
          dispatch={d}
          step={0.001}
          min={0}
        />
      </div>

      {/* Auto-close triggers */}
      <div className="space-y-2.5">
        <SectionHeader>Auto-Close Triggers</SectionHeader>
        <PriceField
          label="Upper Limit"
          value={state.upper_limit_price}
          field="upper_limit_price"
          activePickField={state.activePickField}
          dispatch={d}
          valid={state.upper_limit_price > state.upper_price}
          hint="Close when price rises to this level"
        />
        <PriceField
          label="Lower Limit"
          value={state.lower_limit_price}
          field="lower_limit_price"
          activePickField={state.activePickField}
          dispatch={d}
          valid={
            state.lower_limit_price > 0 && state.lower_limit_price < state.lower_price
          }
          hint="Close when price falls to this level"
          pickable={false}
        />
        <p className="text-[10px] text-[var(--color-text-muted)]">
          Both only fire while the position is out of range. Leave at 0 for no trigger.
        </p>
      </div>

      <AdvancedSection
        open={state.showAdvanced}
        onToggle={() =>
          d({ type: "SET_FIELD", field: "showAdvanced", value: !state.showAdvanced })
        }
      >
        <ToggleField
          label="Keep Position"
          value={state.keep_position}
          field="keep_position"
          dispatch={d}
        />
        <p className="text-[10px] text-[var(--color-text-muted)]">
          On close, keep the net token change as a spot position instead of swapping
          back to {quoteAsset}. This sets the config field; stopping from the
          executors table asks again.
        </p>
        {isMeteoraProvider(state.lp_provider) && (
          <SelectField
            label="Meteora Strategy"
            value={state.strategy_type}
            field="strategy_type"
            dispatch={d}
            options={STRATEGY_TYPE_OPTIONS}
          />
        )}
      </AdvancedSection>

      <ValidationMessages errors={validation.errors} warnings={warnings} />
    </div>
  );
}
