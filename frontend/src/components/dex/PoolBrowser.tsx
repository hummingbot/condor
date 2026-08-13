import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Star,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { PoolSummary } from "@/lib/api";
import { useDexFavorites } from "@/lib/dexFavorites";

type SortKey =
  | "name"
  | "dex_id"
  | "reserve_usd"
  | "volume_24h"
  | "price_change_24h"
  | "apr"
  | "apy";
type SortDir = "asc" | "desc";

/**
 * Both upstreams answer numbers as strings (and GeckoTerminal answers pandas NaN
 * for a missing figure). The backend settles that before it serializes, so this
 * is the second line of defence rather than the first — but it is the one that
 * decides whether a stray string blanks one cell or takes down the whole page.
 */
function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
}

function usd(v: unknown): string {
  const n = num(v);
  if (!n) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

function pct(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(0)}K%`;
  return `${n.toFixed(2)}%`;
}

/** Sorting compares numbers, and a missing figure sorts last in either direction. */
function sortValue(p: PoolSummary, key: SortKey): number {
  return num(p[key as keyof PoolSummary]) ?? -Infinity;
}

function pairLabel(p: PoolSummary): string {
  const base = p.base_symbol && p.base_symbol !== "???" ? p.base_symbol : null;
  const quote =
    p.quote_symbol && p.quote_symbol !== "???" ? p.quote_symbol : null;
  return base && quote ? `${base}-${quote}` : p.name || p.address.slice(0, 8);
}

function SortTh({
  label,
  col,
  sortKey,
  sortDir,
  onSort,
  align = "left",
  title,
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
  align?: "left" | "right";
  title?: string;
}) {
  const active = sortKey === col;
  return (
    <th
      onClick={() => onSort(col)}
      title={title}
      className={`cursor-pointer select-none px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)] ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      <div
        className={`flex items-center gap-1 ${align === "right" ? "justify-end" : ""}`}
      >
        {label}
        {active &&
          (sortDir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          ))}
      </div>
    </th>
  );
}

interface Props {
  pools: PoolSummary[];
  isLoading: boolean;
  /** Shown in place of rows when there are none — the empty state, not an error. */
  emptyMessage: string;
  /** True when Gateway supplied the rows, so fee/APY and bin step have values. */
  showGatewayColumns: boolean;
  /** The chain the rows belong to, for starring a row by (network, address). */
  network: string;
  page: number;
  hasMore: boolean;
  onPageChange: (page: number) => void;
}

/**
 * The pool table. A pool Gateway cannot reach is rendered greyed with the reason
 * rather than hidden: a chain GeckoTerminal indexes but Condor does not trade on
 * is a fact worth seeing, not a row to silently drop.
 *
 * Sorting is page-local, because paging is the upstream's: what the columns
 * reorder is the screenful in front of you, not the whole listing.
 */
export function PoolBrowser({
  pools,
  isLoading,
  emptyMessage,
  showGatewayColumns,
  network,
  page,
  hasMore,
  onPageChange,
}: Props) {
  const navigate = useNavigate();
  const { toggle, isFavorite } = useDexFavorites();
  const [sortKey, setSortKey] = useState<SortKey>("reserve_usd");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sorted = useMemo(() => {
    const rows = [...pools];
    rows.sort((a, b) => {
      let cmp: number;
      if (sortKey === "name") cmp = pairLabel(a).localeCompare(pairLabel(b));
      else if (sortKey === "dex_id") cmp = a.dex_id.localeCompare(b.dex_id);
      else cmp = sortValue(a, sortKey) - sortValue(b, sortKey);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [pools, sortKey, sortDir]);

  // Only when there is somewhere to go: a lone disabled "Page 1" is noise, and
  // the favourites view has no pages at all.
  const pager =
    !hasMore && page <= 1 ? null : (
      <div className="flex items-center justify-between border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-muted)]">
        <span>Page {page}</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            className="flex items-center gap-1 rounded-md border border-[var(--color-border)] px-2 py-1 transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft className="h-3 w-3" />
            Prev
          </button>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={!hasMore}
            className="flex items-center gap-1 rounded-md border border-[var(--color-border)] px-2 py-1 transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
            <ChevronRight className="h-3 w-3" />
          </button>
        </div>
      </div>
    );

  if (isLoading) {
    return (
      <div className="px-4 py-10 text-center text-sm text-[var(--color-text-muted)]">
        Loading pools…
      </div>
    );
  }

  if (!sorted.length) {
    return (
      <>
        <div className="px-4 py-10 text-center text-sm text-[var(--color-text-muted)]">
          {emptyMessage}
        </div>
        {page > 1 && pager}
      </>
    );
  }

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
              <th className="w-8 px-2 py-3" />
              <SortTh
                label="Pair"
                col="name"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
              />
              <SortTh
                label="DEX"
                col="dex_id"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
              />
              <SortTh
                label="TVL"
                col="reserve_usd"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                align="right"
              />
              <SortTh
                label="Vol 24h"
                col="volume_24h"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                align="right"
              />
              <SortTh
                label="24h"
                col="price_change_24h"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                align="right"
              />
              {showGatewayColumns && (
                <>
                  {/* Meteora's `apr` is the day's fees over TVL, not an annual
                      rate — labelling it APR next to a 170% APY reads as broken. */}
                  <SortTh
                    label="Fee/TVL"
                    col="apr"
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                    align="right"
                    title="Last 24h of fees over TVL, as the connector reports it"
                  />
                  <SortTh
                    label="APY"
                    col="apy"
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                    align="right"
                    title="The connector's annualized yield"
                  />
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                    Bin
                  </th>
                </>
              )}
              <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                LP
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => {
              const change = num(p.price_change_24h);
              // A row from Gateway carries `solana`, not the Gateway network id.
              const poolNetwork = p.gateway_network || network;
              const starred = isFavorite({
                network: poolNetwork,
                address: p.address,
              });
              return (
                <tr
                  key={`${p.dex_id}:${p.address}`}
                  onClick={() =>
                    p.tradable && navigate(`/dex/${poolNetwork}/${p.address}`)
                  }
                  title={
                    p.tradable
                      ? undefined
                      : "Gateway has no connector for this chain"
                  }
                  className={`border-b border-[var(--color-border)] transition-colors ${
                    p.tradable
                      ? "cursor-pointer hover:bg-[var(--color-surface-hover)]"
                      : "opacity-40"
                  }`}
                >
                  <td className="px-2 py-2.5">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle({ network: poolNetwork, address: p.address });
                      }}
                      title={
                        starred ? "Remove from favorites" : "Add to favorites"
                      }
                      className="flex items-center justify-center p-1 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
                    >
                      <Star
                        className={`h-3.5 w-3.5 ${
                          starred
                            ? "fill-[var(--color-primary)] text-[var(--color-primary)]"
                            : ""
                        }`}
                      />
                    </button>
                  </td>
                  <td className="px-4 py-2.5 font-medium">
                    {pairLabel(p)}
                    {!p.tradable && (
                      <span className="ml-2 text-[10px] font-normal text-[var(--color-text-muted)]">
                        not on Gateway
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-[var(--color-text-muted)]">
                    {p.dex_id}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {usd(p.reserve_usd)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {usd(p.volume_24h)}
                  </td>
                  <td
                    className={`px-4 py-2.5 text-right tabular-nums ${
                      change === null
                        ? "text-[var(--color-text-muted)]"
                        : change >= 0
                          ? "text-[var(--color-green)]"
                          : "text-[var(--color-red)]"
                    }`}
                  >
                    {pct(p.price_change_24h)}
                  </td>
                  {showGatewayColumns && (
                    <>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {pct(p.apr)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {pct(p.apy)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-[var(--color-text-muted)]">
                        {p.bin_step ?? "—"}
                      </td>
                    </>
                  )}
                  <td className="px-4 py-2.5 text-right">
                    {p.lp_supported ? (
                      <span className="rounded border border-[var(--color-primary)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-primary)]">
                        LP
                      </span>
                    ) : (
                      <span className="text-[10px] text-[var(--color-text-muted)]">
                        swap only
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pager}
    </>
  );
}
