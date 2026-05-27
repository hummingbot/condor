import { useCallback, useSyncExternalStore } from "react";

export type DisplayCurrency = "USDT" | "BTC" | "BRL" | "EUR";

export const CURRENCY_OPTIONS: DisplayCurrency[] = ["USDT", "BTC", "BRL", "EUR"];

export const CURRENCY_SYMBOLS: Record<DisplayCurrency, string> = {
  USDT: "$",
  BTC: "\u20BF",
  BRL: "R$",
  EUR: "\u20AC",
};

const STORAGE_KEY = "condor_display_currency";

function getStoredCurrency(): DisplayCurrency {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && CURRENCY_OPTIONS.includes(stored as DisplayCurrency)) {
    return stored as DisplayCurrency;
  }
  return "USDT";
}

let currentCurrency: DisplayCurrency = getStoredCurrency();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): DisplayCurrency {
  return currentCurrency;
}

export function useDisplayCurrency() {
  const currency = useSyncExternalStore(subscribe, getSnapshot);

  const setCurrency = useCallback((c: DisplayCurrency) => {
    currentCurrency = c;
    localStorage.setItem(STORAGE_KEY, c);
    listeners.forEach((l) => l());
  }, []);

  return {
    currency,
    setCurrency,
    currencySymbol: CURRENCY_SYMBOLS[currency],
  };
}
