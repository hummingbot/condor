// ── Shared recharts tooltips + series colors for PNL evolution charts ──
// Used by AggregatedPnlChart and ControllerPnlChart so styling, colors and the
// Total/Realized/Unrealized and Volume/Position rows stay in sync across both.

import { formatCurrencyVolume, formatDateTime, pnlColor } from "@/lib/formatters";
import { PNL_SERIES_COLORS } from "@/lib/pnl-chart";

interface TooltipProps {
  active?: boolean;
  payload?: Array<{ dataKey: string; value: number }>;
  label?: number;
  symbol: string;
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
        <span className="text-[var(--color-text-muted)]">Total</span>
        <span className="font-semibold" style={{ color: pnlColor(total) }}>
          {sign(total)}{formatCurrencyVolume(total, symbol)}
        </span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">Realized</span>
        <span style={{ color: "var(--color-green)" }}>{sign(byKey.realized ?? 0)}{formatCurrencyVolume(byKey.realized ?? 0, symbol)}</span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-[var(--color-text-muted)]">Unrealized</span>
        <span style={{ color: PNL_SERIES_COLORS.unrealized }}>{sign(byKey.unrealized ?? 0)}{formatCurrencyVolume(byKey.unrealized ?? 0, symbol)}</span>
      </div>
    </div>
  );
}

/** Bottom chart tooltip: Volume / Position rows. */
export function BottomTooltip({ active, payload, label, symbol }: TooltipProps) {
  if (!active || !payload?.length || !label) return null;
  const byKey: Record<string, number> = {};
  for (const p of payload) byKey[p.dataKey] = p.value;

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm px-2.5 py-2 text-[11px] leading-relaxed shadow-lg min-w-[130px]">
      <div className="text-[var(--color-text-muted)] text-[10px] mb-1">{formatDateTime(label)}</div>
      <div className="flex justify-between gap-3">
        <span style={{ color: PNL_SERIES_COLORS.volume }}>Volume</span>
        <span style={{ color: PNL_SERIES_COLORS.volume }}>{formatCurrencyVolume(byKey.volume ?? 0, symbol)}</span>
      </div>
      {byKey.position !== undefined && byKey.position !== 0 && (
        <div className="flex justify-between gap-3">
          <span style={{ color: PNL_SERIES_COLORS.position }}>Position</span>
          <span style={{ color: PNL_SERIES_COLORS.position }}>{formatCurrencyVolume(byKey.position, symbol)}</span>
        </div>
      )}
    </div>
  );
}
