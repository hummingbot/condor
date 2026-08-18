import { type ExecutorInfo } from "@/lib/api";
import { formatUsd } from "@/lib/formatters";

/** LP executors are the only ones the DEX pages can route to a pool. */
export const LP_EXECUTOR_TYPE = "lp_executor";

/** Live enough to see a range break without hammering the API. */
export const LP_REFRESH_MS = 20_000;

/**
 * How far back a "which LP positions exist" poll reads.
 *
 * The upstream search has no usable "open only" filter (its `status` values are
 * not the ones the API normalizes to), so open positions are found by reading
 * recent executors and filtering client-side. Unbounded that is a walk of the
 * entire history every {@link LP_REFRESH_MS}; the search answers newest-first and
 * an open LP position is by definition recent, so the newest few hundred contain
 * them.
 *
 * Shared by the `/dex` strip and a pool's own page so both issue the *same*
 * query — one cache entry, and opening a pool from the strip costs no fetch.
 */
export const RECENT_LP_EXECUTORS = 200;

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/**
 * One open LP position, flattened out of the executor that holds it.
 *
 * The executor carries the pool in its *config* and the position in its
 * *custom_info*, and neither is typed — so the reading happens once, here, rather
 * than in the render. Both the `/dex` strip and the bar above a pool's chart read
 * a position through this, so the two cannot disagree about what one is.
 */
export interface LpPosition {
  id: string;
  /** The Gateway network, which is also the first segment of the pool's URL. */
  network: string;
  poolAddress: string;
  provider: string;
  pair: string;
  /** `IN_RANGE`, `OUT_OF_RANGE`, … as the connector reports it. */
  state: string;
  /** On-chain bounds when the venue reports them, else the requested ones. */
  lowerPrice: number | null;
  upperPrice: number | null;
  currentPrice: number | null;
  valueQuote: number | null;
  feesQuote: number | null;
  pnl: number;
}

export function readLpPosition(ex: ExecutorInfo): LpPosition | null {
  const config = ex.config ?? {};
  const custom = ex.custom_info ?? {};
  const poolAddress = str(config.pool_address);
  const network = str(config.connector_name) || ex.connector;
  // Without a pool there is nowhere to click through to, which is the whole
  // point of the strip — so such an executor belongs on /executors, not here.
  if (!poolAddress || !network) return null;
  // custom_info wins: a CLMM position is snapped to the venue's bins, so the
  // bounds it actually holds are not the ones that were asked for.
  return {
    id: ex.id,
    network,
    poolAddress,
    provider: str(config.lp_provider),
    pair: ex.trading_pair,
    state: str(custom.state),
    lowerPrice: num(custom.lower_price) ?? num(config.lower_price),
    upperPrice: num(custom.upper_price) ?? num(config.upper_price),
    currentPrice: num(custom.current_price) || (ex.current_price > 0 ? ex.current_price : null),
    valueQuote: num(custom.total_value_quote),
    feesQuote: num(custom.fees_earned_quote),
    pnl: ex.pnl,
  };
}

/** Fees on a fresh position are fractions of a cent, where `$0.00` says nothing. */
export function feeAmount(val: number): string {
  if (val !== 0 && Math.abs(val) < 0.01) return `$${val.toPrecision(2)}`;
  return formatUsd(val);
}

/** In range is earning; out of range is not, and is the thing worth spotting. */
export function lpStateStyle(state: string): {
  label: string;
  color: string;
  bg: string;
} {
  const upper = state.toUpperCase();
  if (upper === "IN_RANGE") {
    return {
      label: "In range",
      color: "var(--color-green)",
      bg: "rgba(34,197,94,0.12)",
    };
  }
  if (upper === "OUT_OF_RANGE") {
    return {
      label: "Out of range",
      color: "var(--color-yellow)",
      bg: "rgba(234,179,8,0.14)",
    };
  }
  return {
    label: upper ? upper.replace(/_/g, " ").toLowerCase() : "open",
    color: "var(--color-text-muted)",
    bg: "var(--color-surface)",
  };
}
