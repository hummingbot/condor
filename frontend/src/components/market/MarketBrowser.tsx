import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Loader2, Search, Star, X } from "lucide-react";

import { formatConnectorName } from "@/lib/formatters";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api, type Ticker } from "@/lib/api";
import { formatCompactVolume, formatPrice } from "@/lib/formatters";
import {
  changeColumnLabel,
  changeWindowTitle,
  formatChange,
} from "@/lib/marketChange";
import { useMarketFavorites } from "@/lib/marketFavorites";
import { useTickers } from "./useTickers";
import { VenueRail } from "./VenueRail";

export interface MarketPick {
  connector: string;
  pair: string;
}

interface MarketBrowserProps {
  server: string;
  /**
   * The trade surface's venue: what the rail is seeded with, and what the
   * highlighted row is judged against. Not what the table lists — that is the
   * rail's `scope` below, which starts here and then goes wherever the user
   * browses without moving the chart.
   */
  connector: string;
  /** The trade surface's pair, highlighted in the list. */
  pair: string;
  /** Every venue the panel offers, credentialed first. */
  connectors: string[];
  /** Which of them the account can execute on; undefined until that is known. */
  credentialed?: ReadonlySet<string>;
  onPick: (market: MarketPick) => void;
  onClose: () => void;
}

type SortKey = "trading_pair" | "price" | "usd_volume" | "change_pct";

// No virtualisation: 300 rows in the DOM is fine, and the search narrows the
// rest. The footer says how many of how many, so the cap is never silent.
const MAX_ROWS = 300;

const ALL_QUOTES = "";

/** The quotes worth leading with, in the order a trader looks for them. */
const QUOTE_PRIORITY = ["USDT", "USDC", "BTC", "ETH"];

export function MarketBrowser({
  server,
  connector,
  pair,
  connectors,
  credentialed,
  onPick,
  onClose,
}: MarketBrowserProps) {
  const [search, setSearch] = useState("");
  const [quote, setQuote] = useState(ALL_QUOTES);
  const [sortKey, setSortKey] = useState<SortKey>("usd_volume");
  const [sortAsc, setSortAsc] = useState(false);
  const [favoritesOnly, setFavoritesOnly] = useState(false);

  // Which venue the table lists. Seeded from the trade surface and then owned
  // by the rail — browsing another venue must not move the chart, so nothing is
  // dispatched upwards until a row is picked, and the pick carries this venue.
  // Stored with the venue it was seeded from, so an outside change (a favourite
  // chip on another venue) re-seeds it instead of stranding the list on a venue
  // the surface has left.
  const [scope, setScope] = useState({ from: connector, venue: connector });
  const venue = scope.from === connector ? scope.venue : connector;
  const browseVenue = useCallback(
    (next: string) => setScope({ from: connector, venue: next }),
    [connector],
  );

  // The keyboard cursor belongs to the venue it was moved in. A venue switch is
  // a whole new list, so the cursor simply stops matching and reads 0 again — no
  // effect, and no render where Enter points into the old venue.
  const [cursorAt, setCursorAt] = useState({ venue, index: 0 });
  const activeIndex = cursorAt.venue === venue ? cursorAt.index : 0;
  const setActiveIndex = useCallback(
    (next: number | ((i: number) => number)) =>
      setCursorAt((c) => {
        const base = c.venue === venue ? c.index : 0;
        return {
          venue,
          index: typeof next === "function" ? next(base) : next,
        };
      }),
    [venue],
  );

  const listRef = useRef<HTMLDivElement>(null);

  // Esc and the header's X close it; the market chip that opened it toggles it
  // shut. No outside-click dismiss — the config panel stays usable while the
  // list is up, and a click on it is not a dismissal.
  useEscapeKey(true, onClose);

  const { tickers, isLoading, isFetching } = useTickers(server, venue);
  const { toggle, isFavorite } = useMarketFavorites(server);

  // Only offer pairs the venue actually accepts orders for.
  const { data: rulesData } = useQuery({
    queryKey: ["trading-rules", server, venue],
    queryFn: () => api.getTradingRules(server, venue),
    enabled: !!server && !!venue,
    staleTime: 5 * 60 * 1000,
  });

  const tradablePairs = useMemo(() => {
    const rules = rulesData?.rules ?? [];
    return rules.length ? new Set(rules.map((r) => r.trading_pair)) : null;
  }, [rulesData]);

  // Everything this venue offers, before any of the panel's own filters. The
  // quote list is derived from here rather than from `rows` so that picking a
  // quote never removes the other quotes from the control that picked it.
  const tradable = useMemo(
    () =>
      tradablePairs
        ? tickers.filter((t) => tradablePairs.has(t.trading_pair))
        : tickers,
    [tickers, tradablePairs],
  );

  const quotes = useMemo(() => {
    const seen = new Set<string>();
    for (const t of tradable) {
      const parts = t.trading_pair.split("-");
      if (parts.length > 1) seen.add(parts[parts.length - 1]);
    }
    return [...seen].sort((a, b) => {
      const ai = QUOTE_PRIORITY.indexOf(a);
      const bi = QUOTE_PRIORITY.indexOf(b);
      if (ai !== -1 && bi !== -1) return ai - bi;
      if (ai !== -1) return -1;
      if (bi !== -1) return 1;
      return a.localeCompare(b);
    });
  }, [tradable]);

  const rows = useMemo(() => {
    let list: Ticker[] = tradable;

    if (quote) {
      list = list.filter((t) => {
        const parts = t.trading_pair.split("-");
        return parts.length > 1 && parts[parts.length - 1] === quote;
      });
    }
    if (favoritesOnly) {
      list = list.filter((t) => isFavorite({ connector: venue, pair: t.trading_pair }));
    }
    if (search) {
      const q = search.toUpperCase();
      list = list.filter((t) => t.trading_pair.toUpperCase().includes(q));
    }

    const dir = sortAsc ? 1 : -1;
    const sorted = [...list].sort((a, b) => {
      if (sortKey === "trading_pair") {
        return a.trading_pair.localeCompare(b.trading_pair) * dir;
      }
      if (sortKey === "price") return (a.price - b.price) * dir;
      // Rows with nothing to show stay at the bottom either way — ascending
      // shouldn't fill the top of the list with unknowns.
      const av = sortKey === "usd_volume" ? a.usd_volume : a.change_pct;
      const bv = sortKey === "usd_volume" ? b.usd_volume : b.change_pct;
      const ak = av == null ? 1 : 0;
      const bk = bv == null ? 1 : 0;
      if (ak !== bk) return ak - bk;
      return ((av ?? 0) - (bv ?? 0)) * dir;
    });

    // Starred markets ride on top of whatever the sort decided.
    const starred: Ticker[] = [];
    const rest: Ticker[] = [];
    for (const t of sorted) {
      (isFavorite({ connector: venue, pair: t.trading_pair }) ? starred : rest).push(t);
    }
    return [...starred, ...rest];
  }, [
    tradable,
    quote,
    search,
    sortKey,
    sortAsc,
    favoritesOnly,
    isFavorite,
    venue,
  ]);

  const visible = rows.slice(0, MAX_ROWS);
  // A list that shrank under the cursor (a slower query resolving, a star being
  // removed under a filter) must not leave Enter pointing at nothing.
  const cursor = Math.min(activeIndex, Math.max(0, visible.length - 1));

  // The label comes from the widest window on screen: a pair listed three hours
  // ago is the exception, not the column's promise.
  const windowS = useMemo(() => {
    let max: number | null = null;
    for (const t of visible) {
      if (t.change_window_s != null && (max == null || t.change_window_s > max)) {
        max = t.change_window_s;
      }
    }
    return max;
  }, [visible]);

  useEffect(() => {
    const items = listRef.current?.querySelectorAll("[data-market-row]");
    items?.[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  // The venue and the pair leave together, in one call: that is what spares the
  // page a render on a market nobody asked for, back when changing venue in the
  // top bar reset the pair to the new venue's default before you had picked one.
  const pick = (t: Ticker) => onPick({ connector: venue, pair: t.trading_pair });

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, visible.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && visible[cursor]) {
      e.preventDefault();
      pick(visible[cursor]);
    }
  };

  // Every control that reorders or re-scopes the list rewinds the keyboard
  // cursor with it — done at the event rather than in an effect, so a pick is
  // never one render behind what is on screen.
  const toggleSort = (key: SortKey) => {
    setActiveIndex(0);
    if (key === sortKey) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      // Text reads best A→Z; numbers read best highest-first.
      setSortAsc(key === "trading_pair");
    }
  };

  const sortIcon = (key: SortKey) =>
    key === sortKey ? (
      sortAsc ? (
        <ArrowUp className="h-3 w-3" />
      ) : (
        <ArrowDown className="h-3 w-3" />
      )
    ) : null;

  return (
    <div
      role="dialog"
      aria-label="Browse markets"
      onKeyDown={handleKeyDown}
      className="absolute left-0 top-0 z-40 flex w-full max-w-3xl max-h-[min(34rem,70vh)] rounded-br-lg border-b border-r border-[var(--color-border)] bg-[var(--color-bg)] shadow-lg"
    >
      {/* The venue axis. Re-lists the table; never moves the chart. */}
      <VenueRail
        connectors={connectors}
        credentialed={credentialed}
        value={venue}
        current={connector}
        onChange={(v) => {
          browseVenue(v);
          // A quote that exists on Binance may not exist on the venue you moved
          // to; a filter that survives the move would show an empty table and
          // no reason why.
          setQuote(ALL_QUOTES);
        }}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header: search, quote filter, favourites filter, close. The venue is
            named by the search placeholder rather than a chip of its own — the
            rail on the left is the control that changes it. */}
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] pl-3 pr-2">
          <Search className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
          <input
            autoFocus
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setActiveIndex(0);
            }}
            placeholder={`Search ${formatConnectorName(venue)} markets...`}
            aria-label={`Search ${formatConnectorName(venue)} markets`}
            className="min-w-0 flex-1 bg-transparent py-2.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
          />

          {/* What the pair dropdown used to say by grouping. A filter rather
              than a grouping, because this list also sorts — quote runs and a
              volume sort cannot both own the row order. */}
          {quotes.length > 1 && (
            <select
              value={quote}
              onChange={(e) => {
                setQuote(e.target.value);
                setActiveIndex(0);
              }}
              aria-label="Filter by quote asset"
              title="Filter by quote asset"
              className="shrink-0 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-1.5 py-1 text-[11px] text-[var(--color-text)] focus:outline-none"
            >
              <option value={ALL_QUOTES}>All quotes</option>
              {quotes.map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          )}

          <button
            onClick={() => {
              setFavoritesOnly((v) => !v);
              setActiveIndex(0);
            }}
            aria-pressed={favoritesOnly}
            title="Show starred markets only"
            className={`flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[11px] transition-colors ${
              favoritesOnly
                ? "border-[var(--color-yellow)] text-[var(--color-yellow)]"
                : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            <Star className={`h-3 w-3 ${favoritesOnly ? "fill-current" : ""}`} />
            Starred
          </button>

          {isFetching && (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--color-text-muted)]" />
          )}

          <button
            onClick={onClose}
            title="Close (Esc)"
            aria-label="Close market browser"
            className="shrink-0 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Column headers */}
        <div className="grid grid-cols-[1.5rem_1fr_8rem_7rem_6rem] items-center gap-2 border-b border-[var(--color-border)] px-3 py-1.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
          <span />
          <button
            onClick={() => toggleSort("trading_pair")}
            className="flex items-center gap-1 hover:text-[var(--color-text)]"
          >
            Pair {sortIcon("trading_pair")}
          </button>
          <button
            onClick={() => toggleSort("price")}
            className="flex items-center justify-end gap-1 hover:text-[var(--color-text)]"
          >
            Price {sortIcon("price")}
          </button>
          <button
            onClick={() => toggleSort("usd_volume")}
            className="flex items-center justify-end gap-1 hover:text-[var(--color-text)]"
          >
            24h Vol {sortIcon("usd_volume")}
          </button>
          <button
            onClick={() => toggleSort("change_pct")}
            title={changeWindowTitle(windowS)}
            className="flex items-center justify-end gap-1 hover:text-[var(--color-text)]"
          >
            {changeColumnLabel(windowS)} {sortIcon("change_pct")}
          </button>
        </div>

        {/* Rows */}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {isLoading ? (
            <p className="px-3 py-8 text-center text-xs text-[var(--color-text-muted)]">
              Loading markets...
            </p>
          ) : visible.length === 0 ? (
            <p className="px-3 py-8 text-center text-xs text-[var(--color-text-muted)]">
              {tickers.length === 0
                ? "No ticker data for this venue"
                : favoritesOnly
                  ? "No starred markets on this venue"
                  : "No markets match the search"}
            </p>
          ) : (
            visible.map((t, i) => {
              const [base, quoteAsset] = t.trading_pair.split("-");
              // Only "the market you are on" when both halves match: the same
              // pair on the venue you are browsing is a different market.
              const selected = t.trading_pair === pair && venue === connector;
              const starred = isFavorite({ connector: venue, pair: t.trading_pair });
              return (
                <div
                  key={t.trading_pair}
                  data-market-row
                  className={`grid grid-cols-[1.5rem_1fr_8rem_7rem_6rem] items-center gap-2 px-3 text-xs ${
                    i === cursor
                      ? "bg-[var(--color-primary)]/10"
                      : "hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <button
                    onClick={() => toggle({ connector: venue, pair: t.trading_pair })}
                    aria-pressed={starred}
                    aria-label={`${starred ? "Unstar" : "Star"} ${t.trading_pair}`}
                    className="flex h-6 w-6 items-center justify-center"
                  >
                    <Star
                      className={`h-3.5 w-3.5 ${
                        starred
                          ? "fill-[var(--color-yellow)] text-[var(--color-yellow)]"
                          : "text-[var(--color-text-muted)]/40 hover:text-[var(--color-text-muted)]"
                      }`}
                    />
                  </button>
                  <button
                    onClick={() => pick(t)}
                    className={`truncate py-1.5 text-left ${
                      selected ? "text-[var(--color-primary)]" : "text-[var(--color-text)]"
                    }`}
                  >
                    <span className="font-medium">{base}</span>
                    <span className="text-[var(--color-text-muted)]">-{quoteAsset}</span>
                  </button>
                  <button
                    onClick={() => pick(t)}
                    className="py-1.5 text-right font-mono tabular-nums text-[var(--color-text)]"
                  >
                    {formatPrice(t.price)}
                  </button>
                  <button
                    onClick={() => pick(t)}
                    className="py-1.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]"
                  >
                    {t.usd_volume != null ? formatCompactVolume(t.usd_volume) : "—"}
                  </button>
                  <button
                    onClick={() => pick(t)}
                    title={
                      t.change_window_s != null
                        ? changeWindowTitle(t.change_window_s)
                        : undefined
                    }
                    className={`py-1.5 text-right font-mono tabular-nums ${
                      t.change_pct == null
                        ? "text-[var(--color-text-muted)]"
                        : t.change_pct >= 0
                          ? "text-[var(--color-green)]"
                          : "text-[var(--color-red)]"
                    }`}
                  >
                    {t.change_pct == null ? "—" : formatChange(t.change_pct)}
                  </button>
                </div>
              );
            })
          )}
        </div>

        {/* Footer: how many of how many */}
        <div className="border-t border-[var(--color-border)] px-3 py-1.5 text-center text-[10px] text-[var(--color-text-muted)]">
          Showing {visible.length.toLocaleString()} of {rows.length.toLocaleString()}
          {rows.length > MAX_ROWS && " — refine your search"}
        </div>
      </div>
    </div>
  );
}
