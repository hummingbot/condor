import { type ExecutorInfo } from "@/lib/api";
import { formatCurrencyPnl, formatUsd } from "@/lib/formatters";

import { num } from "./format";

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

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** The quote a position's money figures are denominated in — its pair's. */
export function positionQuote(pair: string): string {
  return (pair.split("-")[1] || "USDT").trim().toUpperCase();
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
  /**
   * The quote asset the money figures below are denominated in — the pair's
   * quote, which is also what the executor's `*_quote` fields are measured in.
   */
  quote: string;
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
    quote: positionQuote(ex.trading_pair),
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

/**
 * The USD price of one unit of a position's quote token, or `null` when it is
 * unknown. The /dex surfaces read it off the pool row they already fetch
 * (`quote_token_price_usd`), so pricing a range costs no rate lookup of its own.
 */
export type QuoteUsd = number | null;

/** A quote-denominated amount under the quote's own symbol, rate unknown. */
function inQuote(amount: number, quote: string): string {
  const s =
    amount === 0 ? "0" : amount.toLocaleString("en-US", { maximumSignificantDigits: 4 });
  return `${s} ${quote}`;
}

/**
 * An LP money figure in USD when the quote's dollar price is known, else left in
 * quote units under the quote's own symbol.
 *
 * `total_value_quote`, `fees_earned_quote` and `net_pnl_quote` are all
 * denominated in the pair's *quote* — SOL on an `X-SOL` range — but the cards
 * used to prefix every one of them with a `$`. That mislabelled, rather than
 * merely rounded, the whole figure: a 0.163 SOL range worth about $20 read as
 * "$0.16". Converting needs the pool's own USD price, which both /dex callers
 * already hold; when it is missing the number is left in SOL and *labelled*
 * SOL rather than a dollar it is not.
 */
export function lpValue(amount: number, quoteUsd: QuoteUsd, quote: string): string {
  return quoteUsd != null && quoteUsd > 0 ? formatUsd(amount * quoteUsd) : inQuote(amount, quote);
}

export function lpPnl(amount: number, quoteUsd: QuoteUsd, quote: string): string {
  if (quoteUsd != null && quoteUsd > 0) return formatCurrencyPnl(amount * quoteUsd);
  return (amount >= 0 ? "+" : "") + inQuote(amount, quote);
}

export function lpFees(amount: number, quoteUsd: QuoteUsd, quote: string): string {
  return quoteUsd != null && quoteUsd > 0
    ? feeAmount(amount * quoteUsd)
    : inQuote(amount, quote);
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

/**
 * Where the price sits inside the range, as a 0–1 fraction, or `null`.
 *
 * The number a CLMM position is actually about: a range you are 95% of the way
 * through is one swap from earning nothing, and no amount of reading two price
 * strings makes that as obvious as a marker does.
 *
 * Lives here rather than beside one renderer because the bar above a pool's
 * chart and the portfolio's liquidity table draw the same marker.
 */
export function rangeFraction(pos: LpPosition, price: number | null): number | null {
  const { lowerPrice: lo, upperPrice: hi } = pos;
  if (!lo || !hi || hi <= lo || !price || price <= 0) return null;
  return Math.min(1, Math.max(0, (price - lo) / (hi - lo)));
}
