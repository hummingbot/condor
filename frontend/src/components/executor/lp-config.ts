import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useReducer } from "react";

import type { ChartPriceMapping, ExecutorValidation, PickSlot } from "./types";
import { api, type DexPoolInfo } from "@/lib/api";
import { getThemeColors } from "@/lib/theme-colors";
import { LP_DEFAULTS_KEY } from "@/lib/sessionState";

// ── Sides ──
// `side` is a TradeType enum, not a direction: it says which token(s) you are
// putting in, which in turn dictates where the range sits relative to the price.

export const LP_SIDE_BUY = 1; // quote-only, range below the price
export const LP_SIDE_SELL = 2; // base-only, range above the price
export const LP_SIDE_RANGE = 3; // both tokens, range centered

export type LpSide = 1 | 2 | 3;

/**
 * How far outside the range its auto-close triggers sit, as a multiple of the
 * range's own half-width.
 *
 * Proportional rather than fixed: a flat 10% buffer put the trigger for a 1%
 * range nine range-widths away, so a position could sit out of range indefinitely
 * without ever firing. At 1× the limits move with the preset — a 1% range closes
 * 1% beyond its bound, a 20% range 20% beyond.
 */
const LIMIT_BUFFER_RATIO = 1;

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

export type LPAction =
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

const STORAGE_KEY = LP_DEFAULTS_KEY;

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
  const buffer = pct * LIMIT_BUFFER_RATIO;
  return {
    lower_price: lower,
    upper_price: upper,
    lower_limit_price: lower * (1 - buffer),
    upper_limit_price: upper * (1 + buffer),
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
 * Bins a Meteora DLMM position of this width would span.
 *
 * Each bin is a fixed geometric step of `1 + bin_step/10000`, so the count is the
 * log of the price ratio over the log of that step. Null when the pool is not a
 * DLMM (only those report a bin step) or the bounds are not a real range.
 */
export function dlmmBinSpan(
  lower: number,
  upper: number,
  binStep: number | null | undefined,
): number | null {
  if (!binStep || binStep <= 0) return null;
  if (!(lower > 0) || !(upper > lower)) return null;
  return Math.ceil(Math.log(upper / lower) / Math.log(1 + binStep / 10000));
}

/**
 * What one Meteora DLMM position can hold. A wider range is not rejected by the
 * form — Gateway is the authority and its own cap can move — but it fails at
 * open time, and until now the only trace of that was a line in the gateway log.
 */
export const DLMM_MAX_BINS = 69;

/**
 * Things the schema permits but that are rarely meant. Kept apart from validation
 * because none of them should block a create — a deliberately odd position is
 * still a position.
 */
export function rangeWarnings(
  state: LPState,
  price: number | null,
  binStep?: number | null,
): string[] {
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

  const bins = dlmmBinSpan(state.lower_price, state.upper_price, binStep);
  if (bins !== null && bins > DLMM_MAX_BINS) {
    warnings.push(
      `Range spans ~${bins} bins; Meteora opens at most ${DLMM_MAX_BINS} per position — narrow it or the open will fail`,
    );
  }
  return warnings;
}

// ── Chart pick slots ──
// One slot per price: the two bounds and both auto-close triggers. The lower
// limit rides `limit2`, the slot for a price the panel draws itself as an extra
// line rather than one the chart owns.
const PICK_SLOT: Record<string, PickSlot> = {
  upper_price: "start",
  lower_price: "end",
  upper_limit_price: "limit",
  lower_limit_price: "limit2",
};

const SLOT_FIELD: Record<PickSlot, keyof LPState> = {
  start: "upper_price",
  end: "lower_price",
  limit: "upper_limit_price",
  limit2: "lower_limit_price",
};

// The slots are named for the grid executor; on an LP range they are prices,
// not a direction of travel, so the chart is told what they mean here.
const LP_LINE_LABELS: Partial<Record<PickSlot, string>> = {
  start: "Upper",
  end: "Lower",
  limit: "Upper limit",
  limit2: "Lower limit",
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
  /**
   * The pool the caller already knows, when it knows one (the DEX pool
   * workspace opens on a specific address). Resolving the pair's deepest pool
   * would then spend a rate-limited GeckoTerminal `search/pools` request — the
   * same per-IP budget the page's chart, stats and bins compete for — on an
   * answer the reducer discards: the caller pins its own pool, which sets
   * `poolTouched` and turns every `RESOLVED` into a no-op.
   */
  lockedPoolAddress?: string,
) {
  const [state, dispatch] = useReducer(lpReducer, undefined, loadSavedDefaults);
  const validation = useLpValidation(state);

  const { data: pool, isFetching: poolFetching } = useQuery({
    queryKey: ["dex-pool", server, connector, pair],
    queryFn: () => api.getDexPool(server!, connector, pair),
    enabled: enabled && !lockedPoolAddress && !!server && !!connector && !!pair,
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
      lineLabels: LP_LINE_LABELS,
      extraLines:
        state.lower_limit_price > 0
          ? [
              {
                price: state.lower_limit_price,
                label: "Lower limit",
                // Amber while armed, matching how the chart marks its own lines.
                color:
                  state.activePickField === "lower_limit_price"
                    ? "#fbbf24"
                    : getThemeColors().red,
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

  const handleChartPriceSet = (field: PickSlot, price: number) => {
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

