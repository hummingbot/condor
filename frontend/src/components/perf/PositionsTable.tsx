import { useMemo } from "react";

import { formatCurrencyPnl, formatCurrencyVolume, pnlColor } from "@/lib/formatters";
import {
  formatAmount,
  parseSide,
  positionExtraColumns,
  type PositionRow,
} from "@/lib/perf-positions";

function SideTag({ side }: { side: string }) {
  if (!side) return <span className="text-[var(--color-text-muted)]">—</span>;
  const buy = side.toLowerCase() === "buy";
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase"
      style={{
        color: buy ? "var(--color-green)" : "var(--color-red)",
        background: buy ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
      }}
    >
      {side}
    </span>
  );
}

/**
 * The bottom band's positions occupant: every open position in scope, one grid.
 *
 * Lifted out of `PerfBrowser` (ARCH-300) with its markup unchanged. The
 * Controller column is dropped when the scope already *is* one controller —
 * every row would repeat its name — which is what `showController` says; the
 * browser passes `!activeCtrl`, exactly as the inline gate did.
 */
export function PositionsTable({
  rows,
  currencySymbol,
  showController,
}: {
  rows: PositionRow[];
  currencySymbol: string;
  showController: boolean;
}) {
  const extraColumns = useMemo(() => positionExtraColumns(rows), [rows]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin border-t border-[var(--color-border)]/60">
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-y border-[var(--color-border)]/60 bg-[var(--color-bg)] text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              {showController && <th className="px-3 py-1.5 text-left font-medium">Controller</th>}
              <th className="px-3 py-1.5 text-left font-medium">Connector</th>
              <th className="px-3 py-1.5 text-left font-medium">Pair</th>
              <th className="px-3 py-1.5 text-left font-medium">Side</th>
              <th className="px-3 py-1.5 text-right font-medium">Amount</th>
              <th className="px-3 py-1.5 text-right font-medium">Breakeven</th>
              <th className="px-3 py-1.5 text-right font-medium" title="Amount x breakeven price">
                Notional
              </th>
              {extraColumns.map((k) => (
                <th key={k} className="px-3 py-1.5 text-right font-medium">{k}</th>
              ))}
              <th className="px-3 py-1.5 text-right font-medium">Realized</th>
              <th className="px-3 py-1.5 text-right font-medium">Unrealized</th>
              <th className="px-3 py-1.5 text-right font-medium">Volume</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const extras = Object.fromEntries(row.extras);
              return (
                <tr key={row.id} className="border-b border-[var(--color-border)]/30 last:border-0">
                  {showController && (
                    <td className="px-3 py-1.5 max-w-[220px] truncate" title={row.ctrlLabel}>
                      {row.ctrlLabel}
                    </td>
                  )}
                  <td className="px-3 py-1.5 text-[var(--color-text-muted)]">{row.connector || "—"}</td>
                  <td className="px-3 py-1.5 font-medium">{row.pair || "—"}</td>
                  <td className="px-3 py-1.5"><SideTag side={row.side} /></td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatAmount(row.amount)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatAmount(row.breakeven)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {row.notional === null
                      ? "—"
                      : formatCurrencyVolume(row.notional, currencySymbol)}
                  </td>
                  {extraColumns.map((k) => {
                    const val = extras[k];
                    return (
                      <td key={k} className="px-3 py-1.5 text-right tabular-nums text-[var(--color-text-muted)]">
                        {val === undefined || val === null
                          ? "—"
                          : typeof val === "number"
                            ? formatAmount(val)
                            : parseSide(String(val))}
                      </td>
                    );
                  })}
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium" style={{ color: pnlColor(row.realized) }}>
                    {formatCurrencyPnl(row.realized, currencySymbol)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium" style={{ color: pnlColor(row.unrealized) }}>
                    {formatCurrencyPnl(row.unrealized, currencySymbol)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {formatCurrencyVolume(row.volume, currencySymbol)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
