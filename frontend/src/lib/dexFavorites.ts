import { useCallback, useEffect, useState } from "react";

import { DEX_FAVORITES_KEY } from "@/lib/sessionState";

/**
 * Starred pools, kept in localStorage.
 *
 * A favourite is stored as `{network, address}` and nothing else: the row's TVL,
 * volume and price have to be as live as any other row in the table, so the
 * favourites view re-reads them by address rather than replaying a copy taken
 * whenever the star was clicked.
 *
 * Per browser, but not a device preference: the pools you are watching describe
 * the person trading, so the session boundary clears them (lib/sessionState.ts).
 */
const STORAGE_KEY = DEX_FAVORITES_KEY;

export interface FavoritePool {
  network: string;
  address: string;
}

/** Same pool: the address alone is ambiguous across chains. */
function same(a: FavoritePool, b: FavoritePool): boolean {
  return a.address === b.address && a.network === b.network;
}

function read(): FavoritePool[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (f): f is FavoritePool =>
        !!f && typeof f.address === "string" && typeof f.network === "string",
    );
  } catch {
    // A hand-edited or half-written entry is not worth failing the page over.
    return [];
  }
}

function write(favorites: FavoritePool[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites));
  // Same-tab listeners: `storage` only fires in the *other* tabs.
  window.dispatchEvent(new Event(EVENT));
}

const EVENT = "condor:dex-favorites";

/** The starred pools, and a toggle — shared by every component that reads them. */
export function useDexFavorites() {
  const [favorites, setFavorites] = useState<FavoritePool[]>(read);

  useEffect(() => {
    const sync = () => setFavorites(read());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const toggle = useCallback((pool: FavoritePool) => {
    const current = read();
    const next = current.some((f) => same(f, pool))
      ? current.filter((f) => !same(f, pool))
      : [...current, { network: pool.network, address: pool.address }];
    write(next);
  }, []);

  const isFavorite = useCallback(
    (pool: FavoritePool) => favorites.some((f) => same(f, pool)),
    [favorites],
  );

  return { favorites, toggle, isFavorite };
}
