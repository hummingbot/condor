import { useCallback, useEffect, useMemo, useState } from "react";

import { MARKET_FAVORITES_KEY } from "@/lib/sessionState";

/**
 * Starred markets, kept in localStorage.
 *
 * `dexFavorites.ts` for CLOB venues: a favourite is `{server, connector, pair}`
 * and nothing else, because the row's price, volume and change have to be as
 * live as any other row in the browser — the star records *what* you are
 * watching, never a copy of how it looked when you clicked it.
 *
 * Scoped to the server, not just the venue. Two servers may both list
 * `binance`, but they are different accounts with different credentials,
 * balances and open positions, so the handful of pairs you keep an eye on while
 * working on one of them is not the set you want offered on the other. A star
 * therefore only ever surfaces on the server it was made on.
 *
 * Per browser, but not a device preference: what you are watching describes the
 * person trading, so the session boundary clears it (see lib/sessionState.ts).
 */
const STORAGE_KEY = MARKET_FAVORITES_KEY;
const EVENT = "condor:market-favorites";

export interface FavoriteMarket {
  server: string;
  connector: string;
  pair: string;
}

/** Same market: BTC-USDT on binance is not BTC-USDT on kucoin, nor on another server. */
function same(a: FavoriteMarket, b: FavoriteMarket): boolean {
  return (
    a.pair === b.pair && a.connector === b.connector && a.server === b.server
  );
}

function readRaw(): unknown[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // A hand-edited or half-written entry is not worth failing the page over.
    return [];
  }
}

function read(): FavoriteMarket[] {
  return readRaw().filter(
    (f): f is FavoriteMarket =>
      !!f &&
      typeof (f as FavoriteMarket).pair === "string" &&
      typeof (f as FavoriteMarket).connector === "string" &&
      typeof (f as FavoriteMarket).server === "string",
  );
}

function write(favorites: FavoriteMarket[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites));
  // Same-tab listeners: `storage` only fires in the *other* tabs.
  window.dispatchEvent(new Event(EVENT));
}

/**
 * Adopt stars written before the key carried a server.
 *
 * They were made on whichever server was selected at the time, and that name is
 * gone — so they are adopted by the first server that reads them, which for a
 * single-server user is the one that made them and for everyone else beats
 * dropping a watch list on upgrade. Idempotent: once adopted there are no
 * legacy rows left, so the second reader on the page is a no-op.
 */
export function adoptLegacyFavorites(server: string) {
  try {
    const isLegacy = (f: unknown) =>
      !!f &&
      typeof (f as FavoriteMarket).pair === "string" &&
      typeof (f as FavoriteMarket).connector === "string" &&
      typeof (f as FavoriteMarket).server !== "string";

    const legacy = readRaw().filter(isLegacy) as FavoriteMarket[];
    if (legacy.length === 0) return;

    const current = read();
    const adopted = legacy
      .map((f) => ({ server, connector: f.connector, pair: f.pair }))
      .filter((a) => !current.some((c) => same(c, a)));
    write([...current, ...adopted]);
  } catch {
    // Storage disabled or full: a migration must not take the page down with it.
  }
}

/**
 * The starred markets on one server, and a toggle — shared by every component
 * that reads them.
 *
 * `server` is required rather than defaulted: a caller that does not know which
 * server it is on cannot say what a star means, and silently falling back to
 * "all of them" is how the cross-server bleed got here in the first place.
 */
export function useMarketFavorites(server: string) {
  const [all, setAll] = useState<FavoriteMarket[]>(read);

  useEffect(() => {
    const sync = () => setAll(read());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  // The hook is the only place that knows a server name, so it is the only
  // place that can say whose the pre-upgrade stars were. Declared *after* the
  // listener above so its write lands as a normal store event — the state
  // arrives through the same door every other update does.
  useEffect(() => {
    if (server) adoptLegacyFavorites(server);
  }, [server]);

  const favorites = useMemo(
    () => all.filter((f) => f.server === server),
    [all, server],
  );

  const toggle = useCallback(
    (market: Omit<FavoriteMarket, "server">) => {
      const entry: FavoriteMarket = {
        server,
        connector: market.connector,
        pair: market.pair,
      };
      const current = read();
      const next = current.some((f) => same(f, entry))
        ? current.filter((f) => !same(f, entry))
        : [...current, entry];
      write(next);
    },
    [server],
  );

  const isFavorite = useCallback(
    (market: Omit<FavoriteMarket, "server">) =>
      favorites.some((f) => f.connector === market.connector && f.pair === market.pair),
    [favorites],
  );

  /**
   * Move one of this server's stars, by its index in `favorites`.
   *
   * The store is one flat array across every server, so the move is applied to
   * the slots this server already occupies rather than to the array as a whole:
   * another server's stars keep both their order and their positions, and a
   * drag over here can never reshuffle a list the user cannot even see.
   */
  const reorder = useCallback(
    (from: number, to: number) => {
      const current = read();
      const slots: number[] = [];
      current.forEach((f, i) => {
        if (f.server === server) slots.push(i);
      });
      const last = slots.length - 1;
      if (from === to || from < 0 || to < 0 || from > last || to > last) return;

      const mine = slots.map((i) => current[i]);
      const [moved] = mine.splice(from, 1);
      mine.splice(to, 0, moved);

      const next = [...current];
      slots.forEach((slot, k) => {
        next[slot] = mine[k];
      });
      write(next);
    },
    [server],
  );

  return { favorites, toggle, isFavorite, reorder };
}
