/**
 * The display-currency rule: one rate table, one symbol, one `⚠`.
 *
 * Two surfaces render the same money — the pages, through `useRates`, and the
 * chat's page-context block, through `pageFacts.money()` — and they must say
 * the same thing about the same number. They used to own a copy of the rule
 * each, and the copies had already drifted: for a quote with no rate path the
 * pages kept the *quote's* symbol while the block relabelled the very same
 * unconverted number with the *display* currency's, so the screen said
 * `$-412.30 ⚠` while the agent was told `€-412.30 ⚠` (ARCH-228). The rule
 * lives here now and both build on it.
 *
 * The rule in one line: convert when there is a rate and label the result with
 * the display currency; otherwise leave the value in quote units, label it
 * with the *quote's* symbol, and mark it `⚠`.
 */

import { CURRENCY_SYMBOLS, type DisplayCurrency } from "@/hooks/useDisplayCurrency";

/** USD-pegged stablecoins — conversions between these are 1:1, no rate needed. */
export const STABLECOINS = new Set(["USDT", "USDC", "FDUSD", "BUSD", "DAI", "TUSD", "USD"]);

/**
 * Quote units per unit of display currency, as `useRates` caches them under
 * `["rates", server, currency, …]`. A `null` entry is the server answering
 * "no path for this pair" — a real answer, not a missing one.
 */
export type RateTable = Record<string, number | null> | undefined;

/** No quote means "USD-ish": the totals the API reports in plain USD. */
function normalizeQuote(quote?: string): string {
  return (quote || "USDT").toUpperCase();
}

/**
 * The rate to divide a `quote`-denominated value by, or `null` when there is
 * none — the caller then keeps the value in quote units and marks it.
 */
export function rateFor(
  rates: RateTable,
  currency: DisplayCurrency,
  quote?: string,
): number | null {
  const q = normalizeQuote(quote);
  if (q === currency) return 1;
  if (STABLECOINS.has(currency) && STABLECOINS.has(q)) return 1;
  const rate = rates?.[q];
  return rate != null && rate > 0 ? rate : null;
}

/**
 * The symbol for values converted upstream of the formatters (USD aggregates).
 * Falls back to `$` until the USD → display-currency rate is live, so the
 * number and its label never disagree about what currency they are in.
 */
export function resolveSymbol(rates: RateTable, currency: DisplayCurrency): string {
  return rateFor(rates, currency, "USDT") != null ? CURRENCY_SYMBOLS[currency] : "$";
}

/**
 * The symbol of a value that could *not* be converted. It is still in quote
 * units, so it keeps the quote's own symbol — `$` for a quote the dashboard
 * has no symbol for. Relabelling it with the display currency would not be a
 * formatting detail; it would be a wrong number on screen.
 */
function quoteSymbol(quote?: string): string {
  return CURRENCY_SYMBOLS[normalizeQuote(quote) as DisplayCurrency] || "$";
}

/**
 * `fmt` bound to a rate table and a display currency: converts and labels with
 * the display currency, or leaves the value in quote units under the quote's
 * symbol with the `⚠` marker.
 */
export function formatWithRate(
  fmt: (val: number, symbol?: string) => string,
  rates: RateTable,
  currency: DisplayCurrency,
): (val: number, quote?: string) => string {
  return (val: number, quote?: string): string => {
    const rate = rateFor(rates, currency, quote);
    return rate != null
      ? fmt(val / rate, CURRENCY_SYMBOLS[currency])
      : `${fmt(val, quoteSymbol(quote))} ⚠`;
  };
}
