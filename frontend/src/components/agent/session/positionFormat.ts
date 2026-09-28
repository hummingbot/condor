// Formatting helpers for the Positions Held card (CORR-389), kept out of the
// component module so react-refresh can hot-swap it.

/**
 * A price at full precision under `symbol`. `formatCurrency` abbreviates at
 * 10K, which would print a BTC entry and current price as the same `R$57.4K`;
 * sub-unit prices keep four significant digits so a memecoin, or a price
 * converted into BTC, does not collapse to `0.00`.
 */
export function formatPositionPrice(val: number, symbol = "$"): string {
  return symbol + (val === 0 || Math.abs(val) >= 1 ? val.toFixed(2) : val.toPrecision(4));
}

/** The quote currency of a `BASE-QUOTE` pair, `USDT` when it has none. */
export function quoteOf(tradingPair?: string): string {
  return tradingPair?.split("-")[1] || "USDT";
}
