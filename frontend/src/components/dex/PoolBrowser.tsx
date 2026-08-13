import { ChevronDown, ChevronUp } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { PoolSummary } from "@/lib/api";

type SortKey =
  | "name"
  | "dex_id"
  | "reserve_usd"
  | "volume_24h"
  | "price_change_24h"
  | "apr";
type SortDir = "asc" | "desc";

function num(v: unknown): number {
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? n : 0;
}

function usd(v: unknown): string {
  const n = num(v);
  if (!n) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(2)}%`;
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
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sortKey === col;
  return (
    <th
      onClick={() => onSort(col)}
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
  /** True when Gateway supplied the rows, so APR and bin step have values. */
  showGatewayColumns: boolean;
}

/**
 * The pool table. A pool Gateway cannot reach is rendered greyed with the reason
 * rather than hidden: a chain GeckoTerminal indexes but Condor does not trade on
 * is a fact worth seeing, not a row to silently drop.
 */
export function PoolBrowser({
  pools,
  isLoading,
  emptyMessage,
  showGatewayColumns,
}: Props) {
  const navigate = useNavigate();
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
      else cmp = num(a[sortKey]) - num(b[sortKey]);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [pools, sortKey, sortDir]);

  if (isLoading) {
    return (
      <div className="px-4 py-10 text-center text-sm text-[var(--color-text-muted)]">
        Loading pools…
      </div>
    );
  }

  if (!sorted.length) {
    return (
      <div className="px-4 py-10 text-center text-sm text-[var(--color-text-muted)]">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
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
                <SortTh
                  label="APR"
                  col="apr"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                  align="right"
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
            const change = p.price_change_24h;
            return (
              <tr
                key={`${p.dex_id}:${p.address}`}
                onClick={() =>
                  p.tradable &&
                  navigate(`/dex/${p.gateway_network}/${p.address}`)
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
                    change === null || change === undefined
                      ? "text-[var(--color-text-muted)]"
                      : change >= 0
                        ? "text-[var(--color-green)]"
                        : "text-[var(--color-red)]"
                  }`}
                >
                  {pct(change)}
                </td>
                {showGatewayColumns && (
                  <>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {pct(p.apr)}
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
  );
}
