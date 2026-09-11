import { ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { feeAmount, lpStateStyle } from "./lp-position";
import { useLpPositions } from "@/hooks/useLpPositions";
import { formatPnl, formatUsd, pnlColor } from "@/lib/formatters";

/**
 * The LP positions you already hold, above the pools you might enter.
 *
 * The browser below is for finding a pool; this is for getting back to one. A
 * position lives in exactly one pool, and its executor knows the address, so the
 * shortest path from "how is my SOL-USDC range doing" to that pool's workspace is
 * a row here rather than a search through Trending.
 *
 * Reading the positions belongs to {@link useLpPositions}, which the portfolio's
 * liquidity table shares; this is only the strip's shape for them.
 *
 * Renders nothing at all when there are no open positions: an empty panel above
 * every visit to the pool browser is a permanent cost for an occasional feature.
 */
export function LpPositions({ server }: { server: string }) {
  const navigate = useNavigate();
  const { positions, label, dexId } = useLpPositions(server);

  if (!positions.length) return null;

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Your LP positions
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          {positions.length}
        </span>
      </div>

      <div className="flex gap-2 overflow-x-auto p-3">
        {positions.map((pos) => {
          const state = lpStateStyle(pos.state);
          return (
            <button
              key={pos.id}
              onClick={() => navigate(`/dex/${pos.network}/${pos.poolAddress}`)}
              title={`Open ${label(pos)} on ${pos.provider || dexId(pos) || pos.network}`}
              className="group flex min-w-[13rem] shrink-0 flex-col gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{label(pos)}</span>
                <ArrowRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-colors group-hover:text-[var(--color-primary)]" />
              </div>

              <div className="flex items-center gap-1.5">
                <span
                  className="rounded px-1.5 py-0.5 text-[10px] font-medium"
                  style={{ color: state.color, backgroundColor: state.bg }}
                >
                  {state.label}
                </span>
                <span className="truncate text-[10px] text-[var(--color-text-muted)]">
                  {pos.provider || dexId(pos)}
                </span>
              </div>

              <div className="flex items-baseline justify-between gap-2 text-xs tabular-nums">
                <span>
                  {pos.valueQuote === null ? "—" : formatUsd(pos.valueQuote)}
                </span>
                <span style={{ color: pnlColor(pos.pnl) }}>
                  {formatPnl(pos.pnl)}
                </span>
              </div>

              {pos.feesQuote !== null && (
                <div className="text-[10px] text-[var(--color-text-muted)]">
                  {feeAmount(pos.feesQuote)} fees
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
