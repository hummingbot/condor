import { useMemo } from "react";

import { feeAmount, lpStateStyle, readLpPosition, type LpPosition } from "./lp-position";
import { type ExecutorInfo } from "@/lib/api";
import { formatPnl, formatPriceSig, isExecutorActive, pnlColor } from "@/lib/formatters";

/**
 * Where the price sits inside the range, as a 0–1 fraction, or `null`.
 *
 * The number a CLMM position is actually about: a range you are 95% of the way
 * through is one swap from earning nothing, and no amount of reading two price
 * strings makes that as obvious as a marker does.
 */
function rangeFraction(pos: LpPosition, price: number | null): number | null {
  const { lowerPrice: lo, upperPrice: hi } = pos;
  if (!lo || !hi || hi <= lo || !price || price <= 0) return null;
  return Math.min(1, Math.max(0, (price - lo) / (hi - lo)));
}

interface Props {
  /** The pool's executors, already scoped to this pool by the page. */
  executors: ExecutorInfo[];
  /** Live pool price, when the chart's own mark is fresher than the executor's. */
  currentPrice: number | null;
  selectedExecutorId?: string | null;
  onSelect?: (executorId: string) => void;
}

/**
 * The liquidity you hold in this pool, above its chart.
 *
 * The chart already draws an open position as a box, but a box answers "where"
 * and not "how is it doing" — and only once you have found it among a page of
 * candles. This is the position's own line: whether it is still earning, the
 * bounds it earns between, where the price is inside them, and what it has made.
 *
 * Renders nothing when nothing is open, so a pool you are only looking at keeps
 * its full chart height.
 */
export function LpPositionBar({
  executors,
  currentPrice,
  selectedExecutorId,
  onSelect,
}: Props) {
  const positions = useMemo(() => {
    const open: LpPosition[] = [];
    for (const ex of executors) {
      if (ex.type?.toLowerCase() !== "lp") continue;
      if (!isExecutorActive(ex.status)) continue;
      const pos = readLpPosition(ex);
      if (pos) open.push(pos);
    }
    return open;
  }, [executors]);

  if (!positions.length) return null;

  return (
    <div className="flex shrink-0 flex-wrap items-stretch gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
      {positions.map((pos) => {
        const state = lpStateStyle(pos.state);
        const price = currentPrice && currentPrice > 0 ? currentPrice : pos.currentPrice;
        const fraction = rangeFraction(pos, price);
        const selected = selectedExecutorId === pos.id;
        return (
          <button
            key={pos.id}
            onClick={() => onSelect?.(pos.id)}
            title={`${pos.provider || "LP"} position ${pos.id}`}
            className={`flex flex-1 flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-3 py-1.5 text-left text-[11px] transition-colors ${
              selected
                ? "border-[var(--color-primary)] bg-[var(--color-primary)]/5"
                : "border-[var(--color-border)] bg-[var(--color-bg)] hover:bg-[var(--color-surface-hover)]"
            }`}
          >
            <span
              className="rounded px-1.5 py-0.5 text-[10px] font-medium"
              style={{ color: state.color, backgroundColor: state.bg }}
            >
              {state.label}
            </span>

            {/* Range, with the price's place inside it */}
            <span className="flex items-center gap-1.5 font-mono tabular-nums">
              <span className="text-[var(--color-text-muted)]">{formatPriceSig(pos.lowerPrice)}</span>
              <span className="relative h-1 w-16 overflow-hidden rounded-full bg-[var(--color-border)]">
                {fraction !== null && (
                  <span
                    className="absolute top-1/2 h-2 w-0.5 -translate-y-1/2 rounded-full"
                    style={{
                      left: `calc(${fraction * 100}% - 1px)`,
                      backgroundColor: state.color,
                    }}
                  />
                )}
              </span>
              <span className="text-[var(--color-text-muted)]">{formatPriceSig(pos.upperPrice)}</span>
            </span>

            <span className="ml-auto flex items-center gap-4 font-mono tabular-nums">
              {pos.valueQuote !== null && (
                <span>
                  <span className="mr-1 text-[10px] text-[var(--color-text-muted)]">value</span>
                  {feeAmount(pos.valueQuote)}
                </span>
              )}
              {pos.feesQuote !== null && (
                <span>
                  <span className="mr-1 text-[10px] text-[var(--color-text-muted)]">fees</span>
                  {feeAmount(pos.feesQuote)}
                </span>
              )}
              <span style={{ color: pnlColor(pos.pnl) }}>{formatPnl(pos.pnl)}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
