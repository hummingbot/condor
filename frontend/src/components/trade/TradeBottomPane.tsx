import { useState, useEffect, useCallback, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronUp, ChevronDown, Loader2, Square, Trash2, Wallet, Clock } from "lucide-react";

import { api, type ExecutorInfo, type ConsolidatedPosition } from "@/lib/api";
import { useServer } from "@/hooks/useServer";
import {
  formatPnl,
  formatPct,
  formatVolume,
  formatAge,
  formatUsd,
  pnlColor,
  isExecutorActive,
} from "@/pages/Executors";

interface TradeBottomPaneProps {
  executors: ExecutorInfo[];
  positions: ConsolidatedPosition[];
  isLoadingPositions: boolean;
  connector: string;
  pair: string;
  isSpot: boolean;
  selectedExecutorId?: string | null;
  onExecutorSelect?: (executor: ExecutorInfo | null) => void;
}

const STORAGE_KEY = "condor_trade_bottom_pane";

function formatPrice(price: number): string {
  if (price === 0) return "—";
  if (Math.abs(price) >= 1000) return price.toFixed(2);
  if (Math.abs(price) >= 1) return price.toFixed(4);
  return price.toPrecision(6);
}

const STRATEGY_LABELS: Record<string, string> = {
  LIMIT: "Limit",
  LIMIT_CHASER: "Chaser",
  MARKET: "Market",
  TWAP: "TWAP",
};

function executorTypeLabel(ex: ExecutorInfo): string {
  const strategy = String(ex.config?.execution_strategy ?? "").toUpperCase();
  if (strategy && STRATEGY_LABELS[strategy]) return STRATEGY_LABELS[strategy];
  return ex.type ? ex.type.charAt(0).toUpperCase() + ex.type.slice(1) : "—";
}

function formatBalance(val: number): string {
  if (val >= 1_000_000) return (val / 1_000_000).toFixed(2) + "M";
  if (val >= 10_000) return (val / 1_000).toFixed(1) + "K";
  if (val >= 1) return val.toFixed(4);
  return val.toPrecision(4);
}

function getEntryPrice(ex: ExecutorInfo): number {
  const ci = ex.custom_info || {};
  return (
    Number(ci.current_position_average_price) ||
    ex.entry_price ||
    Number(ex.config?.start_price) ||
    0
  );
}

function getExitPrice(ex: ExecutorInfo): number {
  const ci = ex.custom_info || {};
  return Number(ci.close_price) || ex.current_price || 0;
}

// Hover tooltip component
function ExecutorTooltip({
  executor,
  anchorRect,
  containerRect,
}: {
  executor: ExecutorInfo;
  anchorRect: DOMRect;
  containerRect: DOMRect;
}) {
  const entry = getEntryPrice(executor);
  const exit = getExitPrice(executor);
  const config = executor.config || {};

  const active = isExecutorActive(executor.status);

  // Position tooltip above the row, centered
  const top = anchorRect.top - containerRect.top - 8;
  const left = Math.min(
    Math.max(anchorRect.left - containerRect.left + anchorRect.width / 2 - 120, 8),
    containerRect.width - 260,
  );

  const slPct = Number(config.stop_loss);
  const tpPct = Number(config.take_profit);
  const leverage = Number(config.leverage);
  const amount = Number(config.total_amount_quote) || Number(config.amount);

  return (
    <div
      className="absolute z-50 pointer-events-none"
      style={{ top, left, transform: "translateY(-100%)" }}
    >
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm shadow-xl p-3 w-[250px] text-[11px]">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono font-semibold text-[var(--color-text)]">
            {executor.id.slice(0, 10)}…
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${
              active
                ? "bg-[var(--color-green)]/20 text-[var(--color-green)]"
                : "bg-[var(--color-text-muted)]/20 text-[var(--color-text-muted)]"
            }`}
          >
            {executor.status}
          </span>
        </div>

        {/* Price row */}
        {(entry > 0 || exit > 0) && (
          <div className="flex items-center gap-2 mb-1.5 text-[var(--color-text-muted)]">
            {entry > 0 && (
              <span>
                Entry: <span className="text-[var(--color-text)] font-mono">{formatPrice(entry)}</span>
              </span>
            )}
            {entry > 0 && exit > 0 && exit !== entry && <span>→</span>}
            {exit > 0 && exit !== entry && (
              <span>
                {active ? "Now" : "Close"}:{" "}
                <span className="text-[var(--color-text)] font-mono">{formatPrice(exit)}</span>
              </span>
            )}
          </div>
        )}

        {/* Config details */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
          {amount > 0 && (
            <div>
              <span className="text-[var(--color-text-muted)]">Amount: </span>
              <span className="font-mono">{formatUsd(amount)}</span>
            </div>
          )}
          {leverage > 1 && (
            <div>
              <span className="text-[var(--color-text-muted)]">Leverage: </span>
              <span className="font-mono">{leverage}x</span>
            </div>
          )}
          {slPct > 0 && slPct !== -1 && (
            <div>
              <span className="text-[var(--color-text-muted)]">SL: </span>
              <span className="font-mono text-[var(--color-red)]">{(slPct * 100).toFixed(2)}%</span>
            </div>
          )}
          {tpPct > 0 && tpPct !== -1 && (
            <div>
              <span className="text-[var(--color-text-muted)]">TP: </span>
              <span className="font-mono text-[var(--color-green)]">{(tpPct * 100).toFixed(2)}%</span>
            </div>
          )}
          {executor.type === "grid" && !!config.start_price && (
            <div className="col-span-2">
              <span className="text-[var(--color-text-muted)]">Range: </span>
              <span className="font-mono">
                {formatPrice(Number(config.start_price as number))} – {formatPrice(Number(config.end_price as number))}
              </span>
            </div>
          )}
          {executor.close_type && !active && (
            <div className="col-span-2">
              <span className="text-[var(--color-text-muted)]">Close: </span>
              <span>{executor.close_type.replace(/_/g, " ")}</span>
            </div>
          )}
        </div>

        {/* Arrow */}
        <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-full">
          <div className="w-0 h-0 border-l-[6px] border-r-[6px] border-t-[6px] border-l-transparent border-r-transparent border-t-[var(--color-border)]" />
        </div>
      </div>
    </div>
  );
}

export function TradeBottomPane({
  executors,
  positions,
  isLoadingPositions,
  connector,
  pair,
  isSpot,
  selectedExecutorId,
  onExecutorSelect,
}: TradeBottomPaneProps) {
  const { server } = useServer();
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) !== "0"; } catch { return true; }
  });
  const [tab, setTab] = useState<"executors" | "positions">("executors");
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [confirmStopId, setConfirmStopId] = useState<string | null>(null);
  const [confirmClearPos, setConfirmClearPos] = useState<ConsolidatedPosition | null>(null);
  const [clearingPositions, setClearingPositions] = useState<Set<string>>(new Set());
  const [hoveredExecutor, setHoveredExecutor] = useState<{ executor: ExecutorInfo; rect: DOMRect } | null>(null);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0"); } catch { /* ok */ }
  }, [expanded]);

  // Fetch balances for the active connector
  const { data: portfolio } = useQuery({
    queryKey: ["portfolio", server],
    queryFn: () => api.getPortfolio(server!),
    enabled: !!server,
    refetchInterval: 30_000,
  });

  // Extract base/quote tokens from pair (e.g. "BTC-USDT" -> ["BTC", "USDT"])
  const [baseToken, quoteToken] = pair.split("-");

  // Find balances for the active connector
  const connectorBalances = portfolio?.connectors?.find(
    (c) => c.connector === connector,
  )?.balances;

  const baseBalance = connectorBalances?.find((b) => b.token === baseToken);
  const quoteBalance = connectorBalances?.find((b) => b.token === quoteToken);

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

  const clearPositionMutation = useMutation({
    mutationFn: (pos: ConsolidatedPosition) => {
      const key = `${pos.connector_name}:${pos.trading_pair}:${pos.position_side}`;
      setClearingPositions((prev) => new Set([...prev, key]));
      return api.clearPositionHeld(
        server!,
        pos.connector_name,
        pos.trading_pair,
        pos.controller_id || undefined,
      );
    },
    onSettled: (_data, _error, pos) => {
      const key = `${pos.connector_name}:${pos.trading_pair}:${pos.position_side}`;
      setClearingPositions((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
      setConfirmClearPos(null);
      queryClient.invalidateQueries({ queryKey: ["positions", server] });
      queryClient.invalidateQueries({ queryKey: ["consolidated-positions", server] });
    },
  });

  const handleStop = useCallback((id: string) => {
    setConfirmStopId(id);
  }, []);

  const handleRowClick = useCallback(
    (ex: ExecutorInfo) => {
      if (!onExecutorSelect) return;
      // Toggle: click same executor again to deselect
      if (selectedExecutorId === ex.id) {
        onExecutorSelect(null);
      } else {
        onExecutorSelect(ex);
      }
    },
    [onExecutorSelect, selectedExecutorId],
  );

  const handleRowMouseEnter = useCallback(
    (ex: ExecutorInfo, e: React.MouseEvent<HTMLTableRowElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      setHoveredExecutor({ executor: ex, rect });
    },
    [],
  );

  const handleRowMouseLeave = useCallback(() => {
    setHoveredExecutor(null);
  }, []);

  const activeExecutors = executors.filter((e) => isExecutorActive(e.status));
  const totalPnl = executors.reduce((sum, e) => sum + (e.pnl || 0), 0);
  const totalVolume = executors.reduce((sum, e) => sum + (e.volume || 0), 0);

  const sortedExecutors = [...executors].sort((a, b) => b.timestamp - a.timestamp);

  return (
    <div ref={containerRef} className="border-t border-[var(--color-border)] bg-[var(--color-surface)] h-full flex flex-col relative">
      {/* Toggle handle + balance bar */}
      <button
        onClick={() => setExpanded((p) => !p)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-[10px] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] transition-colors shrink-0"
      >
        <div className="flex items-center gap-3">
          <span>
            {executors.length} executor{executors.length !== 1 ? "s" : ""}
            {activeExecutors.length > 0 && (
              <span className="text-[var(--color-green)]"> ({activeExecutors.length} active)</span>
            )}
            {" · "}
            {positions.length} position{positions.length !== 1 ? "s" : ""}
          </span>
          {executors.length > 0 && (
            <>
              <span className="text-[var(--color-border)]">|</span>
              <span style={{ color: pnlColor(totalPnl) }} className="font-mono font-medium">
                {formatPnl(totalPnl)}
              </span>
              <span className="font-mono">{formatVolume(totalVolume)} vol</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Balance indicators */}
          <div className="flex items-center gap-2">
            <Wallet className="h-3 w-3 text-[var(--color-text-muted)]" />
            {isSpot && baseBalance && (
              <span className="font-mono">
                {formatBalance(baseBalance.available)} <span className="text-[var(--color-text-muted)]">{baseToken}</span>
              </span>
            )}
            {quoteBalance && (
              <span className="font-mono">
                {formatBalance(quoteBalance.available)} <span className="text-[var(--color-text-muted)]">{quoteToken}</span>
              </span>
            )}
            {!baseBalance && !quoteBalance && (
              <span className="text-[var(--color-text-muted)]">—</span>
            )}
          </div>
          <span className="text-[var(--color-border)]">|</span>
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />}
        </div>
      </button>

      {expanded && (
        <div className="flex flex-col min-h-0 flex-1">
          {/* Tabs */}
          <div className="flex border-t border-[var(--color-border)] shrink-0">
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
          <div className="overflow-y-auto flex-1">
            {tab === "executors" && (
              <table className="w-full text-[11px]">
                <thead className="sticky top-0 bg-[var(--color-surface)] z-[1]">
                  <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    <th className="px-3 py-1.5 font-medium">ID</th>
                    <th className="px-3 py-1.5 font-medium">Type</th>
                    <th className="px-3 py-1.5 font-medium">Side</th>
                    <th className="px-3 py-1.5 font-medium text-right">Entry</th>
                    <th className="px-3 py-1.5 font-medium text-right">Price</th>
                    <th className="px-3 py-1.5 font-medium text-right">PnL</th>
                    <th className="px-3 py-1.5 font-medium text-right">PnL%</th>
                    <th className="px-3 py-1.5 font-medium text-right">Volume</th>
                    <th className="px-3 py-1.5 font-medium text-right">Age</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {sortedExecutors.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="px-3 py-3 text-center text-[var(--color-text-muted)]">
                        No executors for this pair
                      </td>
                    </tr>
                  ) : (
                    sortedExecutors.map((ex) => {
                      const active = isExecutorActive(ex.status);
                      const stopping = stoppingIds.has(ex.id);
                      const side = ex.side?.toUpperCase();
                      const isBuy = side === "BUY" || side === "1";
                      const borderColor = ex.pnl >= 0 ? "var(--color-green)" : "var(--color-red)";
                      const isSelected = selectedExecutorId === ex.id;
                      const entry = getEntryPrice(ex);
                      const exit = getExitPrice(ex);
                      return (
                        <tr
                          key={ex.id}
                          className={`border-b border-[var(--color-border)]/30 transition-colors ${
                            isSelected
                              ? "bg-[var(--color-primary)]/10 hover:bg-[var(--color-primary)]/15"
                              : "hover:bg-[var(--color-surface-hover)]/50"
                          } ${onExecutorSelect ? "cursor-pointer" : ""}`}
                          style={{
                            borderLeft: isSelected
                              ? "3px solid var(--color-primary)"
                              : `3px solid ${borderColor}`,
                          }}
                          onClick={() => handleRowClick(ex)}
                          onMouseEnter={(e) => handleRowMouseEnter(ex, e)}
                          onMouseLeave={handleRowMouseLeave}
                        >
                          <td className="px-3 py-1.5 font-mono text-[var(--color-text-muted)]" title={ex.id}>
                            {ex.id.slice(0, 8)}
                          </td>
                          <td className="px-3 py-1.5">
                            <span className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 text-[10px] font-medium border border-[var(--color-border)]/50">
                              {executorTypeLabel(ex)}
                            </span>
                          </td>
                          <td className="px-3 py-1.5">
                            <span
                              className="text-[10px] font-semibold uppercase"
                              style={{ color: isBuy ? "var(--color-green)" : "var(--color-red)" }}
                            >
                              {side}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
                            {entry > 0 ? formatPrice(entry) : "—"}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
                            {exit > 0 && exit !== entry ? formatPrice(exit) : "—"}
                          </td>
                          <td
                            className="px-3 py-1.5 text-right font-mono font-medium tabular-nums"
                            style={{ color: pnlColor(ex.pnl) }}
                          >
                            {formatPnl(ex.pnl)}
                          </td>
                          <td
                            className="px-3 py-1.5 text-right font-mono tabular-nums"
                            style={{ color: ex.net_pnl_pct ? pnlColor(ex.net_pnl_pct) : undefined }}
                          >
                            {formatPct(ex.net_pnl_pct)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
                            {formatVolume(ex.volume)}
                          </td>
                          <td className="px-3 py-1.5 text-right text-[var(--color-text-muted)]">
                            <span className="inline-flex items-center gap-1">
                              <Clock className="h-2.5 w-2.5" />
                              {formatAge(ex.timestamp)}
                            </span>
                          </td>
                          <td className="px-2 py-1.5 text-center" onClick={(e) => e.stopPropagation()}>
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
                    const posKey = `${pos.connector_name}:${pos.trading_pair}:${pos.position_side}`;
                    const isClearing = clearingPositions.has(posKey);
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
                              <span className="ml-1 text-[var(--color-text-muted)]">
                                (${(pos.notional_value ?? Math.abs(pos.amount) * pos.entry_price).toFixed(2)})
                              </span>
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
                            <button
                              onClick={() => setConfirmClearPos(pos)}
                              disabled={isClearing}
                              className="rounded p-0.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/20 hover:text-[var(--color-red)] disabled:opacity-50"
                              title="Clear position"
                            >
                              {isClearing ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <Trash2 className="h-3 w-3" />
                              )}
                            </button>
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

      {/* Hover tooltip */}
      {hoveredExecutor && containerRef.current && (
        <ExecutorTooltip
          executor={hoveredExecutor.executor}
          anchorRect={hoveredExecutor.rect}
          containerRect={containerRef.current.getBoundingClientRect()}
        />
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

      {/* Clear position confirmation */}
      {confirmClearPos && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-xl">
            <p className="text-sm text-[var(--color-text)]">
              Clear <span className="font-medium">{confirmClearPos.position_side?.toUpperCase()}</span> position on{" "}
              <span className="font-mono text-[var(--color-text-muted)]">{confirmClearPos.trading_pair}</span>?
            </p>
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              This resets the tracked position state. It does not close the position on the exchange.
            </p>
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => setConfirmClearPos(null)}
                className="rounded px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                Cancel
              </button>
              <button
                onClick={() => clearPositionMutation.mutate(confirmClearPos)}
                className="rounded bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:brightness-110"
              >
                Clear
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
