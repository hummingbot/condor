import { Gauge } from "lucide-react";

import type { DexUpstreamState } from "@/hooks/useDexUpstream";

/**
 * "GeckoTerminal is throttling us", said where the missing data would have been.
 *
 * Everything on the DEX panel — pool rows, TVL, the chart — comes from one per-IP
 * GeckoTerminal budget shared by every viewer and every polling chart, and every
 * failure behind it degrades to an empty list. So the difference between "this
 * chain has no pools" and "ask again in ten seconds" is invisible unless it is
 * said out loud. Renders nothing while the budget is healthy.
 */
export function UpstreamNotice({
  state,
  className = "",
}: {
  state: DexUpstreamState;
  className?: string;
}) {
  if (!state.limited) return null;

  return (
    <div
      role="status"
      className={`flex flex-wrap items-center gap-x-2 gap-y-1 border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs ${className}`}
    >
      <Gauge className="h-3.5 w-3.5 shrink-0 text-[var(--color-yellow)]" />
      <span className="font-medium text-[var(--color-yellow)]">
        Market data is rate limited
      </span>
      <span className="text-[var(--color-text-muted)]">
        GeckoTerminal is throttling Condor, so pools and prices may be missing or
        stale.
        {state.retryIn > 0
          ? ` Retrying in ${state.retryIn}s.`
          : " Retrying shortly."}
      </span>
      {state.budget > 0 && (
        <span className="ml-auto shrink-0 tabular-nums text-[var(--color-text-muted)]">
          {state.requestsLastMinute}/{state.budget} req/min
        </span>
      )}
    </div>
  );
}
