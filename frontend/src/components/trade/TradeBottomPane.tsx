import { useState, useEffect, useCallback } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronUp, ChevronDown, Loader2, Square } from "lucide-react";

import { api, type ExecutorInfo, type ConsolidatedPosition } from "@/lib/api";
import { useServer } from "@/hooks/useServer";

interface TradeBottomPaneProps {
  executors: ExecutorInfo[];
  positions: ConsolidatedPosition[];
  isLoadingPositions: boolean;
}

const STORAGE_KEY = "condor_trade_bottom_pane";

function formatAge(timestamp: number): string {
  const now = Date.now() / 1000;
  const ts = timestamp > 1e12 ? timestamp / 1000 : timestamp;
  const diff = Math.max(0, now - ts);
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

function formatPnl(pnl: number): string {
  const sign = pnl >= 0 ? "+" : "";
  if (Math.abs(pnl) >= 1000) return `${sign}$${(pnl / 1000).toFixed(1)}K`;
  return `${sign}$${pnl.toFixed(2)}`;
}

function formatPct(pct: number): string {
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${(pct * 100).toFixed(2)}%`;
}

function formatPrice(price: number): string {
  if (price === 0) return "—";
  if (Math.abs(price) >= 1000) return price.toFixed(2);
  if (Math.abs(price) >= 1) return price.toFixed(4);
  return price.toPrecision(6);
}

const isActive = (status: string) => {
  const s = status?.toLowerCase() ?? "";
  return s === "running" || s === "active_position" || s === "active";
};

const STRATEGY_LABELS: Record<string, string> = {
  LIMIT: "Limit Order",
  LIMIT_CHASER: "Limit Chaser",
  MARKET: "Market Order",
  TWAP: "TWAP",
};

function executorTypeLabel(ex: ExecutorInfo): string {
  const strategy = String(ex.config?.execution_strategy ?? "").toUpperCase();
  if (strategy && STRATEGY_LABELS[strategy]) return STRATEGY_LABELS[strategy];
  // Fallback: capitalize the executor type
  return ex.type ? ex.type.charAt(0).toUpperCase() + ex.type.slice(1) : "—";
}

export function TradeBottomPane({ executors, positions, isLoadingPositions }: TradeBottomPaneProps) {
  const { server } = useServer();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) !== "0"; } catch { return true; }
  });
  const [tab, setTab] = useState<"executors" | "positions">("executors");
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [confirmStopId, setConfirmStopId] = useState<string | null>(null);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0"); } catch { /* ok */ }
  }, [expanded]);

  const stopMutation = useMutation({
    mutationFn: (id: string) => {
      setStoppingIds((prev) => new Set([...prev, id]));
      return api.stopExecutor(server!, id, false);
    },
    onSettled: (_data, _error, id) => {
      setStoppingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setConfirmStopId(null);
      queryClient.invalidateQueries({ queryKey: ["executors-infinite", server] });
    },
  });

  const handleStop = useCallback((id: string) => {
    setConfirmStopId(id);
  }, []);

  const activeCount = executors.filter((e) => isActive(e.status)).length;
  const summary = `${executors.length} executor${executors.length !== 1 ? "s" : ""}${activeCount > 0 ? ` (${activeCount} active)` : ""} · ${positions.length} position${positions.length !== 1 ? "s" : ""}`;

  const sortedExecutors = [...executors].sort((a, b) => b.timestamp - a.timestamp);

  return (
    <div className="border-t border-[var(--color-border)] bg-[var(--color-surface)]">
      {/* Toggle handle */}
      <button
        onClick={() => setExpanded((p) => !p)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-[10px] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <span>{summary}</span>
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />}
      </button>

      {expanded && (
        <div>
          {/* Tabs */}
          <div className="flex border-t border-[var(--color-border)]">
            <button
              onClick={() => setTab("executors")}
              className={`flex-1 px-3 py-1.5 text-[11px] font-medium transition-colors ${
                tab === "executors"
                  ? "border-b-2 border-[var(--color-primary)] text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              Executors ({executors.length})
            </button>
            <button
              onClick={() => setTab("positions")}
              className={`flex-1 px-3 py-1.5 text-[11px] font-medium transition-colors ${
                tab === "positions"
                  ? "border-b-2 border-[var(--color-primary)] text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              Positions ({positions.length})
              {isLoadingPositions && <Loader2 className="ml-1 inline h-3 w-3 animate-spin" />}
            </button>
          </div>

          {/* Content */}
          <div className="max-h-[200px] overflow-y-auto">
            {tab === "executors" && (
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b border-[var(--color-border)] text-left text-[10px] text-[var(--color-text-muted)]">
                    <th className="px-2 py-1 font-medium">ID</th>
                    <th className="px-2 py-1 font-medium">Type</th>
                    <th className="px-2 py-1 font-medium">Side</th>
                    <th className="px-2 py-1 font-medium">Status</th>
                    <th className="px-2 py-1 font-medium">Close</th>
                    <th className="px-2 py-1 font-medium text-right">PnL</th>
                    <th className="px-2 py-1 font-medium text-right">PnL %</th>
                    <th className="px-2 py-1 font-medium text-right">Age</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {sortedExecutors.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="px-2 py-3 text-center text-[var(--color-text-muted)]">
                        No executors for this pair
                      </td>
                    </tr>
                  ) : (
                    sortedExecutors.map((ex) => {
                      const active = isActive(ex.status);
                      const stopping = stoppingIds.has(ex.id);
                      const side = ex.side?.toUpperCase();
                      const isBuy = side === "BUY";
                      return (
                        <tr key={ex.id} className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-surface-hover)]">
                          <td className="px-2 py-1 font-mono text-[var(--color-text-muted)]">
                            {ex.id.slice(0, 8)}
                          </td>
                          <td className="px-2 py-1">{executorTypeLabel(ex)}</td>
                          <td className="px-2 py-1">
                            <span className={`rounded px-1 py-0.5 text-[9px] font-bold ${
                              isBuy
                                ? "bg-[var(--color-green)]/20 text-[var(--color-green)]"
                                : "bg-[var(--color-red)]/20 text-[var(--color-red)]"
                            }`}>
                              {isBuy ? "LONG" : "SHORT"}
                            </span>
                          </td>
                          <td className="px-2 py-1">
                            <span className="flex items-center gap-1">
                              {active && <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-green)]" />}
                              <span className={active ? "text-[var(--color-green)]" : "text-[var(--color-text-muted)]"}>
                                {ex.status}
                              </span>
                            </span>
                          </td>
                          <td className="px-2 py-1 text-[var(--color-text-muted)]">
                            {ex.close_type || "—"}
                          </td>
                          <td className={`px-2 py-1 text-right font-mono ${ex.pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                            {formatPnl(ex.pnl)}
                          </td>
                          <td className={`px-2 py-1 text-right font-mono ${ex.net_pnl_pct >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                            {formatPct(ex.net_pnl_pct)}
                          </td>
                          <td className="px-2 py-1 text-right text-[var(--color-text-muted)]">
                            {formatAge(ex.timestamp)}
                          </td>
                          <td className="px-2 py-1 text-center">
                            {active && (
                              <button
                                onClick={() => handleStop(ex.id)}
                                disabled={stopping}
                                className="rounded p-0.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/20 hover:text-[var(--color-red)] disabled:opacity-50"
                                title="Stop executor"
                              >
                                {stopping ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <Square className="h-3 w-3" />
                                )}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            )}

            {tab === "positions" && (
              <div className="space-y-1 p-2">
                {positions.length === 0 ? (
                  <p className="py-3 text-center text-[var(--color-text-muted)]">
                    No positions for this pair
                  </p>
                ) : (
                  positions.map((pos, i) => {
                    const side = pos.position_side?.toLowerCase();
                    const isBuy = side === "long" || side === "buy";
                    const isFlat = side === "flat";
                    return (
                      <div
                        key={`${pos.connector_name}-${pos.trading_pair}-${pos.position_side}-${i}`}
                        className="rounded border border-[var(--color-border)]/50 bg-[var(--color-bg)] px-2.5 py-1.5"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className={`rounded px-1 py-0.5 text-[9px] font-bold ${
                              isFlat
                                ? "bg-[var(--color-text-muted)]/20 text-[var(--color-text-muted)]"
                                : isBuy
                                  ? "bg-[var(--color-green)]/20 text-[var(--color-green)]"
                                  : "bg-[var(--color-red)]/20 text-[var(--color-red)]"
                            }`}>
                              {pos.position_side?.toUpperCase() ?? "—"}
                            </span>
                            <div className="text-[11px]">
                              <span className="text-[var(--color-text)]">{Math.abs(pos.amount).toFixed(4)}</span>
                              <span className="ml-1.5 text-[var(--color-text-muted)]">@ {formatPrice(pos.entry_price)}</span>
                            </div>
                          </div>
                          <div className="flex items-center gap-3 text-[11px]">
                            {!isFlat && (
                              <span className="text-[var(--color-text-muted)]">
                                Now: {formatPrice(pos.current_price)}
                              </span>
                            )}
                            {pos.leverage > 1 && (
                              <span className="text-[10px] text-[var(--color-text-muted)]">{pos.leverage}x</span>
                            )}
                            <span className={`font-mono font-medium ${pos.unrealized_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                              {formatPnl(pos.unrealized_pnl)}
                            </span>
                          </div>
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-[var(--color-text-muted)]">
                          <span>
                            Realized: <span className={`font-mono ${pos.realized_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                              {formatPnl(pos.realized_pnl)}
                            </span>
                          </span>
                          <span>
                            Fees: <span className="font-mono">${Math.abs(pos.cum_fees).toFixed(2)}</span>
                          </span>
                          {pos.executor_count > 0 && (
                            <span>{pos.executor_count} executor{pos.executor_count !== 1 ? "s" : ""}</span>
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {/* Stop confirmation */}
      {confirmStopId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-xl">
            <p className="text-sm text-[var(--color-text)]">
              Stop executor <span className="font-mono text-[var(--color-text-muted)]">{confirmStopId.slice(0, 8)}</span>?
            </p>
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => setConfirmStopId(null)}
                className="rounded px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                Cancel
              </button>
              <button
                onClick={() => stopMutation.mutate(confirmStopId)}
                className="rounded bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:brightness-110"
              >
                Stop
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
