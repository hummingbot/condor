import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Loader2, Search, X } from "lucide-react";

import { api, type Ticker } from "@/lib/api";
import { formatCompactVolume, formatPrice } from "@/lib/formatters";
import { useTickers } from "./useTickers";

interface MarketsPanelProps {
  server: string;
  connector: string;
  pair: string;
  onSelectPair: (pair: string) => void;
}

type SortKey = "trading_pair" | "price" | "usd_volume";

// Rendering every row of a 1300-pair connector janks the panel; search narrows the rest.
const MAX_ROWS = 150;

export function MarketsPanel({
  server,
  connector,
  pair,
  onSelectPair,
}: MarketsPanelProps) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("usd_volume");
  const [sortAsc, setSortAsc] = useState(false);

  const { tickers, updatedAt, isLoading, isFetching } = useTickers(server, connector);

  // Only offer pairs the connector actually accepts orders for.
  const { data: rulesData } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => api.getTradingRules(server, connector),
    enabled: !!server && !!connector,
    staleTime: 5 * 60 * 1000,
  });

  const tradablePairs = useMemo(() => {
    const rules = rulesData?.rules ?? [];
    return rules.length ? new Set(rules.map((r) => r.trading_pair)) : null;
  }, [rulesData]);

  const rows = useMemo(() => {
    let list: Ticker[] = tradablePairs
      ? tickers.filter((t) => tradablePairs.has(t.trading_pair))
      : tickers;

    if (search) {
      const q = search.toUpperCase();
      list = list.filter((t) => t.trading_pair.toUpperCase().includes(q));
    }

    const dir = sortAsc ? 1 : -1;
    return [...list].sort((a, b) => {
      if (sortKey === "trading_pair") {
        return a.trading_pair.localeCompare(b.trading_pair) * dir;
      }
      if (sortKey === "usd_volume") {
        // Pairs whose quote couldn't be priced stay at the bottom either way —
        // ascending shouldn't fill the top of the list with unknowns.
        const ak = a.usd_volume == null ? 1 : 0;
        const bk = b.usd_volume == null ? 1 : 0;
        if (ak !== bk) return ak - bk;
        return ((a.usd_volume ?? 0) - (b.usd_volume ?? 0)) * dir;
      }
      return (a.price - b.price) * dir;
    });
  }, [tickers, tradablePairs, search, sortKey, sortAsc]);

  const visible = rows.slice(0, MAX_ROWS);

  const toggleSort = (key: SortKey) => {
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
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
        <span className="text-[11px] font-medium text-[var(--color-text)]">
          Markets <span className="text-[var(--color-text-muted)]">— {connector}</span>
        </span>
        <span className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
          {isFetching && <Loader2 className="h-3 w-3 animate-spin" />}
          {updatedAt != null && <TickerAge updatedAt={updatedAt} />}
        </span>
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-1.5">
        <Search className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter markets..."
          className="min-w-0 flex-1 bg-transparent text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
        />
        {search && (
          <button onClick={() => setSearch("")} title="Clear filter" aria-label="Clear filter">
            <X className="h-3.5 w-3.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]" />
          </button>
        )}
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-[1fr_auto_auto] gap-2 border-b border-[var(--color-border)] px-3 py-1 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
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
          className="flex w-16 items-center justify-end gap-1 hover:text-[var(--color-text)]"
        >
          24h Vol {sortIcon("usd_volume")}
        </button>
      </div>

      {/* Rows */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
            Loading markets...
          </p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
            {tickers.length === 0
              ? "No ticker data for this connector"
              : "No markets match the filter"}
          </p>
        ) : (
          visible.map((t) => {
            const [base, quote] = t.trading_pair.split("-");
            const selected = t.trading_pair === pair;
            return (
              <button
                key={t.trading_pair}
                onClick={() => onSelectPair(t.trading_pair)}
                className={`grid w-full grid-cols-[1fr_auto_auto] items-center gap-2 px-3 py-1 text-left text-xs ${
                  selected
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <span className="truncate">
                  <span className="font-medium">{base}</span>
                  <span className="text-[var(--color-text-muted)]">-{quote}</span>
                </span>
                <span className="text-right font-mono tabular-nums">
                  {formatPrice(t.price)}
                </span>
                <span className="w-16 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
                  {t.usd_volume != null ? formatCompactVolume(t.usd_volume) : "—"}
                </span>
              </button>
            );
          })
        )}
        {rows.length > MAX_ROWS && (
          <p className="px-3 py-1.5 text-center text-[10px] text-[var(--color-text-muted)]">
            Showing {MAX_ROWS} of {rows.length} — filter to narrow
          </p>
        )}
      </div>
    </div>
  );
}

/** Seconds since the exchange ticker snapshot was taken. */
function TickerAge({ updatedAt }: { updatedAt: number }) {
  const age = Math.max(0, Math.round(Date.now() / 1000 - updatedAt));
  return <span>⟳ {age < 60 ? `${age}s` : `${Math.round(age / 60)}m`}</span>;
}
