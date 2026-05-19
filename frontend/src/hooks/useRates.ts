import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api";
import { formatCurrency, formatCurrencyPnl, formatCurrencyVolume } from "@/lib/formatters";
import { useDisplayCurrency, CURRENCY_SYMBOLS, type DisplayCurrency } from "./useDisplayCurrency";
import { useServer } from "./useServer";

// USD-pegged stablecoins — treat conversions between these as 1:1
const STABLECOINS = new Set(["USDT", "USDC", "FDUSD", "BUSD", "DAI", "TUSD", "USD"]);

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
        try {
          const resp = await api.getRateOracleRates(server!, pairs);
          const rateMap = resp.rates ?? {};
          for (const quote of needed) {
            const pair = `${currency}-${quote}`;
            const rate = rateMap[pair];
            results[quote] = rate != null ? rate : null;
          }
        } catch {
          for (const quote of needed) {
            results[quote] = null;
          }
        }
      }
      return results;
    },
    enabled: !!server && (needed.length > 0 || stablePairs.length > 0),
    staleTime: 60_000,
    refetchInterval: 60_000,
    placeholderData: (prev: Record<string, number | null> | undefined) => prev,
  });

  const convert = useMemo(() => {
    return (value: number, quoteCurrency: string): { value: number; converted: boolean } => {
      const norm = quoteCurrency.toUpperCase();
      if (norm === currency) return { value, converted: true };
      const rate = rates?.[norm];
      if (rate != null && rate > 0) return { value: value / rate, converted: true };
      return { value, converted: false };
    };
  }, [rates, currency]);

  const formatValue = useMemo(() => {
    return (val: number, quoteCurrency: string): string => {
      const { value, converted } = convert(val, quoteCurrency);
      const sym = converted ? currencySymbol : CURRENCY_SYMBOLS[quoteCurrency.toUpperCase() as DisplayCurrency] || "$";
      return formatCurrencyVolume(value, sym) + (converted ? "" : " \u26A0");
    };
  }, [convert, currencySymbol]);

  const formatPnlValue = useMemo(() => {
    return (val: number, quoteCurrency: string): string => {
      const { value, converted } = convert(val, quoteCurrency);
      const sym = converted ? currencySymbol : CURRENCY_SYMBOLS[quoteCurrency.toUpperCase() as DisplayCurrency] || "$";
      return formatCurrencyPnl(value, sym) + (converted ? "" : " \u26A0");
    };
  }, [convert, currencySymbol]);

  const formatValueDetailed = useMemo(() => {
    return (val: number, quoteCurrency: string): string => {
      const { value, converted } = convert(val, quoteCurrency);
      const sym = converted ? currencySymbol : CURRENCY_SYMBOLS[quoteCurrency.toUpperCase() as DisplayCurrency] || "$";
      return formatCurrency(value, sym) + (converted ? "" : " \u26A0");
    };
  }, [convert, currencySymbol]);

  return {
    rates: rates ?? {},
    convert,
    formatValue,
    formatPnlValue,
    formatValueDetailed,
    isLoading,
    currency,
    currencySymbol,
  };
}
