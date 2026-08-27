// ── Shared recharts tooltips + series colors for PNL evolution charts ──
// Used by PnlEvolutionChart for both of its panes, so styling and colors stay in
// sync across the two. Every row is named from PNL_SERIES_LABELS — the same
// vocabulary the chart's header legend reads (READ-244) — so a series reads the
// same word wherever the user meets it.

import { formatCurrencyVolume, formatDateTime, pnlColor } from "@/lib/formatters";
import { PNL_SERIES_COLORS, PNL_SERIES_LABELS } from "@/lib/pnl-chart";

interface TooltipProps {
  active?: boolean;
  payload?: Array<{ dataKey: string; value: number }>;
  label?: number;
  symbol: string;
}

interface BottomTooltipProps extends TooltipProps {
  /**
   * How long one volume bar covers — `"5m"`, `"1h"`, `"1d"` … — as chosen by
   * the sampling ladder (PERF-238) and read back off the series.
   *
   * The Volume row used to report a running total, which is self-explanatory.
   * Since READ-245 it reports what was traded in one bucket, and that number is
   * unreadable without its bucket: the same $40k means a busy hour or a dead
   * day. Absent (a series too short to have a spacing) the row just says
   * "Volume" rather than inventing an interval.
   */
  bucket?: string;
}

/** Top chart tooltip: Total / Realized / Unrealized rows. */
export function PnlTooltip({ active, payload, label, symbol }: TooltipProps) {
  if (!active || !payload?.length || !label) return null;
  const byKey: Record<string, number> = {};
  for (const p of payload) byKey[p.dataKey] = p.value;
  const total = byKey.total ?? (byKey.realized ?? 0) + (byKey.unrealized ?? 0);
  const sign = (v: number) => (v >= 0 ? "+" : "");

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm px-2.5 py-2 text-[11px] leading-relaxed shadow-lg min-w-[150px]">
      <div className="text-[var(--color-text-muted)] text-[10px] mb-1">{formatDateTime(label)}</div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">{PNL_SERIES_LABELS.total}</span>
        <span className="font-semibold" style={{ color: pnlColor(total) }}>
          {sign(total)}{formatCurrencyVolume(total, symbol)}
        </span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">{PNL_SERIES_LABELS.realized}</span>
        <span style={{ color: "var(--color-green)" }}>{sign(byKey.realized ?? 0)}{formatCurrencyVolume(byKey.realized ?? 0, symbol)}</span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">{PNL_SERIES_LABELS.unrealized}</span>
        <span style={{ color: PNL_SERIES_COLORS.unrealized }}>{sign(byKey.unrealized ?? 0)}{formatCurrencyVolume(byKey.unrealized ?? 0, symbol)}</span>
      </div>
    </div>
  );
}

/** Bottom chart tooltip: Volume / Position rows. */
export function BottomTooltip({ active, payload, label, symbol, bucket }: BottomTooltipProps) {
  if (!active || !payload?.length || !label) return null;
  const byKey: Record<string, number> = {};
  for (const p of payload) byKey[p.dataKey] = p.value;

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm px-2.5 py-2 text-[11px] leading-relaxed shadow-lg min-w-[130px]">
      <div className="text-[var(--color-text-muted)] text-[10px] mb-1">{formatDateTime(label)}</div>
      {/* The bar's own value — this bucket's trading, not the running total.
          The running total is the header legend's "Traded lifetime" entry, the
          one entry there with no swatch precisely because nothing draws it;
          repeating it here would be the number the pane deliberately stopped
          drawing. */}
      <div className="flex justify-between gap-3">
        <span style={{ color: PNL_SERIES_COLORS.volume }}>
          {PNL_SERIES_LABELS.volumeDelta}
          {bucket ? <span className="text-[var(--color-text-muted)]"> / {bucket}</span> : null}
        </span>
        <span style={{ color: PNL_SERIES_COLORS.volume }}>{formatCurrencyVolume(byKey.volumeDelta ?? 0, symbol)}</span>
      </div>
      {byKey.position !== undefined && byKey.position !== 0 && (
        <div className="flex justify-between gap-3">
          <span style={{ color: PNL_SERIES_COLORS.position }}>{PNL_SERIES_LABELS.position}</span>
          <span style={{ color: PNL_SERIES_COLORS.position }}>{formatCurrencyVolume(byKey.position, symbol)}</span>
        </div>
      )}
    </div>
  );
}
