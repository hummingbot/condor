import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import {
  LP_EXECUTOR_TYPE,
  LP_REFRESH_MS,
  RECENT_LP_EXECUTORS,
  feeAmount,
  lpStateStyle,
  readLpPosition,
  type LpPosition,
} from "./lp-position";
import { api, type PoolSummary } from "@/lib/api";
import { formatPnl, formatUsd, isExecutorActive, pnlColor } from "@/lib/formatters";

/** GeckoTerminal's multi-pool endpoint, which the labels come from, caps here. */
const MAX_LABELLED_POOLS = 30;

/**
 * A pair label for a position, preferring the pool's own.
 *
 * An LP executor's `trading_pair` is `<base_mint>-<quote_symbol>` — the form the
 * executor needs, and unreadable as a label: `So1111…112-USDC` rather than
 * `SOL-USDC`. The pool row has the symbols, so it wins when it resolved.
 */
function labelFor(pos: LpPosition, pool: PoolSummary | undefined): string {
  const base = pool?.base_symbol;
  const quote = pool?.quote_symbol;
  if (base && base !== "???" && quote && quote !== "???") return `${base}-${quote}`;
  if (pool?.name) return pool.name;
  const [poolBase, poolQuote] = pos.pair.split("-");
  if (poolBase && poolBase.length > 12) {
    return `${poolBase.slice(0, 4)}…-${poolQuote ?? ""}`;
  }
  return pos.pair || pos.poolAddress.slice(0, 8);
}

/**
 * The LP positions you already hold, above the pools you might enter.
 *
 * The browser below is for finding a pool; this is for getting back to one. A
 * position lives in exactly one pool, and its executor knows the address, so the
 * shortest path from "how is my SOL-USDC range doing" to that pool's workspace is
 * a row here rather than a search through Trending.
 *
 * Renders nothing at all when there are no open positions: an empty panel above
 * every visit to the pool browser is a permanent cost for an occasional feature.
 */
export function LpPositions({ server }: { server: string }) {
  const navigate = useNavigate();

  const { data: executors = [] } = useQuery({
    queryKey: ["dex-lp-executors", server],
    queryFn: () =>
      api.getExecutors(server, {
        executor_type: LP_EXECUTOR_TYPE,
        limit: RECENT_LP_EXECUTORS,
      }),
    refetchInterval: LP_REFRESH_MS,
    staleTime: LP_REFRESH_MS,
  });

  const positions = useMemo(() => {
    const open: LpPosition[] = [];
    for (const ex of executors) {
      if (!isExecutorActive(ex.status)) continue;
      const pos = readLpPosition(ex);
      if (pos) open.push(pos);
    }
    // Biggest first: the position most worth checking is the one with the most
    // in it, and an unvalued position sorts last rather than to the top.
    open.sort((a, b) => (b.valueQuote ?? -1) - (a.valueQuote ?? -1));
    return open;
  }, [executors]);

  // Every open position is on one chain in practice (Gateway's CLMM connectors
  // are Solana-only), but the labels are fetched per chain so a second one does
  // not silently fall back to mint addresses.
  const byNetwork = useMemo(() => {
    const groups: Record<string, string[]> = {};
    for (const pos of positions.slice(0, MAX_LABELLED_POOLS)) {
      const list = (groups[pos.network] ??= []);
      if (!list.includes(pos.poolAddress)) list.push(pos.poolAddress);
    }
    return groups;
  }, [positions]);

  const { data: pools = {} } = useQuery({
    queryKey: ["dex-lp-pools", server, JSON.stringify(byNetwork)],
    queryFn: async () => {
      const entries = await Promise.all(
        Object.entries(byNetwork).map(([network, addresses]) =>
          api
            .getDexPoolsByAddress(server, network, addresses)
            .then((r) => r.pools)
            .catch(() => [] as PoolSummary[]),
        ),
      );
      const map: Record<string, PoolSummary> = {};
      for (const pool of entries.flat()) map[pool.address] = pool;
      return map;
    },
    enabled: Object.keys(byNetwork).length > 0,
    // A pair label does not change; only the TVL beside it does.
    staleTime: 5 * 60_000,
  });

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
          const pool = pools[pos.poolAddress];
          const state = lpStateStyle(pos.state);
          return (
            <button
              key={pos.id}
              onClick={() => navigate(`/dex/${pos.network}/${pos.poolAddress}`)}
              title={`Open ${labelFor(pos, pool)} on ${pos.provider || pool?.dex_id || pos.network}`}
              className="group flex min-w-[13rem] shrink-0 flex-col gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">
                  {labelFor(pos, pool)}
                </span>
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
                  {pos.provider || pool?.dex_id || ""}
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
