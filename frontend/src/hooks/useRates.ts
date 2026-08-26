import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api";
import { formatCurrency, formatCurrencyPnl, formatCurrencyVolume } from "@/lib/formatters";
import { formatWithRate, rateFor, resolveSymbol, STABLECOINS } from "@/lib/rates";
import { useDisplayCurrency } from "./useDisplayCurrency";
import { useServer } from "./useServer";

export function useRates(quoteCurrencies: string[]) {
  const { currency, currencySymbol } = useDisplayCurrency();
  const { server } = useServer();

  // Dedupe and filter out currencies that match the display currency
  // Also skip stablecoin-to-stablecoin pairs (rate is ~1.0)
  const { needed, stablePairs } = useMemo(() => {
    const set = new Set<string>();
    const stable: string[] = [];
    const isCurrencyStable = STABLECOINS.has(currency);
    for (const q of quoteCurrencies) {
      const norm = q.toUpperCase();
      if (norm === currency) continue;
      if (isCurrencyStable && STABLECOINS.has(norm)) {
        stable.push(norm);
      } else {
        set.add(norm);
      }
    }
    return { needed: Array.from(set).sort(), stablePairs: stable };
  }, [quoteCurrencies, currency]);

  const { data: rates, isLoading } = useQuery({
    queryKey: ["rates", server, currency, needed.join(",")],
    queryFn: async () => {
      const results: Record<string, number | null> = {};
      // Stablecoin-to-stablecoin: use 1:1 rate (no API call needed)
      for (const q of stablePairs) {
        results[q] = 1.0;
      }
      if (needed.length > 0) {
        const pairs = needed.map((quote) => `${currency}-${quote}`);
        // A request failure must propagate: turning it into null rates would cache a
        // "successful" unconvertible result for the whole staleTime, so a single blip
        // (a server still warming up, say) freezes every value in the old currency
        // until the page is reloaded. Throwing lets React Query retry instead.
        const resp = await api.getRates(server!, pairs);
        const rateMap = resp.rates ?? {};
        for (const quote of needed) {
          const pair = `${currency}-${quote}`;
          const rate = rateMap[pair];
          // A null here is the server saying "no path for this pair" — a real answer,
          // rendered with the ⚠ marker rather than retried.
          results[quote] = rate != null ? rate : null;
        }
      }
      return results;
    },
    enabled: !!server && (needed.length > 0 || stablePairs.length > 0),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: 3,
    retryDelay: (attempt: number) => Math.min(500 * 2 ** attempt, 5_000),
    // Only reuse rates when just the requested quote set changed. Carrying them
    // across a currency switch leaves `convert()` silently unconverted while the
    // new rates load, so values would render unchanged under the new symbol.
    placeholderData: (
      prev: Record<string, number | null> | undefined,
      prevQuery: { queryKey: readonly unknown[] } | undefined,
    ) => {
      const prevKey = prevQuery?.queryKey;
      return prevKey?.[1] === server && prevKey?.[2] === currency ? prev : undefined;
    },
  });

  // The rule itself lives in `lib/rates.ts`, shared with the chat's
  // page-context block so the two never drift apart again (ARCH-228).
  const convert = useMemo(() => {
    return (value: number, quoteCurrency: string): { value: number; converted: boolean } => {
      const rate = rateFor(rates, currency, quoteCurrency);
      return rate != null ? { value: value / rate, converted: true } : { value, converted: false };
    };
  }, [rates, currency]);

  // Symbol for values already run through `convert()` (aggregates, USD totals).
  // Falls back to "$" until the USD -> display-currency rate is live, so the
  // number and its label never disagree.
  const usdConverted = useMemo(() => rateFor(rates, currency, "USDT") != null, [rates, currency]);
  const resolvedSymbol = useMemo(() => resolveSymbol(rates, currency), [rates, currency]);

  const formatValue = useMemo(
    () => formatWithRate(formatCurrencyVolume, rates, currency),
    [rates, currency],
  );

  const formatPnlValue = useMemo(
    () => formatWithRate(formatCurrencyPnl, rates, currency),
    [rates, currency],
  );

  const formatValueDetailed = useMemo(
    () => formatWithRate(formatCurrency, rates, currency),
    [rates, currency],
  );

  return {
    rates: rates ?? {},
    convert,
    formatValue,
    formatPnlValue,
    formatValueDetailed,
    isLoading,
    currency,
    currencySymbol,
    resolvedSymbol,
    usdConverted,
  };
}
