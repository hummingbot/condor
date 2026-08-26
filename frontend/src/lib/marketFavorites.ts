import { useCallback, useEffect, useState } from "react";

import { MARKET_FAVORITES_KEY } from "@/lib/sessionState";

/**
 * Starred markets, kept in localStorage.
 *
 * `dexFavorites.ts` for CLOB venues: a favourite is `{connector, pair}` and
 * nothing else, because the row's price, volume and change have to be as live
 * as any other row in the browser — the star records *what* you are watching,
 * never a copy of how it looked when you clicked it.
 *
 * Per browser, but not a device preference: what you are watching describes the
 * person trading, so the session boundary clears it (see lib/sessionState.ts).
 */
const STORAGE_KEY = MARKET_FAVORITES_KEY;
const EVENT = "condor:market-favorites";

export interface FavoriteMarket {
  connector: string;
  pair: string;
}

/** Same market: BTC-USDT on binance is not BTC-USDT on kucoin. */
function same(a: FavoriteMarket, b: FavoriteMarket): boolean {
  return a.pair === b.pair && a.connector === b.connector;
}

function read(): FavoriteMarket[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (f): f is FavoriteMarket =>
        !!f && typeof f.pair === "string" && typeof f.connector === "string",
    );
  } catch {
    // A hand-edited or half-written entry is not worth failing the page over.
    return [];
  }
}

function write(favorites: FavoriteMarket[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites));
  // Same-tab listeners: `storage` only fires in the *other* tabs.
  window.dispatchEvent(new Event(EVENT));
}

/** The starred markets, and a toggle — shared by every component that reads them. */
export function useMarketFavorites() {
  const [favorites, setFavorites] = useState<FavoriteMarket[]>(read);

  useEffect(() => {
    const sync = () => setFavorites(read());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const toggle = useCallback((market: FavoriteMarket) => {
    const current = read();
    const next = current.some((f) => same(f, market))
      ? current.filter((f) => !same(f, market))
      : [...current, { connector: market.connector, pair: market.pair }];
    write(next);
  }, []);

  const isFavorite = useCallback(
    (market: FavoriteMarket) => favorites.some((f) => same(f, market)),
    [favorites],
  );

  return { favorites, toggle, isFavorite };
}
