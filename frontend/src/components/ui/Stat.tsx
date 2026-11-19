import type { ReactNode } from "react";

/**
 * A KPI tile — @hummingbot/design-system `statCard` preset: title + icon row,
 * bold value, muted description. Condor adds an optional signed `delta`.
 *
 * The delta's colour comes from its sign, and a delta that rounds to zero at
 * the shown precision is neutral — never a red "-$0.00".
 */
export function Stat({
  label,
  value,
  icon,
  delta,
  formatDelta = (n) => n.toFixed(2),
  sub,
  className = "",
}: {
  label: ReactNode;
  value: ReactNode;
  icon?: ReactNode;
  delta?: number | null;
  formatDelta?: (n: number) => string;
  sub?: ReactNode;
  className?: string;
}) {
  let deltaEl: ReactNode = null;
  if (delta !== undefined && delta !== null && Number.isFinite(delta)) {
    const text = formatDelta(Math.abs(delta));
    const isZero = Number(text.replace(/[^0-9.]/g, "")) === 0;
    const tone = isZero
      ? "text-[var(--color-text-muted)]"
      : delta > 0
        ? "text-[var(--color-green)]"
        : "text-[var(--color-red)]";
    const sign = isZero ? "" : delta > 0 ? "+" : "−";
    deltaEl = (
      <span className={`text-sm font-medium tabular-nums ${tone}`}>
        {sign}
        {text}
      </span>
    );
  }
  return (
    <div className={`min-w-0 ${className}`}>
      <div className="flex items-center justify-between pb-2">
        <span className="truncate text-sm font-medium text-[var(--color-text)]">{label}</span>
        {icon && <span className="text-[var(--color-text-muted)] [&_svg]:h-4 [&_svg]:w-4">{icon}</span>}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="truncate text-2xl font-bold tabular-nums text-[var(--color-text)]">{value}</span>
        {deltaEl}
      </div>
      {sub && <p className="mt-1 truncate text-xs text-[var(--color-text-muted)]">{sub}</p>}
    </div>
  );
}
