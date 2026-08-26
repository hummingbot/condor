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

/**
 * The display currency, read outside React.
 *
 * The chat's page-context block (FEAT-060) formats money at *send* time, from
 * a plain function with no component around it — but it must read the same
 * number the user is looking at, so it reads the same store the hook does.
 */
export function getDisplayCurrency(): DisplayCurrency {
  return currentCurrency;
}
