import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Anchor,
  Activity,
  ArrowDownUp,
  Bot,
  ChevronDown,
  ChevronUp,
  Circle,
  Grid3X3,
  List,
} from "lucide-react";
import { useMemo, useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api, type ConsolidatedPosition } from "@/lib/api";

// ── Formatters ──

function formatUsd(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return val.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function formatPnl(val: number) {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatUsd(val);
}

function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

function formatPrice(val: number): string {
  if (!val) return "\u2014";
  if (val >= 1000) return val.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (val >= 1) return val.toFixed(4);
  return val.toPrecision(4);
}

function normalizeSide(raw: string): string {
  const s = raw.toUpperCase();
  if (s === "1") return "LONG";
  if (s === "2") return "SHORT";
  return s;
}

function sideColor(side: string) {
  return side === "LONG" || side === "BUY" ? "var(--color-green)" : "var(--color-red)";
}

// ── Types ──

type FilterTab = "all" | "executor" | "bot";
type ViewMode = "cards" | "table";
type SortKey = "trading_pair" | "side" | "unrealized_pnl" | "amount" | "entry_price" | "current_price" | "source";
type SortDir = "asc" | "desc";

// ── Component ──

export function Positions() {
  const { server } = useServer();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<FilterTab>("all");
  const [viewMode, setViewMode] = useState<ViewMode>("cards");
  const [clearError, setClearError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("unrealized_pnl");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const { data, isLoading } = useQuery({
    queryKey: ["consolidated-positions", server],
    queryFn: () => api.getConsolidatedPositions(server!),
    enabled: !!server,
    refetchInterval: 10000,
  });

  const allPositions = useMemo(() => {
    if (!data) return [];
    return [...data.executor_positions, ...data.bot_positions];
  }, [data]);

  const filteredPositions = useMemo(() => {
    let result = allPositions;
    if (filter !== "all") {
      result = result.filter((p) => p.source === filter);
    }
    // Sort
    const sorted = [...result].sort((a, b) => {
      let av: string | number, bv: string | number;
      switch (sortKey) {
        case "trading_pair": av = a.trading_pair; bv = b.trading_pair; break;
        case "side": av = normalizeSide(a.position_side); bv = normalizeSide(b.position_side); break;
        case "unrealized_pnl": av = a.unrealized_pnl; bv = b.unrealized_pnl; break;
        case "amount": av = Math.abs(a.amount); bv = Math.abs(b.amount); break;
        case "entry_price": av = a.entry_price; bv = b.entry_price; break;
        case "current_price": av = a.current_price; bv = b.current_price; break;
        case "source": av = a.source; bv = b.source; break;
        default: av = 0; bv = 0;
      }
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortDir === "asc" ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return sorted;
  }, [allPositions, filter, sortKey, sortDir]);

  const counts = useMemo(() => ({
    all: allPositions.length,
    executor: data?.executor_positions.length ?? 0,
    bot: data?.bot_positions.length ?? 0,
  }), [allPositions, data]);

  const totalPnl = useMemo(
    () => filteredPositions.reduce((sum, p) => sum + p.unrealized_pnl, 0),
    [filteredPositions],
  );

  const clearPositionMutation = useMutation({
    mutationFn: (pos: ConsolidatedPosition) =>
      api.clearPositionHeld(server!, pos.connector_name, pos.trading_pair, pos.controller_id || undefined),
    onMutate: () => setClearError(null),
    onError: (err: Error) => {
      setClearError(err.message);
      setTimeout(() => setClearError(null), 5000);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["consolidated-positions", server] });
      queryClient.invalidateQueries({ queryKey: ["positions-held", server] });
    },
  });

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const SortIcon = ({ col }: { col: SortKey }) => {
    if (col !== sortKey) return <ArrowDownUp className="h-3 w-3 opacity-30" />;
    return sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />;
  };

  if (!server)
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Anchor className="h-5 w-5 text-amber-500" />
          <h2 className="text-xl font-bold">Positions</h2>
          {allPositions.length > 0 && (
            <span className="text-sm text-[var(--color-text-muted)]">
              ({allPositions.length})
            </span>
          )}
        </div>
        {/* View toggle */}
        <div className="flex rounded-md border border-[var(--color-border)] overflow-hidden">
          <button
            onClick={() => setViewMode("cards")}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              viewMode === "cards"
                ? "bg-[var(--color-primary)] text-white"
                : "bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
            }`}
          >
            <Grid3X3 className="h-3.5 w-3.5" />
            Cards
          </button>
          <button
            onClick={() => setViewMode("table")}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              viewMode === "table"
                ? "bg-[var(--color-primary)] text-white"
                : "bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
            }`}
          >
            <List className="h-3.5 w-3.5" />
            Table
          </button>
        </div>
      </div>

      {/* PnL summary + filter tabs row */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        {/* Filter tabs */}
        <div className="flex rounded-md border border-[var(--color-border)] overflow-hidden w-fit">
          {(["all", "executor", "bot"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setFilter(tab)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
                filter === tab
                  ? "bg-[var(--color-primary)] text-white"
                  : "bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              {tab === "executor" && <Activity className="h-3.5 w-3.5" />}
              {tab === "bot" && <Bot className="h-3.5 w-3.5" />}
              {tab === "all" ? "All" : tab === "executor" ? "Executor" : "Bot"}
              <span className="opacity-60">({counts[tab]})</span>
            </button>
          ))}
        </div>

        {/* PnL summary */}
        {filteredPositions.length > 0 && (
          <div className="flex items-center gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2">
            <div className="text-xs text-[var(--color-text-muted)]">Total Unrealized PnL</div>
            <div className="text-sm font-semibold tabular-nums" style={{ color: pnlColor(totalPnl) }}>
              {formatPnl(totalPnl)}
            </div>
          </div>
        )}
      </div>

      {/* Error banner */}
      {clearError && (
        <div className="rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          Failed to clear position: {clearError}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
          <Circle className="h-5 w-5 animate-spin mr-2" />
          Loading positions...
        </div>
      )}

      {/* Empty state */}
      {!isLoading && filteredPositions.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)]">
          <Anchor className="h-8 w-8 mb-3 opacity-40" />
          <p className="text-sm">No positions held</p>
          {filter !== "all" && (
            <p className="text-xs mt-1">
              Try switching to{" "}
              <button
                onClick={() => setFilter("all")}
                className="underline hover:text-[var(--color-text)]"
              >
                All
              </button>
            </p>
          )}
        </div>
      )}

      {/* ── Card view ── */}
      {viewMode === "cards" && filteredPositions.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {filteredPositions.map((pos) => {
            const side = normalizeSide(pos.position_side);
            const key = `${pos.source}:${pos.connector_name}:${pos.trading_pair}:${pos.controller_id}`;

            return (
              <div
                key={key}
                className={`rounded-lg border bg-[var(--color-surface)] p-4 transition-colors ${
                  pos.source === "executor"
                    ? "border-amber-500/30 hover:border-amber-500/60"
                    : "border-blue-500/30 hover:border-blue-500/60"
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <div
                      className={`h-2 w-2 rounded-full ${
                        pos.source === "executor" ? "bg-amber-500" : "bg-blue-500"
                      }`}
                    />
                    <span className="text-sm font-medium truncate">
                      {pos.trading_pair}
                    </span>
                    <span className="text-xs font-semibold uppercase" style={{ color: sideColor(side) }}>
                      {side}
                    </span>
                  </div>
                  {pos.source === "executor" && (
                    <button
                      onClick={() => clearPositionMutation.mutate(pos)}
                      disabled={clearPositionMutation.isPending}
                      className="rounded px-2 py-0.5 text-[10px] font-medium border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-red)]/50 hover:text-[var(--color-red)] transition-colors disabled:opacity-50"
                      title="Clear held position (mark as externally closed)"
                    >
                      Clear
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <div>
                    <div className="text-[var(--color-text-muted)] text-[10px] uppercase">Unrealized PnL</div>
                    <div className="font-medium tabular-nums" style={{ color: pnlColor(pos.unrealized_pnl) }}>
                      {formatPnl(pos.unrealized_pnl)}
                    </div>
                  </div>
                  {pos.amount !== 0 && (
                    <div>
                      <div className="text-[var(--color-text-muted)] text-[10px] uppercase">Size</div>
                      <div className="font-medium tabular-nums">{Math.abs(pos.amount).toPrecision(4)}</div>
                    </div>
                  )}
                  {pos.entry_price > 0 && (
                    <div>
                      <div className="text-[var(--color-text-muted)] text-[10px] uppercase">Entry</div>
                      <div className="font-medium tabular-nums">{formatPrice(pos.entry_price)}</div>
                    </div>
                  )}
                  {pos.current_price > 0 && (
                    <div>
                      <div className="text-[var(--color-text-muted)] text-[10px] uppercase">Current</div>
                      <div className="font-medium tabular-nums">{formatPrice(pos.current_price)}</div>
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 mt-2 text-xs text-[var(--color-text-muted)]">
                  <span>{pos.connector_name}</span>
                  {pos.leverage > 1 && <span>{pos.leverage}x</span>}
                  {pos.controller_id && <span className="truncate">{pos.controller_id}</span>}
                  <span className="ml-auto text-[10px] opacity-70">{pos.source_name}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Table view ── */}
      {viewMode === "table" && filteredPositions.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                {([
                  ["source", "Source"],
                  ["trading_pair", "Pair"],
                  ["side", "Side"],
                  ["unrealized_pnl", "Unrealized PnL"],
                  ["amount", "Size"],
                  ["entry_price", "Entry"],
                  ["current_price", "Current"],
                ] as [SortKey, string][]).map(([key, label]) => (
                  <th
                    key={key}
                    onClick={() => handleSort(key)}
                    className="cursor-pointer select-none whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                  >
                    <div className="flex items-center gap-1">
                      {label}
                      <SortIcon col={key} />
                    </div>
                  </th>
                ))}
                <th className="px-3 py-2 text-left text-xs font-medium text-[var(--color-text-muted)]">
                  Connector
                </th>
                <th className="px-3 py-2 text-right text-xs font-medium text-[var(--color-text-muted)]">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredPositions.map((pos) => {
                const side = normalizeSide(pos.position_side);
                const key = `${pos.source}:${pos.connector_name}:${pos.trading_pair}:${pos.controller_id}`;
                return (
                  <tr
                    key={key}
                    className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-surface-hover)] transition-colors"
                  >
                    <td className="whitespace-nowrap px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <div className={`h-2 w-2 rounded-full ${pos.source === "executor" ? "bg-amber-500" : "bg-blue-500"}`} />
                        <span className="text-xs">{pos.source_name}</span>
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-medium">{pos.trading_pair}</td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <span className="text-xs font-semibold" style={{ color: sideColor(side) }}>
                        {side}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums font-medium" style={{ color: pnlColor(pos.unrealized_pnl) }}>
                      {formatPnl(pos.unrealized_pnl)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                      {pos.amount !== 0 ? Math.abs(pos.amount).toPrecision(4) : "\u2014"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                      {formatPrice(pos.entry_price)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                      {formatPrice(pos.current_price)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-[var(--color-text-muted)]">
                      <div className="flex items-center gap-1.5">
                        <span>{pos.connector_name}</span>
                        {pos.leverage > 1 && <span className="text-xs">{pos.leverage}x</span>}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      {pos.source === "executor" ? (
                        <button
                          onClick={() => clearPositionMutation.mutate(pos)}
                          disabled={clearPositionMutation.isPending}
                          className="rounded px-2 py-0.5 text-[10px] font-medium border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-red)]/50 hover:text-[var(--color-red)] transition-colors disabled:opacity-50"
                        >
                          Clear
                        </button>
                      ) : (
                        <span className="text-[10px] text-[var(--color-text-muted)] opacity-50">\u2014</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            {/* Table footer with total PnL */}
            <tfoot>
              <tr className="border-t border-[var(--color-border)] bg-[var(--color-surface)]">
                <td colSpan={3} className="px-3 py-2 text-xs font-medium text-[var(--color-text-muted)]">
                  Total ({filteredPositions.length} position{filteredPositions.length !== 1 ? "s" : ""})
                </td>
                <td className="px-3 py-2 tabular-nums font-semibold" style={{ color: pnlColor(totalPnl) }}>
                  {formatPnl(totalPnl)}
                </td>
                <td colSpan={5} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
